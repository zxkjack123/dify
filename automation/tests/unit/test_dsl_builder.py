import unittest
from automation.domain.dsl_builder import DSLBuilder
from automation.domain.exceptions import DSLValidationError


class TestDSLBuilder(unittest.TestCase):
    def test_basic_workflow_generation(self):
        builder = DSLBuilder(app_name="Test App")

        # Add nodes
        start_id = builder.add_start_node(variables=[
            {"variable": "query", "label": "Query", "type": "text-input"}
        ])
        llm_id = builder.add_llm_node(
            title="LLM",
            prompt_template=[{"role": "user", "text": "{{#start.query#}}"}]
        )
        end_id = builder.add_end_node(outputs=[
            {"variable": "text", "value_selector": [llm_id, "text"]}
        ])

        # Add edges
        builder.add_edge(start_id, llm_id)
        builder.add_edge(llm_id, end_id)

        # Generate DSL
        dsl_dict = builder.to_dict()

        self.assertEqual(dsl_dict["app"]["name"], "Test App")
        self.assertEqual(len(dsl_dict["workflow"]["graph"]["nodes"]), 3)
        self.assertEqual(len(dsl_dict["workflow"]["graph"]["edges"]), 2)

        # Check validation
        builder.validate()

    def test_validation_error_missing_start(self):
        builder = DSLBuilder()
        builder.add_end_node([])
        with self.assertRaises(DSLValidationError):
            builder.validate()

    def test_validation_error_missing_end(self):
        builder = DSLBuilder()
        builder.add_start_node([])
        with self.assertRaises(DSLValidationError):
            builder.validate()


if __name__ == '__main__':
    unittest.main()
