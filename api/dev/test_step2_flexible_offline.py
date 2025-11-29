from __future__ import annotations

"""Offline sanity tests for [8][12] Essay Step 2 flexible workflow.

This script does *not* run the full Dify engine. Instead it:

1. Loads the YAML DSL for the Step 2 flexible workflow.
2. Compiles all Python code nodes to ensure there are no syntax errors.
3. Runs key parsing code (AO1/AO2/AO3 extractors) on sample inputs:
   - JSON-shaped parser output for AO1/AO2/AO3.
   - The real user-provided outline Markdown text for the AO1 flexible parser,
     to verify it does not crash and returns a reasonable keyword list.

The goal is to catch obvious configuration or code issues before wiring
everything into a live Dify instance.
"""

from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml

ROOT = Path(__file__).resolve().parents[2]
DSL_PATH = ROOT / "workflows_local/essay_write_step2/[8][12] Essay Step 2 flexible.yml"
OUTLINE_MD_PATH = ROOT / (
    "workflows_local/essay_write_step2/用 step 1 改完后的 -Explain three causes of "
    "unemployment and consider which cause （新 970821MJ24）.md"
)


def load_workflow() -> Dict[str, Any]:
    if not DSL_PATH.exists():
        raise SystemExit(f"DSL file not found: {DSL_PATH}")

    text = DSL_PATH.read_text(encoding="utf-8")
    try:
        data: Dict[str, Any] = yaml.safe_load(text)
    except Exception as exc:  # pragma: no cover - defensive
        raise SystemExit(f"Failed to parse YAML DSL: {exc}") from exc

    if not isinstance(data, dict):
        raise SystemExit("Parsed DSL is not a mapping at top level")

    return data


def build_node_map(data: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    try:
        nodes: List[Dict[str, Any]] = data["workflow"]["graph"]["nodes"]
    except Exception as exc:  # pragma: no cover - schema guard
        raise SystemExit(f"Unexpected DSL schema when accessing nodes: {exc}") from exc

    node_map: Dict[str, Dict[str, Any]] = {}
    for node in nodes:
        node_id = node.get("id")
        if isinstance(node_id, str):
            node_map[node_id] = node
    return node_map


def compile_python_nodes(node_map: Dict[str, Dict[str, Any]]) -> List[Tuple[str, str, Exception]]:
    """Compile all python3 code nodes to ensure syntax is valid.

    Returns a list of (node_id, title, exception) for any failures.
    """

    failures: List[Tuple[str, str, Exception]] = []

    for node_id, node in node_map.items():
        data = node.get("data", {})
        code = data.get("code")
        if not isinstance(code, str):
            continue
        if data.get("code_language") not in {"python3", "python"}:
            continue

        title = str(data.get("title", ""))
        try:
            ns: Dict[str, Any] = {}
            compiled = compile(code, f"<node {node_id} {title}>", "exec")
            exec(compiled, ns, ns)
        except Exception as exc:  # pragma: no cover - diagnostic
            failures.append((node_id, title, exc))

    return failures


def run_json_parser_smoke_tests(node_map: Dict[str, Dict[str, Any]]) -> None:
    """Run AO1/AO2/AO3 JSON parsers on a small sample payload.

    This exercises the shared parse_parser_output/normalize_list helpers.
    """

    sample_json = {
        "ao1_keywords": [
            "Frictional unemployment: short-term job search",
            "Structural unemployment: mismatch of skills",
        ],
        "ao2_logics": [
            "If AD falls, output falls and unemployment rises",
        ],
        "ao3_factors": [
            "Magnitude of demand shock", "Labour market flexibility",
        ],
    }

    import json as _json

    payload = _json.dumps(sample_json, ensure_ascii=False)

    def _run_node_by_title(title: str) -> Dict[str, Any]:
        for node in node_map.values():
            data = node.get("data", {})
            if data.get("title") == title and isinstance(data.get("code"), str):
                ns: Dict[str, Any] = {}
                compiled = compile(data["code"], f"<node {title}>", "exec")
                exec(compiled, ns, ns)
                if "main" not in ns:
                    raise SystemExit(f"Node '{title}' has no main() function")
                result = ns["main"](payload)
                if not isinstance(result, dict):
                    raise SystemExit(f"Node '{title}' returned non-dict: {type(result)}")
                return result
        raise SystemExit(f"Code node with title '{title}' not found in DSL")

    ao1_result = _run_node_by_title("获取AO1-关键词")
    ao2_result = _run_node_by_title("获取AO2-logics")
    ao3_result = _run_node_by_title("获取AO3-factors")

    # Basic shape checks
    assert isinstance(ao1_result.get("keywords"), list)
    assert isinstance(ao1_result.get("num_keywords"), int)

    assert isinstance(ao2_result.get("logics"), list)
    assert isinstance(ao2_result.get("num_logics"), int)

    assert isinstance(ao3_result.get("factors"), list)
    assert isinstance(ao3_result.get("num_factors"), int)


def run_outline_parser_on_md(node_map: Dict[str, Dict[str, Any]]) -> None:
    """Feed the real outline Markdown into the AO1 flexible parser.

    This approximates the user scenario where the outline text may not
    strictly follow the original template.
    """

    if not OUTLINE_MD_PATH.exists():
        raise SystemExit(f"Outline Markdown not found: {OUTLINE_MD_PATH}")

    outline_text = OUTLINE_MD_PATH.read_text(encoding="utf-8")

    target_title = "获取AO1-关键词-小分"
    for node in node_map.values():
        data = node.get("data", {})
        if data.get("title") == target_title and isinstance(data.get("code"), str):
            ns: Dict[str, Any] = {}
            compiled = compile(data["code"], f"<node {target_title}>", "exec")
            exec(compiled, ns, ns)
            if "main" not in ns:
                raise SystemExit(f"Node '{target_title}' has no main() function")

            result = ns["main"](outline_text)
            if not isinstance(result, dict):
                raise SystemExit(
                    f"AO1 flexible parser returned non-dict: {type(result)}"
                )

            keywords = result.get("keywords")
            num_keywords = result.get("num_keywords")
            if not isinstance(keywords, list) or not isinstance(num_keywords, int):
                raise SystemExit(
                    "AO1 flexible parser returned unexpected structure: "
                    f"keywords={type(keywords)}, num_keywords={type(num_keywords)}"
                )

            # The exact keywords are model/outline dependent; we only require
            # that the parser can extract at least one non-empty keyword.
            if not keywords:
                raise SystemExit(
                    "AO1 flexible parser produced an empty keyword list "
                    "for the provided outline Markdown."
                )

            return

    raise SystemExit(f"Code node '{target_title}' not found in DSL")


def main() -> None:
    data = load_workflow()
    node_map = build_node_map(data)

    failures = compile_python_nodes(node_map)
    if failures:
        msg_lines = ["Python code compilation failed for some nodes:"]
        for node_id, title, exc in failures:
            msg_lines.append(f"- Node {node_id} ({title}): {exc}")
        raise SystemExit("\n".join(msg_lines))

    run_json_parser_smoke_tests(node_map)
    run_outline_parser_on_md(node_map)

    print("Step 2 flexible offline tests passed: code nodes compiled and AO1/2/3 parsers behaved as expected.")


if __name__ == "__main__":  # pragma: no cover
    main()
