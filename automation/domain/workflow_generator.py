from automation.domain.dsl_builder import DSLBuilder


class WorkflowGenerator:
    def generate_simple_chat_workflow(self, app_name: str, prompt: str) -> str:
        """
        Generates a simple Start -> LLM -> End workflow.
        """
        builder = DSLBuilder(app_name=app_name, mode="advanced-chat")

        # Start Node
        start_id = builder.add_start_node(variables=[
            {
                "variable": "query",
                "label": "User Query",
                "type": "text-input",
                "required": True
            }
        ])

        # LLM Node
        llm_id = builder.add_llm_node(
            title="LLM",
            prompt_template=[
                {"role": "system", "text": prompt},
                {"role": "user", "text": "{{#start.query#}}"}
            ]
        )

        # End Node
        end_id = builder.add_end_node(outputs=[
            {"variable": "answer", "value_selector": [llm_id, "text"]}
        ])

        # Edges
        builder.add_edge(start_id, llm_id)
        builder.add_edge(llm_id, end_id)

        return builder.to_yaml()
