import unittest
from unittest.mock import MagicMock
from automation.services.workflow_runner import WorkflowRunner


class TestWorkflowRunner(unittest.TestCase):
    def setUp(self):
        self.client = MagicMock()
        self.runner = WorkflowRunner(self.client)

    def test_run_success(self):
        # Mock SSE response
        mock_response = MagicMock()
        mock_response.iter_lines.return_value = [
            b'data: {"event": "workflow_started", "workflow_run_id": "run-1"}',
            b'data: {"event": "node_started", "data": {"node_id": "n1", '
            b'"title": "Start", "node_type": "start"}}',
            b'data: {"event": "node_finished", "data": {"node_id": "n1", '
            b'"status": "succeeded"}}',
            b'data: {"event": "workflow_finished", "data": {"outputs": '
            b'{"res": "ok"}, "total_tokens": 10}}'
        ]
        self.client.run_workflow.return_value = mock_response

        execution = self.runner.run("app-1", {})
        
        self.assertEqual(execution.status, "succeeded")
        self.assertEqual(execution.run_id, "run-1")
        self.assertEqual(execution.outputs, {"res": "ok"})
        self.assertEqual(execution.total_tokens, 10)
        self.assertEqual(len(execution.node_executions), 1)
        self.assertEqual(execution.node_executions[0].status, "succeeded")

    def test_run_error_event(self):
        mock_response = MagicMock()
        mock_response.iter_lines.return_value = [
            b'data: {"event": "workflow_started", "workflow_run_id": "run-2"}',
            b'data: {"event": "error", "message": "Something went wrong"}'
        ]
        self.client.run_workflow.return_value = mock_response

        execution = self.runner.run("app-2", {})
        
        self.assertEqual(execution.status, "failed")
        self.assertEqual(execution.error, "Something went wrong")

    def test_run_timeout(self):
        # Mock infinite stream or slow stream
        def slow_stream():
            yield b'data: {"event": "workflow_started"}'
            import time
            time.sleep(0.2)
            yield b'data: {"event": "node_started"}'
            
        mock_response = MagicMock()
        mock_response.iter_lines.side_effect = slow_stream
        self.client.run_workflow.return_value = mock_response

        # Set very short timeout
        execution = self.runner.run("app-3", {}, timeout=0.1)
        
        self.assertEqual(execution.status, "failed")
        self.assertIn("timed out", execution.error)

    def test_run_node_success(self):
        self.client.run_workflow_node.return_value = {
            "id": "exec-1",
            "status": "succeeded",
            "outputs": {"out": "value"},
            "node_type": "llm",
            "title": "LLM Node"
        }

        result = self.runner.run_node("app-1", "node-1", {"in": "val"})
        
        self.assertEqual(result.status, "succeeded")
        self.assertEqual(result.outputs, {"out": "value"})
        self.assertEqual(result.node_id, "node-1")
        self.client.run_workflow_node.assert_called_with(
            app_id="app-1", node_id="node-1", inputs={"in": "val"}, files=None
        )

    def test_run_node_failure(self):
        self.client.run_workflow_node.side_effect = Exception("API Error")

        result = self.runner.run_node("app-1", "node-1", {})
        
        self.assertEqual(result.status, "failed")
        self.assertIn("API Error", result.error)


if __name__ == '__main__':
    unittest.main()
