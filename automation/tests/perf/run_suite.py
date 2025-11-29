"""Performance suite runner for workflow automation."""
from __future__ import annotations

import argparse
import json
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, cast

from automation.clients.dify_console_client import DifyConsoleClient
from automation.domain.execution import WorkflowExecutionResult
from automation.services.template_service import TemplateService
from automation.services.workflow_service import WorkflowService

DEFAULT_CONFIG = Path(__file__).resolve().with_name("config.json")


class FakeConsoleClient:
    """Minimal console client stub used for perf tests."""

    def __init__(self) -> None:
        self._counter = 0

    def import_app(
        self,
        mode: str,
        yaml_content: str,
        app_id: str | None = None
    ) -> Dict[str, Any]:
        self._counter += 1
        return {"app_id": app_id or f"perf-app-{self._counter}"}

    def export_app(self, app_id: str, include_secret: bool = False) -> str:
        return ""


class PerfFakeRunner:
    """Runner stub that simulates latency for throughput tests."""

    def __init__(self, outputs: Dict[str, Any], latency_ms: float = 0.0):
        self.outputs = outputs or {}
        self.latency = max(0.0, latency_ms) / 1000.0

    def run(
        self,
        app_id: str,
        inputs: Dict[str, Any],
        mode: str = "workflow",
        trace_id: str | None = None
    ) -> WorkflowExecutionResult:
        if self.latency:
            time.sleep(self.latency)
        execution = WorkflowExecutionResult(
            run_id=f"perf-run-{app_id}",
            app_id=app_id,
            inputs=inputs,
            outputs=self.outputs,
            status="succeeded",
        )
        execution.finish(status="succeeded", outputs=self.outputs)
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
            "outputs": self.outputs,
            "error": None,
            "execution_metadata": {"perf": True},
        }


class PerfRunner:
    def __init__(
        self,
        config_path: Path = DEFAULT_CONFIG,
        template_service: TemplateService | None = None
    ) -> None:
        self.config_path = Path(config_path)
        self.template_service = template_service or TemplateService()
        self.project_root = self._detect_project_root()
        self.cases = self._load_cases()

    def _detect_project_root(self) -> Path:
        parents = list(self.config_path.parents)
        for parent in parents:
            automation_dir = parent / "automation"
            api_dir = parent / "api"
            if automation_dir.exists() and api_dir.exists():
                return parent
        return parents[-1]

    def _load_cases(self) -> Dict[str, Dict[str, Any]]:
        if not self.config_path.exists():
            raise FileNotFoundError(
                f"Performance config not found: {self.config_path}"
            )
        with open(self.config_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise ValueError("Perf config must be a dictionary of cases")
        return data

    def list_cases(self) -> List[Dict[str, Any]]:
        catalog: List[Dict[str, Any]] = []
        for name, payload in self.cases.items():
            catalog.append({
                "id": name,
                "description": payload.get("description", ""),
                "source": payload.get("template") or payload.get("dsl_path"),
                "iterations": payload.get("iterations", 20),
                "concurrency": payload.get("concurrency", 4),
                "runner_latency_ms": payload.get("runner_latency_ms", 0),
            })
        return catalog

    def run(self) -> Dict[str, Any]:
        suite_results: List[Dict[str, Any]] = []
        for name, payload in self.cases.items():
            suite_results.append(self._run_case(name, payload))

        total_iterations = sum(case["iterations"] for case in suite_results)
        failed_cases = sum(1 for case in suite_results if case["failed"])
        total_wall = sum(
            case["metrics"].get("total_wall_time_ms", 0.0)
            for case in suite_results
        )

        return {
            "total_cases": len(suite_results),
            "failed_cases": failed_cases,
            "total_iterations": total_iterations,
            "total_wall_time_ms": total_wall,
            "cases": suite_results,
        }

    def _run_case(self, name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        iterations = int(payload.get("iterations", 20))
        iterations = max(1, iterations)
        concurrency = int(payload.get("concurrency", min(4, iterations)))
        concurrency = max(1, min(iterations, concurrency))
        expected_outputs = payload.get("expected_outputs", {})
        latency_ms = float(payload.get("runner_latency_ms", 0))
        inputs = payload.get("inputs", {})

        dsl_content = self._materialize_dsl(payload)
        run_results: List[Dict[str, Any]] = []
        wall_start = time.perf_counter()

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = [
                executor.submit(
                    self._execute_iteration,
                    dsl_content,
                    inputs,
                    expected_outputs,
                    latency_ms,
                )
                for _ in range(iterations)
            ]
            for future in as_completed(futures):
                try:
                    run_results.append(future.result())
                except Exception as exc:  # pragma: no cover - defensive
                    run_results.append({
                        "elapsed": 0.0,
                        "status": "error",
                        "outputs": {},
                        "error": str(exc),
                        "passed": False,
                    })

        wall_time = time.perf_counter() - wall_start
        durations = [result["elapsed"] for result in run_results]
        passed = sum(1 for result in run_results if result["passed"])
        failed = iterations - passed
        metrics = self._build_metrics(durations, wall_time)

        return {
            "case": name,
            "description": payload.get("description", ""),
            "iterations": iterations,
            "concurrency": concurrency,
            "passed": passed,
            "failed": failed,
            "status": "passed" if failed == 0 else "failed",
            "metrics": metrics,
            "errors": [
                result["error"]
                for result in run_results
                if result.get("error")
            ],
        }

    def _execute_iteration(
        self,
        dsl_content: str,
        inputs: Dict[str, Any],
        expected_outputs: Dict[str, Any],
        latency_ms: float,
    ) -> Dict[str, Any]:
        client = cast(DifyConsoleClient, FakeConsoleClient())
        service = WorkflowService(client)
        service.runner = PerfFakeRunner(  # type: ignore[assignment]
            expected_outputs,
            latency_ms,
        )

        start = time.perf_counter()
        result = service.create_and_run(dsl_content, inputs)
        elapsed = time.perf_counter() - start

        status = result.get("status", "failed")
        outputs = result.get("outputs", {})
        error = result.get("error")
        passed = status == "succeeded"
        if expected_outputs:
            passed = passed and outputs == expected_outputs

        return {
            "elapsed": elapsed,
            "status": status,
            "outputs": outputs,
            "error": error,
            "passed": passed,
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
            raise ValueError("Perf case must define 'template' or 'dsl_path'")
        raw_path = Path(dsl_path)
        candidates: List[Path] = []
        if raw_path.is_absolute():
            candidates.append(raw_path)
        else:
            candidates.append((self.config_path.parent / raw_path).resolve())
            candidates.append((self.project_root / raw_path).resolve())
            candidates.append(raw_path.resolve())

        for candidate in candidates:
            if candidate.exists():
                return candidate.read_text(encoding="utf-8")

        raise FileNotFoundError(
            f"DSL fixture not found for '{dsl_path}'. Checked: "
            f"{', '.join(str(path) for path in candidates)}"
        )

    @staticmethod
    def _build_metrics(
        durations: List[float],
        wall_time: float
    ) -> Dict[str, float]:
        if not durations:
            return {
                "avg_duration_ms": 0.0,
                "min_duration_ms": 0.0,
                "max_duration_ms": 0.0,
                "p95_duration_ms": 0.0,
                "throughput_rps": 0.0,
                "total_wall_time_ms": wall_time * 1000.0,
            }

        durations_ms = [value * 1000.0 for value in durations]
        durations_ms.sort()
        avg = sum(durations_ms) / len(durations_ms)
        min_duration = durations_ms[0]
        max_duration = durations_ms[-1]
        p95 = PerfRunner._percentile(durations_ms, 95)
        throughput = 0.0 if wall_time == 0 else len(durations) / wall_time

        return {
            "avg_duration_ms": avg,
            "min_duration_ms": min_duration,
            "max_duration_ms": max_duration,
            "p95_duration_ms": p95,
            "throughput_rps": throughput,
            "total_wall_time_ms": wall_time * 1000.0,
        }

    @staticmethod
    def _percentile(values: List[float], percentile: float) -> float:
        if not values:
            return 0.0
        if len(values) == 1:
            return values[0]
        rank = (percentile / 100.0) * (len(values) - 1)
        lower = math.floor(rank)
        upper = math.ceil(rank)
        if lower == upper:
            return values[int(rank)]
        lower_val = values[lower]
        upper_val = values[upper]
        return lower_val + (upper_val - lower_val) * (rank - lower)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run workflow automation performance suite"
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG),
        help="Path to performance suite config JSON",
    )
    parser.add_argument(
        "--json", action="store_true", help="Return JSON output"
    )
    args = parser.parse_args()

    runner = PerfRunner(Path(args.config))
    summary = runner.run()
    if args.json:
        print(json.dumps(summary, indent=2, ensure_ascii=False))
    else:
        for case in summary["cases"]:
            status = case["status"].upper()
            metrics = case["metrics"]
            avg = metrics['avg_duration_ms']
            p95 = metrics['p95_duration_ms']
            throughput = metrics['throughput_rps']
            print(
                f"[{status}] {case['case']} | "
                f"avg={avg:.2f}ms p95={p95:.2f}ms "
                f"throughput={throughput:.2f} rps"
            )
        print(
            f"Total cases: {summary['total_cases']} | "
            f"Failed: {summary['failed_cases']} | "
            f"Iterations: {summary['total_iterations']}"
        )

    if summary["failed_cases"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
