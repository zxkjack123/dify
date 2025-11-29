import unittest
from unittest.mock import MagicMock, patch
from automation.services.workflow_service import WorkflowService


class TestWorkflowService(unittest.TestCase):

    @patch('automation.services.workflow_service.DifyConsoleClient')
    def test_create_and_run_mvp(self, mock_client_cls):
        mock_client = mock_client_cls.return_value
        
        # Mock import_app response
        mock_client.import_app.return_value = {
            "app_id": "mock-app-id",
            "status": "completed"
        }
        
        # Mock run_workflow response (SSE stream)
        mock_response = MagicMock()
        mock_response.iter_lines.return_value = [
            b'data: {"event": "workflow_started"}',
            b'data: {"event": "node_started", "data": {"node_id": "n1"}}',
            b'data: {"event": "workflow_finished", "data": '
            b'{"outputs": {"text": "Hello"}}}'
        ]
        mock_client.run_workflow.return_value = mock_response
        
        service = WorkflowService(mock_client)
        result = service.create_and_run_mvp("Hello")
        
        self.assertEqual(result["app_id"], "mock-app-id")
        self.assertEqual(result["outputs"], {"text": "Hello"})
        # 1 node execution * 2 = 2
        self.assertEqual(result["events_count"], 2)
        
        mock_client.import_app.assert_called_once()
        mock_client.run_workflow.assert_called_once()

    def test_check_dependencies_no_plugins(self):
        mock_client = MagicMock()
        service = WorkflowService(mock_client)
        dsl = {
            "workflow": {
                "graph": {
                    "nodes": [
                        {"id": "1", "type": "start", "data": {}}
                    ]
                }
            }
        }
        missing = service.check_dependencies(dsl)
        self.assertEqual(missing, [])

    def test_check_dependencies_with_plugin(self):
        mock_client = MagicMock()
        service = WorkflowService(mock_client)
        # Mock _is_plugin_installed to return False
        service._is_plugin_installed = MagicMock(return_value=False)

        dsl = {
            "workflow": {
                "graph": {
                    "nodes": [
                        {
                            "id": "1",
                            "type": "tool",
                            "data": {
                                "type": "tool",
                                "provider_id": "google_search"
                            }
                        }
                    ]
                }
            }
        }
        missing = service.check_dependencies(dsl)
        self.assertEqual(missing, ["Plugin: google_search"])

    def test_dry_run_valid(self):
        mock_client = MagicMock()
        service = WorkflowService(mock_client)
        # Create a temporary valid DSL file
        import tempfile
        import os
        import yaml
        
        dsl = {
            "workflow": {
                "graph": {
                    "nodes": [
                        {"id": "1", "data": {"type": "start"}},
                        {"id": "2", "data": {"type": "end"}}
                    ]
                }
            }
        }
        
        with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
            yaml.dump(dsl, f)
            path = f.name
            
        try:
            report = service.dry_run(path)
            self.assertTrue(report["valid"])
            self.assertEqual(report["node_count"], 2)
        finally:
            os.remove(path)


if __name__ == '__main__':
    unittest.main()

