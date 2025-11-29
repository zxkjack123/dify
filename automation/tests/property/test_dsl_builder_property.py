import random
import string

import pytest
import yaml  # type: ignore[import]

from automation.domain.dsl_builder import DSLBuilder


ALPHABET = string.ascii_letters + string.digits + " -_"


def _random_text(
    rng: random.Random,
    min_len: int = 1,
    max_len: int = 48
) -> str:
    length = rng.randint(min_len, max_len)
    return "".join(rng.choices(ALPHABET, k=length))


def _random_start_variables(rng: random.Random) -> list:
    count = rng.randint(1, 3)
    variables = []
    for _ in range(count):
        variables.append({
            "variable": _random_text(rng, 3, 12).lower(),
            "label": _random_text(rng, 4, 24),
            "type": rng.choice([
                "text-input",
                "number",
                "paragraph",
                "select"
            ]),
            "required": rng.choice([True, False])
        })
    return variables


def _random_prompt(rng: random.Random) -> list:
    entries = []
    for _ in range(rng.randint(1, 3)):
        entries.append({
            "role": rng.choice(["system", "user", "assistant"]),
            "text": _random_text(rng, 8, 64)
        })
    return entries


def _random_model_config(rng: random.Random) -> dict:
    return {
        "provider": rng.choice(["openai", "azure-openai", "anthropic"]),
        "name": _random_text(rng, 4, 16).lower(),
        "mode": rng.choice(["chat", "completion"]),
        "completion_params": {
            "temperature": round(rng.uniform(0.0, 1.0), 2)
        }
    }


def _random_llm_defs(rng: random.Random) -> list:
    nodes = []
    for _ in range(rng.randint(0, 4)):
        nodes.append({
            "title": _random_text(rng, 6, 32),
            "prompt_template": _random_prompt(rng),
            "model_config": _random_model_config(rng)
        })
    return nodes


def _random_code_defs(rng: random.Random) -> list:
    nodes = []
    for _ in range(rng.randint(0, 3)):
        output_name = _random_text(rng, 4, 12).lower()
        nodes.append({
            "title": _random_text(rng, 6, 32),
            "code": "def main(value):\n    return {'result': value}\n",
            "output_name": output_name
        })
    return nodes


def build_random_workflow(rng: random.Random) -> DSLBuilder:
    builder = DSLBuilder(
        app_name=_random_text(rng, 6, 24),
        mode=rng.choice(["workflow", "advanced-chat"])
    )
    start_vars = _random_start_variables(rng)
    start_id = builder.add_start_node(start_vars)
    previous_node = start_id
    previous_output = start_vars[0]["variable"]

    for llm in _random_llm_defs(rng):
        node_id = builder.add_llm_node(
            title=llm["title"],
            prompt_template=llm["prompt_template"],
            model_config=llm["model_config"]
        )
        builder.add_edge(previous_node, node_id)
        previous_node = node_id
        previous_output = "text"

    for code in _random_code_defs(rng):
        node_id = builder.add_code_node(
            title=code["title"],
            code=code["code"],
            variables=[{
                "variable": f"input_{code['output_name']}",
                "value_selector": [previous_node, previous_output]
            }],
            outputs={code["output_name"]: {"type": "string"}}
        )
        builder.add_edge(previous_node, node_id)
        previous_node = node_id
        previous_output = code["output_name"]

    end_id = builder.add_end_node([
        {
            "variable": "final",
            "value_selector": [previous_node, previous_output]
        }
    ])
    builder.add_edge(previous_node, end_id)
    return builder


@pytest.mark.parametrize("seed", range(25))
def test_random_workflow_produces_valid_dsl(seed: int) -> None:
    rng = random.Random(seed)
    builder = build_random_workflow(rng)
    dsl = builder.to_dict()

    nodes = dsl["workflow"]["graph"]["nodes"]
    node_ids = [node["id"] for node in nodes]
    assert len(node_ids) == len(set(node_ids))

    node_types = [node["data"].get("type") for node in nodes]
    assert node_types.count("start") == 1
    assert node_types.count("end") == 1

    for edge in dsl["workflow"]["graph"].get("edges", []):
        assert edge["source"] in node_ids
        assert edge["target"] in node_ids


@pytest.mark.parametrize("seed", range(10))
def test_yaml_round_trip_matches_dict(seed: int) -> None:
    rng = random.Random(seed + 100)
    builder = build_random_workflow(rng)
    dsl_dict = builder.to_dict()
    yaml_payload = builder.to_yaml()
    loaded = yaml.safe_load(yaml_payload)
    assert loaded == dsl_dict
