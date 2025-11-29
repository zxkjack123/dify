import argparse
import sys
import json
from pathlib import Path
from typing import Dict, List, Optional
from automation.clients.dify_console_client import DifyConsoleClient
from automation.domain.exceptions import AuthError
from automation.services.workflow_service import WorkflowService
from automation.services.secret_service import SecretService
from automation.services.trace_service import TraceService
from automation.infra.metrics import RequestMetricsRecorder
from automation.services.alias_service import AliasService
from automation.services.template_service import TemplateService

DEFAULT_REGRESSION_CASES = (
    Path(__file__).resolve().parents[1]
    / "tests"
    / "regression"
    / "cases.json"
)

DEFAULT_PERF_CONFIG = (
    Path(__file__).resolve().parents[1]
    / "tests"
    / "perf"
    / "config.json"
)


def _resolve_app_id(
    alias_service: AliasService,
    app_id: Optional[str],
    alias: Optional[str]
) -> str:
    if app_id:
        return app_id
    if alias:
        entry = alias_service.get_alias(alias)
        if not entry:
            raise ValueError(f"Alias '{alias}' not found")
        return entry["app_id"]
    raise ValueError("Either --app-id or --alias must be provided")


def _parse_key_values(items: Optional[List[str]]) -> Dict[str, str]:
    result: Dict[str, str] = {}
    if not items:
        return result
    for raw in items:
        if "=" not in raw:
            raise ValueError(
                f"Invalid assignment '{raw}'. Expected KEY=VALUE."
            )
        key, value = raw.split("=", 1)
        key = key.strip()
        if not key:
            raise ValueError("Variable name cannot be empty")
        result[key] = value.strip()
    return result


def main():
    parser = argparse.ArgumentParser(
        description="Dify Workflow Automation CLI"
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # mvp command
    mvp_parser = subparsers.add_parser("mvp", help="Run MVP workflow")
    mvp_parser.add_argument(
        "--prompt", type=str, required=True, help="Input prompt"
    )

    # generate command
    gen_parser = subparsers.add_parser(
        "generate", help="Generate workflow DSL"
    )
    gen_parser.add_argument("--name", default="Generated App", help="App Name")
    gen_parser.add_argument(
        "--prompt",
        default="You are a helpful assistant.",
        help="System Prompt"
    )
    gen_parser.add_argument(
        "--output", default="workflow.yml", help="Output file path"
    )

    # dry-run command
    dry_run_parser = subparsers.add_parser("dry-run", help="Validate DSL")
    dry_run_parser.add_argument(
        "--file", required=True, help="Path to DSL file"
    )

    # diff command
    diff_parser = subparsers.add_parser(
        "diff", help="Diff local vs remote DSL"
    )
    diff_parser.add_argument("--file", required=True, help="Path to local DSL")
    diff_target_group = diff_parser.add_mutually_exclusive_group(required=True)
    diff_target_group.add_argument("--app-id", help="Remote App ID")
    diff_target_group.add_argument("--alias", help="Alias name")

    # run command
    run_parser = subparsers.add_parser("run", help="Run remote workflow")
    run_target = run_parser.add_mutually_exclusive_group(required=True)
    run_target.add_argument("--app-id", help="App ID")
    run_target.add_argument("--alias", help="Alias name")
    run_parser.add_argument(
        "--inputs", default="{}", help="Inputs JSON string"
    )
    run_parser.add_argument(
        "--mode", default="workflow", help="App mode (workflow/advanced-chat)"
    )

    # run-node command
    run_node_parser = subparsers.add_parser(
        "run-node", help="Run single node in draft workflow"
    )
    run_node_target = run_node_parser.add_mutually_exclusive_group(
        required=True
    )
    run_node_target.add_argument("--app-id", help="App ID")
    run_node_target.add_argument("--alias", help="Alias name")
    run_node_parser.add_argument("--node-id", required=True, help="Node ID")
    run_node_parser.add_argument(
        "--inputs", default="{}", help="Inputs JSON string"
    )

    # audit command
    audit_parser = subparsers.add_parser("audit", help="Audit logs and stats")
    audit_subparsers = audit_parser.add_subparsers(
        dest="audit_command", help="Audit subcommand"
    )
    
    # audit show
    audit_show_parser = audit_subparsers.add_parser(
        "show", help="Show recent logs"
    )
    audit_show_parser.add_argument(
        "--limit", type=int, default=10, help="Number of logs to show"
    )
    
    # audit stats
    audit_subparsers.add_parser("stats", help="Show aggregate statistics")
    audit_trace_parser = audit_subparsers.add_parser(
        "trace", help="Show trace timeline"
    )
    audit_trace_parser.add_argument(
        "--trace-id", required=True, help="Trace identifier"
    )
    audit_trace_parser.add_argument(
        "--json", action="store_true", help="Return JSON output"
    )
    audit_requests_parser = audit_subparsers.add_parser(
        "requests",
        help="Show console request metrics"
    )
    audit_requests_parser.add_argument(
        "--window",
        type=int,
        default=100,
        help="Number of recent entries to include in metrics"
    )
    audit_requests_parser.add_argument(
        "--tail",
        type=int,
        default=5,
        help="Show the last N request entries when not using --json"
    )
    audit_requests_parser.add_argument(
        "--json", action="store_true", help="Return JSON output"
    )

    # secrets command
    secrets_parser = subparsers.add_parser(
        "secrets", help="Manage workflow automation secrets"
    )
    secrets_subparsers = secrets_parser.add_subparsers(
        dest="secrets_command", help="Secrets subcommand"
    )

    secrets_list_parser = secrets_subparsers.add_parser(
        "list", help="List configured secrets"
    )
    secrets_list_parser.add_argument(
        "--json", action="store_true", help="Return JSON output"
    )

    secrets_set_parser = secrets_subparsers.add_parser(
        "set", help="Create or update a secret"
    )
    secrets_set_parser.add_argument(
        "--name", required=True, help="Secret name"
    )
    secrets_set_parser.add_argument(
        "--value", required=True, help="Secret value"
    )
    secrets_set_parser.add_argument(
        "--expires-in",
        type=int,
        default=None,
        help="Days until the secret expires"
    )
    secrets_set_parser.add_argument(
        "--description",
        default="",
        help="Short description for the secret"
    )

    secrets_rotate_parser = secrets_subparsers.add_parser(
        "rotate", help="Rotate or mark a secret for rotation"
    )
    secrets_rotate_parser.add_argument(
        "--name", required=True, help="Secret name"
    )
    secrets_rotate_parser.add_argument(
        "--value",
        default=None,
        help="New secret value (optional). If omitted, marks as pending."
    )
    secrets_rotate_parser.add_argument(
        "--expires-in",
        type=int,
        default=None,
        help="Days until the rotated secret expires"
    )

    secrets_validate_parser = secrets_subparsers.add_parser(
        "validate", help="Validate required secrets and expiry"
    )
    secrets_validate_parser.add_argument(
        "--required",
        nargs="+",
        default=[],
        help="List of required secret names"
    )
    secrets_validate_parser.add_argument(
        "--warn-days",
        type=int,
        default=None,
        help="Warn if a secret expires within this many days"
    )
    secrets_validate_parser.add_argument(
        "--json", action="store_true", help="Return JSON output"
    )

    secrets_policy_parser = secrets_subparsers.add_parser(
        "policy", help="Show the configured secret expiry policy"
    )
    secrets_policy_parser.add_argument(
        "--json", action="store_true", help="Return JSON output"
    )

    # template command
    template_parser = subparsers.add_parser(
        "template", help="Browse and apply workflow templates"
    )
    template_subparsers = template_parser.add_subparsers(
        dest="template_command", help="Template subcommands"
    )

    template_list_parser = template_subparsers.add_parser(
        "list", help="List available templates"
    )
    template_list_parser.add_argument(
        "--json", action="store_true", help="Return JSON output"
    )

    template_apply_parser = template_subparsers.add_parser(
        "apply", help="Materialize a template to a file"
    )
    template_apply_parser.add_argument(
        "--name", required=True, help="Template name"
    )
    template_apply_parser.add_argument(
        "--output", help="Output path (defaults to workflow_<name>.yml)"
    )
    template_apply_parser.add_argument(
        "--set",
        dest="assignments",
        metavar="KEY=VALUE",
        action="append",
        default=[],
        help="Override template variables (repeatable)",
    )
    template_apply_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite target file if it exists",
    )
    template_apply_parser.add_argument(
        "--json", action="store_true", help="Return JSON output"
    )

    # alias command
    alias_parser = subparsers.add_parser(
        "alias", help="Manage app alias mappings"
    )
    alias_subparsers = alias_parser.add_subparsers(
        dest="alias_command", help="Alias subcommands"
    )

    alias_list_parser = alias_subparsers.add_parser(
        "list", help="List existing aliases"
    )
    alias_list_parser.add_argument(
        "--json", action="store_true", help="Return JSON output"
    )

    alias_set_parser = alias_subparsers.add_parser(
        "set", help="Create or update an alias"
    )
    alias_set_parser.add_argument("--alias", required=True, help="Alias name")
    alias_set_parser.add_argument("--app-id", required=True, help="App ID")
    alias_set_parser.add_argument(
        "--description", default="", help="Alias description"
    )

    alias_get_parser = alias_subparsers.add_parser(
        "get", help="Resolve an alias"
    )
    alias_get_parser.add_argument("--alias", required=True, help="Alias name")
    alias_get_parser.add_argument(
        "--json", action="store_true", help="Return JSON output"
    )

    alias_delete_parser = alias_subparsers.add_parser(
        "delete", help="Remove an alias"
    )
    alias_delete_parser.add_argument(
        "--alias", required=True, help="Alias name"
    )

    # regression command
    regression_parser = subparsers.add_parser(
        "regression", help="Run regression workflows"
    )
    regression_subparsers = regression_parser.add_subparsers(
        dest="regression_command",
        help="Regression subcommands"
    )
    regression_list_parser = regression_subparsers.add_parser(
        "list", help="List regression cases"
    )
    regression_list_parser.add_argument(
        "--cases",
        default=str(DEFAULT_REGRESSION_CASES),
        help="Path to regression cases JSON file"
    )
    regression_list_parser.add_argument(
        "--json", action="store_true", help="Return JSON output"
    )
    regression_run_parser = regression_subparsers.add_parser(
        "run", help="Execute regression suite"
    )
    regression_run_parser.add_argument(
        "--cases",
        default=str(DEFAULT_REGRESSION_CASES),
        help="Path to regression cases JSON file"
    )
    regression_run_parser.add_argument(
        "--json", action="store_true", help="Return JSON summary"
    )

    # perf command
    perf_parser = subparsers.add_parser(
        "perf", help="Run performance validation suite"
    )
    perf_subparsers = perf_parser.add_subparsers(
        dest="perf_command",
        help="Performance subcommands"
    )
    perf_list_parser = perf_subparsers.add_parser(
        "list", help="List performance cases"
    )
    perf_list_parser.add_argument(
        "--config",
        default=str(DEFAULT_PERF_CONFIG),
        help="Path to performance config file"
    )
    perf_list_parser.add_argument(
        "--json", action="store_true", help="Return JSON output"
    )
    perf_run_parser = perf_subparsers.add_parser(
        "run", help="Execute performance suite"
    )
    perf_run_parser.add_argument(
        "--config",
        default=str(DEFAULT_PERF_CONFIG),
        help="Path to performance config file"
    )
    perf_run_parser.add_argument(
        "--json", action="store_true", help="Return JSON output"
    )

    # pull command
    pull_parser = subparsers.add_parser("pull", help="Pull remote DSL")
    pull_target = pull_parser.add_mutually_exclusive_group(required=True)
    pull_target.add_argument("--app-id", help="App ID")
    pull_target.add_argument("--alias", help="Alias name")
    pull_parser.add_argument(
        "--output", required=True, help="Output file path"
    )

    # push command
    push_parser = subparsers.add_parser("push", help="Push DSL to remote")
    push_target = push_parser.add_mutually_exclusive_group(required=True)
    push_target.add_argument("--app-id", help="App ID")
    push_target.add_argument("--alias", help="Alias name")
    push_parser.add_argument("--file", required=True, help="DSL file path")
    push_parser.add_argument(
        "--force", action="store_true", help="Force overwrite"
    )

    args = parser.parse_args()

    if args.command == "mvp":
        try:
            client = DifyConsoleClient()
            service = WorkflowService(client)
            print(f"Running MVP with prompt: {args.prompt}")
            result = service.create_and_run_mvp(args.prompt)
            print(json.dumps(result, indent=2, ensure_ascii=False))
        except AuthError:
            print("Error: Authentication failed. Check your .env file.")
            sys.exit(1)
        except Exception:
            import traceback
            traceback.print_exc()
            sys.exit(1)
    elif args.command == "generate":
        from automation.domain.workflow_generator import WorkflowGenerator
        generator = WorkflowGenerator()
        dsl = generator.generate_simple_chat_workflow(args.name, args.prompt)
        with open(args.output, "w") as f:
            f.write(dsl)
        print(f"Workflow generated at {args.output}")
    elif args.command == "dry-run":
        client = DifyConsoleClient()
        service = WorkflowService(client)
        report = service.dry_run(args.file)
        print(json.dumps(report, indent=2))
        if not report["valid"]:
            sys.exit(1)
    elif args.command == "diff":
        try:
            client = DifyConsoleClient()
            service = WorkflowService(client)
            alias_service = AliasService()
            target_app_id = _resolve_app_id(
                alias_service,
                args.app_id,
                args.alias
            )
            diff_result = service.diff(args.file, target_app_id)
            print(json.dumps(diff_result, indent=2))
        except Exception as e:
            print(f"Error during diff: {str(e)}")
            sys.exit(1)
    elif args.command == "run":
        try:
            client = DifyConsoleClient()
            from automation.services.workflow_runner import WorkflowRunner
            trace_service = TraceService()
            runner = WorkflowRunner(client, trace_service=trace_service)
            trace_id = trace_service.new_trace_id()
            inputs = json.loads(args.inputs)
            alias_service = AliasService()
            target_app_id = _resolve_app_id(
                alias_service,
                args.app_id,
                args.alias
            )
            print(
                f"Running app {target_app_id} (trace {trace_id})..."
            )
            execution = runner.run(
                target_app_id,
                inputs,
                mode=args.mode,
                trace_id=trace_id
            )
            print(f"Trace ID: {trace_id}")
            
            print(f"Status: {execution.status}")
            if execution.error:
                print(f"Error: {execution.error}")
            print("Outputs:")
            print(json.dumps(execution.outputs, indent=2, ensure_ascii=False))
            print(f"Total Tokens: {execution.total_tokens}")
            print(f"Duration: {execution.duration:.2f}s")
        except Exception as e:
            print(f"Error during run: {str(e)}")
            sys.exit(1)
    elif args.command == "run-node":
        try:
            client = DifyConsoleClient()
            service = WorkflowService(client)
            inputs = json.loads(args.inputs)
            alias_service = AliasService()
            target_app_id = _resolve_app_id(
                alias_service,
                args.app_id,
                args.alias
            )
            print(f"Running node {args.node_id} in app {target_app_id}...")
            result = service.run_node(target_app_id, args.node_id, inputs)
            
            print(f"Status: {result['status']}")
            if result.get('error'):
                print(f"Error: {result['error']}")
            print("Outputs:")
            print(json.dumps(result['outputs'], indent=2, ensure_ascii=False))
        except Exception as e:
            print(f"Error during run-node: {str(e)}")
            sys.exit(1)
    elif args.command == "audit":
        from automation.services.audit_service import AuditService
        audit_service = AuditService()
        
        if args.audit_command == "show":
            logs = audit_service.get_logs(limit=args.limit)
            print(json.dumps(logs, indent=2, ensure_ascii=False))
        elif args.audit_command == "stats":
            stats = audit_service.get_stats()
            print(json.dumps(stats, indent=2, ensure_ascii=False))
        elif args.audit_command == "trace":
            trace_service = TraceService()
            entries = trace_service.get_trace(args.trace_id)
            if args.json:
                print(json.dumps(entries, indent=2, ensure_ascii=False))
            else:
                if not entries:
                    print(
                        f"No trace events found for {args.trace_id}."
                    )
                for item in entries:
                    timestamp = item.get("timestamp")
                    stage = item.get("stage")
                    action = item.get("action")
                    metadata = item.get("metadata", {})
                    print(
                        f"[{timestamp}] {action} -> {stage}: "
                        f"{json.dumps(metadata, ensure_ascii=False)}"
                    )
        elif args.audit_command == "requests":
            recorder = RequestMetricsRecorder()
            summary = recorder.summarize(window=args.window)
            if args.json:
                payload = dict(summary)
                payload["recent"] = recorder.tail(limit=args.window)
                print(json.dumps(payload, indent=2, ensure_ascii=False))
            else:
                print(
                    f"Total requests: {summary['total_requests']} | "
                    f"Success rate: {summary['success_rate']}%"
                )
                print(
                    f"Avg duration: {summary['avg_duration_ms']} ms | "
                    f"P95: {summary['p95_duration_ms']} ms"
                )
                if summary["status_distribution"]:
                    print("Status codes:")
                    for code, count in summary["status_distribution"].items():
                        print(f"- {code}: {count}")
                if summary["failure_types"]:
                    print("Failure types:")
                    for name, count in summary["failure_types"].items():
                        print(f"- {name}: {count}")
                tail_count = max(args.tail, 0)
                if tail_count:
                    recent = recorder.tail(limit=tail_count)
                    if recent:
                        print(
                            f"Last {min(tail_count, len(recent))} request(s):"
                        )
                        for entry in recent[-tail_count:]:
                            status = entry.get("status_code") or "n/a"
                            result = (
                                "ok"
                                if entry.get("success")
                                else entry.get("error_type", "error")
                            )
                            duration = entry.get("duration_ms")
                            trace_val = entry.get("trace_id")
                            print(
                                f"[{entry['timestamp']}] {entry['method']} "
                                f"{entry['endpoint']} status={status} "
                                f"{duration}ms trace={trace_val} "
                                f"result={result}"
                            )
                    else:
                        print("No recent request entries recorded.")
        else:
            audit_parser.print_help()
    elif args.command == "secrets":
        secret_service = SecretService()
        if args.secrets_command == "list":
            secrets = secret_service.list_secrets()
            if args.json:
                print(json.dumps(secrets, indent=2, ensure_ascii=False))
            else:
                if not secrets:
                    print("No secrets configured.")
                for item in secrets:
                    preview = item.get("value_preview", "") or "(empty)"
                    status = item.get("status", "unknown")
                    expires = item.get("expires_at") or "n/a"
                    print(
                        f"- {item['name']}: {status}, expires {expires}, "
                        f"value {preview}"
                    )
        elif args.secrets_command == "set":
            secret_service.set_secret(
                name=args.name,
                value=args.value,
                expires_in_days=args.expires_in,
                description=args.description
            )
            print(f"Secret '{args.name}' stored successfully.")
        elif args.secrets_command == "rotate":
            secret_service.rotate_secret(
                name=args.name,
                new_value=args.value,
                expires_in_days=args.expires_in
            )
            if args.value:
                print(f"Secret '{args.name}' rotated with new value.")
            else:
                print(
                    f"Secret '{args.name}' marked for rotation. "
                    "Update the value soon."
                )
        elif args.secrets_command == "validate":
            result = secret_service.validate_secrets(
                required_names=args.required,
                warn_within_days=args.warn_days
            )
            if args.json:
                print(json.dumps(result, indent=2, ensure_ascii=False))
            else:
                if result["missing"]:
                    print("Missing secrets:")
                    for name in result["missing"]:
                        print(f"- {name}")
                if result["expiring"]:
                    print("Secrets expiring soon:")
                    for item in result["expiring"]:
                        print(
                            f"- {item['name']}: {item['status']} "
                            f"in {item['days_left']} days"
                        )
                if not result["missing"] and not result["expiring"]:
                    print("All required secrets look healthy.")
            if result["missing"] or result["expiring"]:
                sys.exit(1)
        elif args.secrets_command == "policy":
            summary = secret_service.get_policy_summary()
            if args.json:
                print(json.dumps(summary, indent=2, ensure_ascii=False))
            else:
                path = summary["path"]
                if not summary["enabled"]:
                    print(
                        "No policy file found. Create one at "
                        f"{path} or copy policy.example.json."
                    )
                else:
                    print(f"Policy path: {path}")
                    print(
                        "Default warn within days: "
                        f"{summary['default_warn_within_days']}"
                    )
                    default_ttl = summary.get("default_ttl_days")
                    if default_ttl is not None:
                        print(f"Default TTL days: {default_ttl}")
                    if not summary["secrets"]:
                        print("No per-secret overrides defined.")
                    else:
                        print("Per-secret rules:")
                        for entry in summary["secrets"]:
                            desc = entry.get("description") or ""
                            required = (
                                "required"
                                if entry["required"]
                                else "optional"
                            )
                            warn = entry.get("warn_within_days")
                            ttl = entry.get("default_ttl_days")
                            max_ttl = entry.get("max_ttl_days")
                            scopes = (
                                ", ".join(entry.get("scopes") or [])
                                or "n/a"
                            )
                            print(
                                f"- {entry['name']} ({required}, "
                                f"scopes {scopes})"
                            )
                            details: List[str] = []
                            if warn is not None:
                                details.append(f"warn {warn}d")
                            if ttl is not None:
                                details.append(f"default ttl {ttl}d")
                            if max_ttl is not None:
                                details.append(f"max ttl {max_ttl}d")
                            if details:
                                print("  " + ", ".join(details))
                            if desc:
                                print(f"  {desc}")
        else:
            secrets_parser.print_help()
    elif args.command == "template":
        template_service = TemplateService()
        if args.template_command == "list":
            templates = template_service.list_templates()
            if args.json:
                print(json.dumps(templates, indent=2, ensure_ascii=False))
            else:
                if not templates:
                    print("No templates available.")
                for item in templates:
                    categories = ", ".join(item.get("categories", [])) or "n/a"
                    variables = item.get("variables", [])
                    var_preview = ", ".join(
                        f"{var['name']}{'*' if var.get('required') else ''}"
                        for var in variables
                    ) or "none"
                    print(
                        f"- {item['name']} ({item['title']}): "
                        f"{item['description']}\n"
                        f"  Categories: {categories} | "
                        f"Variables: {var_preview}"
                    )
        elif args.template_command == "apply":
            try:
                overrides = _parse_key_values(args.assignments)
            except ValueError as exc:
                print(f"Error: {exc}")
                sys.exit(1)
            output_path = args.output or f"workflow_{args.name}.yml"
            try:
                result = template_service.apply_template(
                    name=args.name,
                    output_path=output_path,
                    overrides=overrides,
                    overwrite=args.force
                )
            except Exception as exc:
                print(f"Failed to apply template: {exc}")
                sys.exit(1)
            if args.json:
                print(json.dumps(result, indent=2, ensure_ascii=False))
            else:
                print(
                    f"Template '{args.name}' exported to "
                    f"{result['output_path']}"
                )
                if overrides:
                    print(
                        "Variables: "
                        + ", ".join(
                            f"{key}={value}"
                            for key, value in overrides.items()
                        )
                    )
        else:
            template_parser.print_help()
    elif args.command == "pull":
        try:
            client = DifyConsoleClient()
            service = WorkflowService(client)
            alias_service = AliasService()
            target_app_id = _resolve_app_id(
                alias_service,
                args.app_id,
                args.alias
            )
            service.pull_app(target_app_id, args.output)
            print(
                f"Successfully pulled app {target_app_id} to {args.output}"
            )
        except Exception as e:
            print(f"Error during pull: {str(e)}")
            sys.exit(1)
    elif args.command == "push":
        try:
            client = DifyConsoleClient()
            service = WorkflowService(client)
            trace_id = service.trace_service.new_trace_id()
            alias_service = AliasService()
            target_app_id = _resolve_app_id(
                alias_service,
                args.app_id,
                args.alias
            )
            service.push_app(
                target_app_id,
                args.file,
                force=args.force,
                trace_id=trace_id
            )
            print(
                f"Successfully pushed {args.file} to app {target_app_id}. "
                f"Trace ID: {trace_id}"
            )
        except Exception as e:
            print(f"Error during push: {str(e)}")
            sys.exit(1)
    elif args.command == "alias":
        alias_service = AliasService()
        if args.alias_command == "list":
            aliases = alias_service.list_aliases()
            if args.json:
                print(json.dumps(aliases, indent=2, ensure_ascii=False))
            else:
                if not aliases:
                    print("No aliases configured.")
                for item in aliases:
                    print(
                        f"- {item['alias']}: {item['app_id']} "
                        f"({item.get('description', '')})"
                    )
        elif args.alias_command == "set":
            alias_service.set_alias(
                alias=args.alias,
                app_id=args.app_id,
                description=args.description
            )
            print(
                f"Alias '{args.alias}' now points to app {args.app_id}."
            )
        elif args.alias_command == "get":
            entry = alias_service.get_alias(args.alias)
            if not entry:
                print(f"Alias '{args.alias}' not found.")
                sys.exit(1)
            if args.json:
                payload = {"alias": args.alias, **entry}
                print(json.dumps(payload, indent=2, ensure_ascii=False))
            else:
                print(
                    f"Alias '{args.alias}' -> {entry['app_id']} "
                    f"({entry.get('description', '')})"
                )
        elif args.alias_command == "delete":
            removed = alias_service.delete_alias(args.alias)
            if removed:
                print(f"Alias '{args.alias}' removed.")
            else:
                print(f"Alias '{args.alias}' not found.")
                sys.exit(1)
        else:
            alias_parser.print_help()
    elif args.command == "regression":
        from automation.tests.regression.run_suite import RegressionRunner

        cases_path = Path(
            getattr(args, "cases", DEFAULT_REGRESSION_CASES)
        ).expanduser().resolve()
        try:
            runner = RegressionRunner(cases_path=cases_path)
        except Exception as exc:
            print(f"Failed to load regression suite: {exc}")
            sys.exit(1)

        if args.regression_command == "list":
            cases = runner.list_cases()
            if args.json:
                print(json.dumps(cases, indent=2, ensure_ascii=False))
            else:
                if not cases:
                    print("No regression cases registered.")
                for case in cases:
                    desc = case.get("description", "")
                    print(f"- {case['id']}: {desc}")
        elif args.regression_command == "run":
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
                    f"Passed: {summary['passed']} | "
                    f"Failed: {summary['failed']}"
                )
            if summary["failed"]:
                sys.exit(1)
        else:
            regression_parser.print_help()
    elif args.command == "perf":
        from automation.tests.perf.run_suite import PerfRunner

        config_path = Path(args.config).expanduser().resolve()
        try:
            runner = PerfRunner(config_path)
        except Exception as exc:
            print(f"Failed to load performance suite: {exc}")
            sys.exit(1)

        if args.perf_command == "list":
            cases = runner.list_cases()
            if args.json:
                print(json.dumps(cases, indent=2, ensure_ascii=False))
            else:
                if not cases:
                    print("No performance cases configured.")
                for case in cases:
                    desc = case.get("description", "")
                    print(
                        f"- {case['id']}: {desc} "
                        f"(iter={case['iterations']}, "
                        f"conc={case['concurrency']})"
                    )
        elif args.perf_command == "run":
            summary = runner.run()
            if args.json:
                print(json.dumps(summary, indent=2, ensure_ascii=False))
            else:
                for case in summary["cases"]:
                    metrics = case["metrics"]
                    print(
                        f"[{case['status'].upper()}] {case['case']} "
                        f"avg={metrics['avg_duration_ms']:.2f}ms "
                        f"p95={metrics['p95_duration_ms']:.2f}ms "
                        f"throughput={metrics['throughput_rps']:.2f} rps"
                    )
                print(
                    f"Total cases: {summary['total_cases']} | "
                    f"Failed: {summary['failed_cases']} | "
                    f"Iterations: {summary['total_iterations']}"
                )
            if summary["failed_cases"]:
                sys.exit(1)
        else:
            perf_parser.print_help()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
