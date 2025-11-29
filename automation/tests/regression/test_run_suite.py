"""Unit tests for the regression suite runner."""
from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Dict

from automation.services.template_service import TemplateService
from automation.tests.regression.run_suite import RegressionRunner


class StubTemplateService(TemplateService):
    """Minimal TemplateService stand-in for tests."""

    def __init__(self) -> None:  # pragma: no cover - no registry needed
        # Skip parent initialization to avoid registry dependencies
        pass

    def render_template(
        self,
        name: str,
        overrides: Dict[str, str] | None = None,
    ):
        return "", overrides or {}


def _write_cases(tmp_path: Path, cases: Dict[str, Dict[str, object]]) -> Path:
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(
        json.dumps(cases, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return cases_path


def _write_dsl(tmp_path: Path, filename: str = "simple.yml") -> str:
    dsl_content = textwrap.dedent(
        """
        app:
          name: regression
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
              - id: edge-1
                source: start
                target: end
        """
    ).strip()
    dsl_path = tmp_path / filename
    dsl_path.write_text(dsl_content, encoding="utf-8")
    return filename


def test_list_cases_returns_summary(tmp_path: Path) -> None:
    dsl_rel_path = _write_dsl(tmp_path)
    cases_path = _write_cases(tmp_path, {
        "simple": {
            "description": "basic flow",
            "dsl_path": dsl_rel_path,
            "inputs": {"query": "hi"},
            "expected_outputs": {"answer": "hello"},
            "expected_node_count": 2,
        }
    })

    runner = RegressionRunner(
        cases_path=cases_path,
        template_service=StubTemplateService(),
    )

    summary = runner.list_cases()

    assert summary == [{
        "id": "simple",
        "description": "basic flow",
        "source": dsl_rel_path,
        "expected_node_count": 2,
    }]


def test_run_reports_pass_and_failure(tmp_path: Path) -> None:
    dsl_rel_path = _write_dsl(tmp_path)
    cases_path = _write_cases(tmp_path, {
        "passing": {
            "description": "matching node count",
            "dsl_path": dsl_rel_path,
            "inputs": {"query": "42"},
            "expected_outputs": {"answer": "42"},
            "expected_node_count": 2,
        },
        "failing": {
            "description": "mismatched node count",
            "dsl_path": dsl_rel_path,
            "inputs": {"query": "?"},
            "expected_outputs": {"answer": "?"},
            "expected_node_count": 5,
        },
    })

    runner = RegressionRunner(
        cases_path=cases_path,
        template_service=StubTemplateService(),
    )

    summary = runner.run()

    assert summary["passed"] == 1
    assert summary["failed"] == 1

    cases_by_name = {case["case"]: case for case in summary["cases"]}
    assert cases_by_name["passing"]["status"] == "passed"
    assert cases_by_name["passing"]["node_count"] == 2
    assert cases_by_name["failing"]["status"] == "failed"
    assert "Node count mismatch" in cases_by_name["failing"].get("error", "")
