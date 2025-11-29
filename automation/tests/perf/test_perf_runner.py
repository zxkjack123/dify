"""Tests for the workflow performance runner."""
from __future__ import annotations

import json
import textwrap
from pathlib import Path

from automation.tests.perf.run_suite import PerfRunner


def _write_dsl(tmp_path: Path, filename: str = "perf_case.yml") -> str:
    dsl = textwrap.dedent(
        """
        app:
          name: perf
          mode: workflow
        workflow:
          graph:
            nodes:
              - id: start
                data:
                  type: start
                  title: Start
              - id: end
                data:
                  type: end
                  title: End
            edges:
              - id: e-1
                source: start
                target: end
        """
    ).strip()
    path = tmp_path / filename
    path.write_text(dsl, encoding="utf-8")
    return filename


def _write_config(tmp_path: Path, dsl_filename: str) -> Path:
    config = {
        "case": {
            "description": "local perf case",
            "dsl_path": dsl_filename,
            "inputs": {"query": "hello"},
            "expected_outputs": {"answer": "world"},
            "iterations": 3,
            "concurrency": 1,
            "runner_latency_ms": 0,
        }
    }
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(config, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return config_path


def test_list_cases_returns_iterations_and_concurrency(tmp_path: Path) -> None:
    dsl_name = _write_dsl(tmp_path)
    config_path = _write_config(tmp_path, dsl_name)

    runner = PerfRunner(config_path=config_path)
    catalog = runner.list_cases()

    assert catalog == [{
        "id": "case",
        "description": "local perf case",
        "source": dsl_name,
        "iterations": 3,
        "concurrency": 1,
        "runner_latency_ms": 0,
    }]


def test_run_generates_metrics_summary(tmp_path: Path) -> None:
    dsl_name = _write_dsl(tmp_path)
    config_path = _write_config(tmp_path, dsl_name)

    runner = PerfRunner(config_path=config_path)
    summary = runner.run()

    assert summary["total_cases"] == 1
    assert summary["failed_cases"] == 0
    assert summary["total_iterations"] == 3

    case = summary["cases"][0]
    assert case["passed"] == 3
    assert case["failed"] == 0
    assert case["status"] == "passed"

    metrics = case["metrics"]
    assert metrics["avg_duration_ms"] >= 0
    assert metrics["throughput_rps"] >= 0
    assert metrics["total_wall_time_ms"] >= 0
