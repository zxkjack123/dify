import unittest
from automation.domain.workflow_generator import WorkflowGenerator


class TestWorkflowGenerator(unittest.TestCase):
    def setUp(self):
        self.generator = WorkflowGenerator()

    def test_generate_simple_chat_workflow(self):
        dsl = self.generator.generate_simple_chat_workflow(
            "Test App", "You are a bot."
        )
        
        self.assertIn("app:", dsl)
        self.assertIn("name: Test App", dsl)
        self.assertIn("mode: advanced-chat", dsl)
        self.assertIn("workflow:", dsl)
        self.assertIn("nodes:", dsl)
        self.assertIn("You are a bot.", dsl)


if __name__ == '__main__':
    unittest.main()
