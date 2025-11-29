import copy
import yaml
import hashlib
import os
from pathlib import Path
from typing import Dict, Any, List, Optional
from automation.clients.dify_console_client import DifyConsoleClient
from automation.services.diff_service import DiffService
from automation.services.workflow_runner import WorkflowRunner
from automation.services.security_service import SecurityService
from automation.services.trace_service import TraceService
from automation.infra.logger import logger


class WorkflowService:
    def __init__(self, client: DifyConsoleClient):
        self.client = client
        self.diff_service = DiffService()
        self.trace_service = TraceService()
        self.runner = WorkflowRunner(client, trace_service=self.trace_service)
        self.security_service = SecurityService()

    def create_and_run_mvp(self, prompt: str) -> Dict[str, Any]:
        # 1. Load DSL from file
        with open("automation/dsl/mvp_workflow.yml", "r") as f:
            dsl_content = f.read()

        dsl_data = yaml.safe_load(dsl_content)
        app_mode = dsl_data.get("app", {}).get("mode", "workflow")

        # Security Check
        violations = self.check_security(dsl_data)
        if violations:
            raise ValueError(f"Security violations detected: {violations}")

        # 2. Import App (Create new)
        # For MVP, we create a new app every time.
        import_res = self.client.import_app(
            mode="yaml-content",
            yaml_content=dsl_content
        )

        app_id = import_res.get("app_id")
        if not app_id:
            raise Exception(f"Failed to import app: {import_res}")

        trace_id = self.trace_service.new_trace_id()

        # 3. Run Workflow using Runner
        execution = self.runner.run(
            app_id=app_id,
            inputs={"query": prompt},
            mode=app_mode,
            trace_id=trace_id
        )

        return {
            "app_id": app_id,
            "outputs": execution.outputs,
            "events_count": len(execution.node_executions) * 2,  # approx
            "status": execution.status,
            "error": execution.error,
            "trace_id": trace_id
        }

    def create_and_run(
        self,
        dsl_content: str,
        inputs: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Import a DSL as a new app and run it.
        """
        dsl_data = yaml.safe_load(dsl_content)
        app_mode = dsl_data.get("app", {}).get("mode", "workflow")

        # Security Check
        violations = self.check_security(dsl_data)
        if violations:
            raise ValueError(f"Security violations detected: {violations}")

        # Import
        import_res = self.client.import_app(
            mode="yaml-content",
            yaml_content=dsl_content
        )
        app_id = import_res.get("app_id")
        if not app_id:
            raise Exception(f"Failed to import app: {import_res}")

        trace_id = self.trace_service.new_trace_id()

        # Run
        execution = self.runner.run(
            app_id=app_id,
            inputs=inputs,
            mode=app_mode,
            trace_id=trace_id
        )

        return {
            "app_id": app_id,
            "outputs": execution.outputs,
            "status": execution.status,
            "error": execution.error,
            "trace_id": trace_id
        }

    def check_dependencies(self, dsl_data: Dict[str, Any]) -> List[str]:
        """
        Check for missing plugins or dependencies.
        """
        missing = []
        nodes = dsl_data.get("workflow", {}).get("graph", {}).get("nodes", [])
        for node in nodes:
            node_data = node.get("data", {})
            node_type = node_data.get("type")
            
            # Example check: if it's a tool node, check provider
            if node_type == "tool":
                provider_id = node_data.get("provider_id")
                if provider_id and not self._is_plugin_installed(provider_id):
                    missing.append(f"Plugin: {provider_id}")
        
        return missing

    def _is_plugin_installed(self, provider_id: str) -> bool:
        # Mock implementation for now
        # In real implementation, this would query the console API
        return True

    def diff(self, local_dsl_path: str, app_id: str) -> Dict[str, Any]:
        """
        Compare local DSL file with remote app DSL.
        """
        # 1. Read local
        with open(local_dsl_path, "r") as f:
            local_content = f.read()
        local_dsl = yaml.safe_load(local_content)

        # 2. Fetch remote
        remote_content = self.client.export_app(app_id)
        remote_dsl = yaml.safe_load(remote_content)

        # 3. Diff
        return self.diff_service.diff(local_dsl, remote_dsl)

    def dry_run(self, dsl_input: Any) -> Dict[str, Any]:
        """
        Validate DSL without importing it.
        Args:
            dsl_input: File path (str) or DSL dictionary (dict)
        """
        dsl_data = {}
        try:
            if isinstance(dsl_input, str):
                with open(dsl_input, "r") as f:
                    dsl_content = f.read()
                dsl_data = yaml.safe_load(dsl_content)
            elif isinstance(dsl_input, dict):
                dsl_data = dsl_input
            else:
                raise ValueError(
                    "Invalid input type. Expected file path or dict."
                )
        except Exception as e:
            return {
                "valid": False,
                "errors": [f"Failed to read or parse input: {str(e)}"],
                "missing_plugins": [],
                "node_count": 0
            }

        report: Dict[str, Any] = {
            "valid": True,
            "errors": [],
            "missing_plugins": [],
            "security_violations": [],
            "node_count": 0
        }

        # 1. Structure Validation
        try:
            if "workflow" not in dsl_data:
                report["valid"] = False
                report["errors"].append("Missing 'workflow' key")
            else:
                nodes = dsl_data["workflow"].get("graph", {}).get("nodes", [])
                report["node_count"] = len(nodes)
                
                # Check for start/end
                node_types = [n.get("data", {}).get("type") for n in nodes]
                if "start" not in node_types:
                    report["valid"] = False
                    report["errors"].append("Missing 'start' node")
                if "end" not in node_types:
                    report["valid"] = False
                    report["errors"].append("Missing 'end' node")
            
            # 2. Dependency Check
            missing = self.check_dependencies(dsl_data)
            if missing:
                report["valid"] = False
                report["missing_plugins"] = missing
                report["errors"].append(
                    f"Missing plugins: {', '.join(missing)}"
                )

            # 3. Security Check
            security_violations = self.check_security(dsl_data)
            if security_violations:
                report["valid"] = False
                report["security_violations"] = security_violations
                report["errors"].extend(security_violations)

        except Exception as e:
            report["valid"] = False
            report["errors"].append(f"Validation error: {str(e)}")

        return report

    def run_node(
        self,
        app_id: str,
        node_id: str,
        inputs: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Run a single node in the draft workflow.
        """
        result = self.runner.run_node(app_id, node_id, inputs)
        
        return {
            "node_id": result.node_id,
            "status": result.status,
            "outputs": result.outputs,
            "error": result.error,
            "execution_metadata": result.execution_metadata
        }

    def check_security(self, dsl_data: Dict[str, Any]) -> List[str]:
        """
        Scan code nodes for security violations.
        """
        violations = []
        nodes = dsl_data.get("workflow", {}).get("graph", {}).get("nodes", [])
        for node in nodes:
            node_data = node.get("data", {})
            node_type = node_data.get("type")
            
            if node_type == "code":
                code = node_data.get("code", "")
                node_title = node_data.get("title", "Code Node")
                errors = self.security_service.scan_code(code)
                for err in errors:
                    violations.append(f"[{node_title}] {err}")
        
        return violations

    def pull_app(self, app_id: str, output_path: str) -> None:
        """
        Fetch remote DSL and save to file.
        Also saves a .hash file for conflict detection.
        """
        dsl_content = self.client.export_app(app_id)
        
        with open(output_path, "w") as f:
            f.write(dsl_content)

        # Persist last-synced snapshot for future three-way merges
        with open(f"{output_path}.base", "w") as base_file:
            base_file.write(dsl_content)
            
        # Save hash
        dsl_hash = hashlib.sha256(dsl_content.encode('utf-8')).hexdigest()
        with open(f"{output_path}.hash", "w") as f:
            f.write(dsl_hash)

    def push_app(
        self,
        app_id: str,
        dsl_path: str,
        force: bool = False,
        trace_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Push local DSL to remote app.
        Performs conflict detection unless force=True.
        """
        # 1. Read local DSL
        with open(dsl_path, "r") as f:
            dsl_content = f.read()
        local_hash = hashlib.sha256(
            dsl_content.encode('utf-8')
        ).hexdigest()
            
        trace_id = trace_id or self.trace_service.new_trace_id()

        self.trace_service.record(
            trace_id,
            "push_app",
            "start",
            {"app_id": app_id, "dsl_path": dsl_path, "force": force}
        )

        # 2. Security Check
        dsl_data = yaml.safe_load(dsl_content)
        violations = self.check_security(dsl_data)
        if violations:
            self.trace_service.record(
                trace_id,
                "push_app",
                "blocked",
                {"reason": "security", "details": violations}
            )
            raise ValueError(f"Security violations detected: {violations}")

        remote_content: Optional[str] = None
        remote_hash: Optional[str] = None

        def _ensure_remote(strict: bool) -> None:
            nonlocal remote_content, remote_hash
            if remote_content is not None:
                return
            try:
                remote_content = self.client.export_app(
                    app_id,
                    trace_id=trace_id
                )
                remote_hash = hashlib.sha256(
                    remote_content.encode('utf-8')
                ).hexdigest()
            except Exception as exc:
                if strict:
                    raise
                logger.warning(
                    "Failed to fetch remote DSL for %s: %s",
                    app_id,
                    exc
                )

        # 3. Conflict Detection
        if not force:
            # Check if .hash file exists
            hash_path = f"{dsl_path}.hash"
            if not os.path.exists(hash_path):
                raise ValueError(
                    "No sync history found (missing .hash file). "
                    "Use --force to overwrite remote app, or pull first."
                )
            
            with open(hash_path, "r") as f:
                last_synced_hash = f.read().strip()
                
            # Fetch remote to check if it changed since last sync
            _ensure_remote(strict=True)
            
            if last_synced_hash != remote_hash:
                merge_hint = self._handle_auto_merge(
                    dsl_path,
                    dsl_content,
                    remote_content or "",
                    last_synced_hash
                )
                self.trace_service.record(
                    trace_id,
                    "push_app",
                    "blocked",
                    {
                        "reason": "conflict",
                        "auto_merge": bool(merge_hint)
                    }
                )
                error_message = (
                    "Remote app has changed since last pull. "
                    "Please pull changes first or use --force to overwrite."
                )
                if merge_hint:
                    error_message += f" {merge_hint}"
                raise ValueError(error_message)

        _ensure_remote(strict=False)
        if remote_hash and remote_hash == local_hash:
            with open(f"{dsl_path}.hash", "w") as f:
                f.write(remote_hash)
            self.trace_service.record(
                trace_id,
                "push_app",
                "skipped",
                {
                    "app_id": app_id,
                    "hash": remote_hash,
                    "reason": "no_changes"
                }
            )
            return {
                "status": "skipped",
                "reason": "no_changes",
                "hash": remote_hash
            }

        # 4. Import
        try:
            res = self.client.import_app(
                mode="yaml-content",
                yaml_content=dsl_content,
                app_id=app_id,
                trace_id=trace_id
            )
        except Exception as exc:
            self.trace_service.record(
                trace_id,
                "push_app",
                "error",
                {"error": str(exc)}
            )
            raise

        payload = res.get("data") if isinstance(res, dict) else None
        import_status = (
            (payload or res).get("status")
            if isinstance((payload or res), dict)
            else None
        )
        import_status_lower = (import_status or "").lower()
        if import_status_lower in {"failed", "error"}:
            message = None
            if isinstance(payload, dict):
                message = payload.get("message") or payload.get("error")
            if isinstance(res, dict) and not message:
                message = res.get("message") or res.get("error")
            message = message or "Remote import failed"
            raise ValueError(
                f"Remote import failed with status {import_status_lower}: "
                f"{message}"
            )
        if import_status_lower and import_status_lower not in {
            "success",
            "pending"
        }:
            logger.warning(
                "Import returned unexpected status %s for %s",
                import_status,
                app_id
            )
        
        # 5. Update hash file after successful push
        # Note: We should ideally fetch the new remote content to get the
        # canonical hash, but assuming import is successful and we are
        # the only writer, the local content is now the truth.
        # However, server might reformat.
        # To be safe, we should fetch it again or trust that next pull
        # will handle it.
        # But if we don't update .hash, next push will fail because
        # remote hash (new) != last_synced_hash (old).
        # So we MUST update .hash to match what we expect the remote to be.
        # But we don't know exactly what the server stores (reformatting).
        # So the safest way is to fetch it back.
        # But that adds latency.
        # MVP: Just save the hash of what we pushed?
        # No, if server reformats, next check:
        # last_synced_hash (local content hash) != remote_hash (server content)
        # So we will get a conflict.
        # So we MUST fetch back the content to establish the new baseline.
        
        new_remote_content = self.client.export_app(
            app_id,
            trace_id=trace_id
        )
        new_hash = hashlib.sha256(
            new_remote_content.encode('utf-8')
        ).hexdigest()
        
        with open(f"{dsl_path}.hash", "w") as f:
            f.write(new_hash)

        self.trace_service.record(
            trace_id,
            "push_app",
            "finish",
            {"app_id": app_id, "hash": new_hash}
        )
        
        return res

    def _handle_auto_merge(
        self,
        dsl_path: str,
        local_content: str,
        remote_content: str,
        expected_hash: str
    ) -> Optional[str]:
        base_path = Path(f"{dsl_path}.base")
        if not base_path.exists():
            return None

        try:
            base_content = base_path.read_text(encoding="utf-8")
        except Exception as exc:
            logger.warning(
                "Failed to read base snapshot %s: %s",
                base_path,
                exc
            )
            return None

        base_hash = hashlib.sha256(base_content.encode('utf-8')).hexdigest()
        if base_hash != expected_hash:
            logger.warning(
                "Base snapshot hash mismatch for %s (expected %s)",
                dsl_path,
                expected_hash
            )
            return None

        merged_path, conflicts = self._attempt_auto_merge(
            base_content,
            local_content,
            remote_content,
            dsl_path
        )

        if merged_path:
            return (
                f"Auto-merged draft saved to {merged_path}. "
                "Review the file, then push with --force once satisfied."
            )
        if conflicts:
            logger.info(
                "Auto merge conflicts for %s at %s",
                dsl_path,
                ", ".join(conflicts)
            )
        return None

    def _attempt_auto_merge(
        self,
        base_content: str,
        local_content: str,
        remote_content: str,
        dsl_path: str
    ) -> tuple[Optional[str], List[str]]:
        base_data = self._safe_yaml_dict(base_content)
        local_data = self._safe_yaml_dict(local_content)
        remote_data = self._safe_yaml_dict(remote_content)

        conflicts: List[str] = []
        merged = self._merge_dicts(
            base_data,
            local_data,
            remote_data,
            conflicts,
            []
        )

        if conflicts:
            return None, conflicts

        merged_yaml = yaml.safe_dump(
            merged,
            sort_keys=False,
            allow_unicode=True
        )
        merged_path = Path(f"{dsl_path}.auto-merged.yml")
        merged_path.write_text(merged_yaml, encoding="utf-8")
        return str(merged_path), []

    def _safe_yaml_dict(self, content: str) -> Dict[str, Any]:
        loaded = yaml.safe_load(content) or {}
        if isinstance(loaded, dict):
            return loaded
        return {}

    def _merge_dicts(
        self,
        base: Dict[str, Any],
        local: Dict[str, Any],
        remote: Dict[str, Any],
        conflicts: List[str],
        path: List[str]
    ) -> Dict[str, Any]:
        merged: Dict[str, Any] = {}
        keys = set(base.keys()) | set(local.keys()) | set(remote.keys())
        for key in keys:
            sub_path = path + [str(key)]
            merged[key] = self._merge_value(
                base.get(key),
                local.get(key),
                remote.get(key),
                conflicts,
                sub_path
            )
        return merged

    def _merge_value(
        self,
        base_val: Any,
        local_val: Any,
        remote_val: Any,
        conflicts: List[str],
        path: List[str]
    ) -> Any:
        if self._values_equal(local_val, remote_val):
            return copy.deepcopy(local_val)
        if self._values_equal(local_val, base_val):
            return copy.deepcopy(remote_val)
        if self._values_equal(remote_val, base_val):
            return copy.deepcopy(local_val)

        if all(
            isinstance(val, dict) or val is None
            for val in (base_val, local_val, remote_val)
        ):
            return self._merge_dicts(
                base_val or {},
                local_val or {},
                remote_val or {},
                conflicts,
                path
            )

        if all(
            isinstance(val, list) or val is None
            for val in (base_val, local_val, remote_val)
        ):
            if path[-3:] == ["workflow", "graph", "nodes"]:
                return self._merge_nodes_list(
                    base_val or [],
                    local_val or [],
                    remote_val or [],
                    conflicts,
                    path
                )
            if path[-3:] == ["workflow", "graph", "edges"]:
                return self._merge_nodes_list(
                    base_val or [],
                    local_val or [],
                    remote_val or [],
                    conflicts,
                    path
                )

        conflicts.append(".".join(path) or "<root>")
        return copy.deepcopy(local_val)

    def _merge_nodes_list(
        self,
        base_list: List[Dict[str, Any]],
        local_list: List[Dict[str, Any]],
        remote_list: List[Dict[str, Any]],
        conflicts: List[str],
        path: List[str]
    ) -> List[Dict[str, Any]]:
        for entry in base_list + local_list + remote_list:
            if entry is None:
                continue
            if not isinstance(entry, dict) or "id" not in entry:
                conflicts.append(".".join(path))
                return copy.deepcopy(local_list)

        base_map = {item["id"]: item for item in base_list}
        local_map = {item["id"]: item for item in local_list}
        remote_map = {item["id"]: item for item in remote_list}

        ordered_ids: List[Any] = []
        for source in (base_list, local_list, remote_list):
            for item in source:
                if item["id"] not in ordered_ids:
                    ordered_ids.append(item["id"])

        merged_items: Dict[Any, Dict[str, Any]] = {}
        for node_id in ordered_ids:
            base_entry = base_map.get(node_id)
            local_entry = local_map.get(node_id)
            remote_entry = remote_map.get(node_id)

            if self._values_equal(local_entry, remote_entry):
                if local_entry is not None:
                    merged_items[node_id] = copy.deepcopy(local_entry)
                continue
            if self._values_equal(local_entry, base_entry):
                if remote_entry is not None:
                    merged_items[node_id] = copy.deepcopy(remote_entry)
                continue
            if self._values_equal(remote_entry, base_entry):
                if local_entry is not None:
                    merged_items[node_id] = copy.deepcopy(local_entry)
                continue

            if local_entry is None and remote_entry is None:
                continue

            if (
                local_entry is None
                and base_entry is None
                and remote_entry is not None
            ):
                merged_items[node_id] = copy.deepcopy(remote_entry)
                continue
            if (
                remote_entry is None
                and base_entry is None
                and local_entry is not None
            ):
                merged_items[node_id] = copy.deepcopy(local_entry)
                continue

            if local_entry is None:
                if self._values_equal(base_entry, remote_entry):
                    continue
                conflicts.append(".".join(path + [str(node_id)]))
                return copy.deepcopy(local_list)
            if remote_entry is None:
                if self._values_equal(base_entry, local_entry):
                    continue
                conflicts.append(".".join(path + [str(node_id)]))
                return copy.deepcopy(local_list)

            conflicts.append(".".join(path + [str(node_id)]))
            return copy.deepcopy(local_list)

        return [
            merged_items[node_id]
            for node_id in ordered_ids
            if node_id in merged_items
        ]

    @staticmethod
    def _values_equal(left: Any, right: Any) -> bool:
        return left == right
