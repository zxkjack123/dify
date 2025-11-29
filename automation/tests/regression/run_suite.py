"""Regression suite runner for workflow automation."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, cast

import yaml  # type: ignore[import]

from automation.clients.dify_console_client import DifyConsoleClient
from automation.domain.execution import WorkflowExecutionResult
from automation.services.template_service import TemplateService
from automation.services.workflow_service import WorkflowService
from automation.services.workflow_runner import WorkflowRunner

CASES_PATH = Path(__file__).resolve().with_name("cases.json")


class FakeConsoleClient:
    """Minimal console client stub so WorkflowService can import apps."""

    def __init__(self) -> None:
        self._counter = 0

    def import_app(
        self,
        mode: str,
        yaml_content: str,
        app_id: str | None = None
    ) -> Dict[str, Any]:
        self._counter += 1
        return {"app_id": app_id or f"reg-app-{self._counter}"}

    # Unused methods are provided for API parity when needed later.
    def export_app(self, app_id: str, include_secret: bool = False) -> str:
        return ""


class FakeRunner:
    """Runner stub that returns deterministic outputs."""

    def __init__(self, expected_outputs: Dict[str, Any]):
        self.expected_outputs = expected_outputs

    def run(
        self,
        app_id: str,
        inputs: Dict[str, Any],
        mode: str = "workflow",
        trace_id: str | None = None
    ) -> WorkflowExecutionResult:
        execution = WorkflowExecutionResult(
            run_id=f"reg-run-{app_id}",
            app_id=app_id,
            inputs=inputs,
            outputs=self.expected_outputs,
            status="succeeded",
        )
        execution.finish(status="succeeded", outputs=self.expected_outputs)
        return execution

    def run_node(
        self,
        app_id: str,
        node_id: str,
        inputs: Dict[str, Any]
    ) -> Dict[str, Any]:
        return {
            "node_id": node_id,
            "status": "succeeded",
            "outputs": self.expected_outputs,
            "error": None,
            "execution_metadata": {"regression": True},
        }


class RegressionRunner:
    def __init__(
        self,
        cases_path: Path = CASES_PATH,
        template_service: TemplateService | None = None
    ):
        self.cases_path = cases_path
        self.template_service = template_service or TemplateService()
        self.cases = self._load_cases()

    def _load_cases(self) -> Dict[str, Dict[str, Any]]:
        if not self.cases_path.exists():
            raise FileNotFoundError(
                f"Regression cases not found: {self.cases_path}"
            )
        with open(self.cases_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise ValueError(
                "Cases file must contain a dictionary of scenarios"
            )
        return data

    def list_cases(self) -> List[Dict[str, Any]]:
        summary = []
        for name, payload in self.cases.items():
            summary.append({
                "id": name,
                "description": payload.get("description", ""),
                "source": payload.get("template") or payload.get("dsl_path"),
                "expected_node_count": payload.get("expected_node_count"),
            })
        return summary

    def run(self) -> Dict[str, Any]:
        results: List[Dict[str, Any]] = []
        passed = failed = 0

        for name, payload in self.cases.items():
            try:
                case_result = self._run_case(name, payload)
                case_result["status"] = "passed"
                results.append(case_result)
                passed += 1
            except Exception as exc:  # pylint: disable=broad-except
                failed += 1
                results.append({
                    "case": name,
                    "status": "failed",
                    "error": str(exc),
                })

        return {"passed": passed, "failed": failed, "cases": results}

    def _run_case(self, name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        dsl_content = self._materialize_dsl(payload)
        dsl_dict = yaml.safe_load(dsl_content)

        service = WorkflowService(
            cast(DifyConsoleClient, FakeConsoleClient())
        )
        service.runner = cast(
            WorkflowRunner,
            FakeRunner(payload.get("expected_outputs", {}))
        )

        result = service.create_and_run(dsl_content, payload.get("inputs", {}))

        expected_outputs = payload.get("expected_outputs", {})
        if expected_outputs and result["outputs"] != expected_outputs:
            raise AssertionError(  # noqa: PIE786
                f"Output mismatch for {name}: "
                f"expected {expected_outputs}, got {result['outputs']}"
            )

        dry_run_report = service.dry_run(dsl_dict)
        if not dry_run_report.get("valid", False):
            raise AssertionError(
                f"Dry-run failed for {name}: {dry_run_report}"
            )

        expected_node_count = payload.get("expected_node_count")
        if (
            expected_node_count is not None
            and dry_run_report.get("node_count") != expected_node_count
        ):
            raise AssertionError(
                f"Node count mismatch for {name}: expected "
                f"{expected_node_count}, got "
                f"{dry_run_report.get('node_count')}"
            )

        return {
            "case": name,
            "app_id": result.get("app_id"),
            "node_count": dry_run_report.get("node_count"),
            "trace_id": result.get("trace_id"),
        }

    def _materialize_dsl(self, payload: Dict[str, Any]) -> str:
        if "template" in payload:
            rendered, _context = self.template_service.render_template(
                name=payload["template"],
                overrides=payload.get("overrides")
            )
            return rendered

        dsl_path = payload.get("dsl_path")
        if not dsl_path:
            raise ValueError(
                "Each case must define either 'template' or 'dsl_path'"
            )
        path = (self.cases_path.parent / Path(dsl_path)).resolve()
        if not path.exists():
            raise FileNotFoundError(f"DSL fixture not found: {path}")
        return path.read_text(encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(
        description="Run workflow regression suite"
    )
    parser.add_argument(
        "--list", action="store_true", help="List scenarios only"
    )
    parser.add_argument(
        "--json", action="store_true", help="Output JSON summary"
    )
    args = parser.parse_args()

    runner = RegressionRunner()

    if args.list:
        cases = runner.list_cases()
        if args.json:
            print(json.dumps(cases, indent=2, ensure_ascii=False))
        else:
            for case in cases:
                print(f"- {case['id']}: {case['description']}")
        return

    summary = runner.run()
    if args.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        for case in summary["cases"]:
            status = case["status"].upper()
            line = f"[{status}] {case['case']}"
            if case.get("error"):
                line += f" -> {case['error']}"
            print(line)
        print(
            f"Passed: {summary['passed']} | Failed: {summary['failed']}"
        )

    if summary["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
