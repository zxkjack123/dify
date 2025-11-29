import unittest
import os
import hashlib
import tempfile
import textwrap
from unittest.mock import MagicMock
from automation.services.workflow_service import WorkflowService


class TestWorkflowSync(unittest.TestCase):
    def setUp(self):
        self.mock_client = MagicMock()
        self.service = WorkflowService(self.mock_client)
        self.app_id = "app-123"
        self.dsl_content = "workflow:\n  graph:\n    nodes: []"
        self.dsl_hash = hashlib.sha256(
            self.dsl_content.encode('utf-8')
        ).hexdigest()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.dsl_path = os.path.join(self.temp_dir.name, "workflow.yml")
        self.hash_path = f"{self.dsl_path}.hash"
        self.base_path = f"{self.dsl_path}.base"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_pull_app(self):
        self.mock_client.export_app.return_value = self.dsl_content
        
        self.service.pull_app(self.app_id, self.dsl_path)
        
        self.assertTrue(os.path.exists(self.dsl_path))
        self.assertTrue(os.path.exists(self.hash_path))
        self.assertTrue(os.path.exists(self.base_path))
        
        with open(self.dsl_path, "r") as f:
            self.assertEqual(f.read(), self.dsl_content)
            
        with open(self.hash_path, "r") as f:
            self.assertEqual(f.read(), self.dsl_hash)

        with open(self.base_path, "r") as f:
            self.assertEqual(f.read(), self.dsl_content)

    def test_pull_app_overwrites_base_snapshot(self):
        existing_content = "workflow:\n  graph:\n    nodes: [{id: 1}]"
        updated_content = "workflow:\n  graph:\n    nodes: [{id: 2}]"

        with open(self.base_path, "w") as f:
            f.write(existing_content)

        self.mock_client.export_app.return_value = updated_content

        self.service.pull_app(self.app_id, self.dsl_path)

        with open(self.base_path, "r") as f:
            self.assertEqual(f.read(), updated_content)

    def test_push_app_success(self):
        # Setup local files
        updated_content = self.dsl_content.replace(
            "nodes: []",
            "nodes: [{id: 1}]"
        )
        with open(self.dsl_path, "w") as f:
            f.write(updated_content)
        with open(self.hash_path, "w") as f:
            f.write(self.dsl_hash)

        # Mock remote matching last pull snapshot
        self.mock_client.export_app.return_value = self.dsl_content

        self.service.push_app(self.app_id, self.dsl_path)

        self.mock_client.import_app.assert_called()

    def test_push_app_conflict(self):
        # Setup local files
        with open(self.dsl_path, "w") as f:
            f.write(self.dsl_content)
        with open(self.hash_path, "w") as f:
            f.write(self.dsl_hash)
            
        # Mock remote DIFFERENT from local hash
        remote_content = "workflow:\n  graph:\n    nodes: [{id: 1}]"
        self.mock_client.export_app.return_value = remote_content
        
        with self.assertRaises(ValueError) as cm:
            self.service.push_app(self.app_id, self.dsl_path)
            
        self.assertIn("Remote app has changed", str(cm.exception))
        self.mock_client.import_app.assert_not_called()

    def test_push_app_force(self):
        # Setup local files
        with open(self.dsl_path, "w") as f:
            f.write(self.dsl_content)
        with open(self.hash_path, "w") as f:
            f.write(self.dsl_hash)
            
        # Mock remote DIFFERENT
        remote_content = "workflow:\n  graph:\n    nodes: [{id: 1}]"
        self.mock_client.export_app.return_value = remote_content
        
        # Should succeed with force=True
        self.service.push_app(self.app_id, self.dsl_path, force=True)
        
        self.mock_client.import_app.assert_called()

    def test_push_app_no_hash(self):
        with open(self.dsl_path, "w") as f:
            f.write(self.dsl_content)

        with self.assertRaises(ValueError) as cm:
            self.service.push_app(self.app_id, self.dsl_path)

        self.assertIn("No sync history found", str(cm.exception))

    def test_push_app_conflict_auto_merge_generates_file(self):
        base_content = textwrap.dedent(
            """
            workflow:
              graph:
                nodes:
                  - id: start
                    data:
                      type: start
                  - id: llm
                    data:
                      type: llm
                      title: Agent
                  - id: end
                    data:
                      type: end
                edges:
                  - id: e1
                    source: start
                    target: llm
                  - id: e2
                    source: llm
                    target: end
            """
        ).strip()
        local_content = textwrap.dedent(
            """
            workflow:
              graph:
                nodes:
                  - id: start
                    data:
                      type: start
                  - id: llm
                    data:
                      type: llm
                      title: Agent Local
                  - id: end
                    data:
                      type: end
                edges:
                  - id: e1
                    source: start
                    target: llm
                  - id: e2
                    source: llm
                    target: end
            """
        ).strip()
        remote_content = textwrap.dedent(
            """
            workflow:
              graph:
                nodes:
                  - id: start
                    data:
                      type: start
                  - id: llm
                    data:
                      type: llm
                      title: Agent
                  - id: review
                    data:
                      type: llm
                      title: Reviewer
                  - id: end
                    data:
                      type: end
                edges:
                  - id: e1
                    source: start
                    target: llm
                  - id: e2
                    source: llm
                    target: review
                  - id: e3
                    source: review
                    target: end
            """
        ).strip()

        with open(self.dsl_path, "w") as f:
            f.write(local_content)
        with open(self.base_path, "w") as f:
            f.write(base_content)
        with open(self.hash_path, "w") as f:
            f.write(hashlib.sha256(base_content.encode("utf-8")).hexdigest())

        self.mock_client.export_app.return_value = remote_content

        auto_path = f"{self.dsl_path}.auto-merged.yml"
        with self.assertRaises(ValueError) as cm:
            self.service.push_app(self.app_id, self.dsl_path)

        message = str(cm.exception)
        self.assertIn("Auto-merged draft", message)
        self.mock_client.import_app.assert_not_called()
        self.assertTrue(os.path.exists(auto_path))

        with open(auto_path, "r") as merged_file:
            merged_payload = merged_file.read()
            self.assertIn("Agent Local", merged_payload)
            self.assertIn("review", merged_payload)

    def test_push_app_skip_when_no_changes(self):
        with open(self.dsl_path, "w") as f:
            f.write(self.dsl_content)
        with open(self.hash_path, "w") as f:
            f.write(self.dsl_hash)

        self.mock_client.export_app.return_value = self.dsl_content

        result = self.service.push_app(self.app_id, self.dsl_path)

        self.mock_client.import_app.assert_not_called()
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["reason"], "no_changes")

    def test_push_app_import_failure_status(self):
        local_content = self.dsl_content.replace(
            "nodes: []",
            "nodes: [{id: 1}]"
        )
        remote_content = self.dsl_content

        with open(self.dsl_path, "w") as f:
            f.write(local_content)
        with open(self.hash_path, "w") as f:
            f.write(
                hashlib.sha256(remote_content.encode("utf-8")).hexdigest()
            )

        self.mock_client.export_app.return_value = remote_content
        self.mock_client.import_app.return_value = {
            "data": {
                "status": "failed",
                "message": "validation error"
            }
        }

        with self.assertRaises(ValueError) as cm:
            self.service.push_app(self.app_id, self.dsl_path)

        self.assertIn("validation error", str(cm.exception))
