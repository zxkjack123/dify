import unittest
import yaml
from automation.domain.dsl_builder import DSLBuilder


class TestDSLBuilder(unittest.TestCase):

    def test_simple_workflow(self):
        builder = DSLBuilder()
        
        # Start Node
        start_id = builder.add_node(
            type="start",
            title="Start",
            data={
                "variables": [
                    {
                        "variable": "query",
                        "label": "Query",
                        "type": "text-input"
                    }
                ]

            }
        )
        
        # LLM Node
        llm_id = builder.add_node(
            type="llm",
            title="LLM",
            data={
                "model": {"provider": "openai", "name": "gpt-3.5-turbo"},
                "prompt_template": [{"role": "user", "text": "{{#query#}}"}]
            }
        )
        
        # End Node
        end_id = builder.add_node(
            type="end",
            title="End",
            data={
                "outputs": [
                    {"variable": "text", "value_selector": [llm_id, "text"]}
                ]

            }
        )
        
        # Edges
        builder.add_edge(start_id, llm_id)
        builder.add_edge(llm_id, end_id)
        
        # Generate YAML
        yaml_str = builder.to_yaml()
        
        # Verify
        data = yaml.safe_load(yaml_str)
        self.assertIn("graph", data)
        self.assertIn("nodes", data["graph"])
        self.assertIn("edges", data["graph"])
        
        nodes = data["graph"]["nodes"]
        self.assertEqual(len(nodes), 3)
        
        edges = data["graph"]["edges"]
        self.assertEqual(len(edges), 2)
        
        # Check node types
        node_types = [n["data"]["type"] for n in nodes]
        self.assertIn("start", node_types)
        self.assertIn("llm", node_types)
        self.assertIn("end", node_types)


if __name__ == '__main__':

    unittest.main()
