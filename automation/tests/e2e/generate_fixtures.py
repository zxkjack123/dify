from automation.domain.dsl_builder import DSLBuilder

def generate_greeting_workflow():
    builder = DSLBuilder(app_name="E2E Greeting", mode="workflow")
    
    # Start
    start_id = builder.add_start_node([
        {"variable": "name", "label": "Name", "type": "text-input", "required": True}
    ])
    
    # Code Node (Mocking LLM with code for deterministic E2E without API keys)
    # We use a code node to simulate logic so we don't depend on OpenAI keys in E2E
    code = """
def main(name: str) -> dict:
    return {
        "result": f"Hello, {name}!"
    }
"""
    code_id = builder.add_code_node(
        title="Greeting Code",
        code=code,
        variables=[{"variable": "name", "value_selector": [start_id, "name"]}],
        outputs={"result": {"type": "string"}}
    )
    
    # End
    end_id = builder.add_end_node([
        {"variable": "greeting", "value_selector": [code_id, "result"]}
    ])
    
    # Edges
    builder.add_edge(start_id, code_id)
    builder.add_edge(code_id, end_id)
    
    return builder.to_yaml()


def generate_math_workflow():
    builder = DSLBuilder(app_name="E2E Math", mode="workflow")
    
    # Start
    start_id = builder.add_start_node([
        {"variable": "x", "label": "X", "type": "number", "required": True},
        {"variable": "y", "label": "Y", "type": "number", "required": True}
    ])
    
    code = """
def main(x: int, y: int) -> dict:
    return {
        "sum": x + y,
        "product": x * y
    }
"""
    code_id = builder.add_code_node(
        title="Math Code",
        code=code,
        variables=[
            {"variable": "x", "value_selector": [start_id, "x"]},
            {"variable": "y", "value_selector": [start_id, "y"]}
        ],
        outputs={
            "sum": {"type": "number"},
            "product": {"type": "number"}
        }
    )
    
    # End
    end_id = builder.add_end_node([
        {"variable": "sum", "value_selector": [code_id, "sum"]},
        {"variable": "product", "value_selector": [code_id, "product"]}
    ])
    
    # Edges
    builder.add_edge(start_id, code_id)
    builder.add_edge(code_id, end_id)
    
    return builder.to_yaml()


if __name__ == "__main__":
    with open("automation/tests/e2e/workflows/simple_greeting.yml", "w") as f:
        f.write(generate_greeting_workflow())
    
    with open("automation/tests/e2e/workflows/code_math.yml", "w") as f:
        f.write(generate_math_workflow())
