import unittest
from automation.services.diff_service import DiffService


class TestDiffService(unittest.TestCase):
    def setUp(self):
        self.service = DiffService()

    def test_diff_no_changes(self):
        dsl = {
            "workflow": {
                "graph": {
                    "nodes": [
                        {"id": "1", "type": "start", "data": {"foo": "bar"}}
                    ]
                }
            }
        }
        result = self.service.diff(dsl, dsl)
        self.assertFalse(result["has_changes"])
        self.assertEqual(result["nodes"]["added"], [])
        self.assertEqual(result["nodes"]["removed"], [])
        self.assertEqual(result["nodes"]["modified"], [])

    def test_diff_added_node(self):
        local_dsl = {
            "workflow": {
                "graph": {
                    "nodes": [
                        {"id": "1", "type": "start"},
                        {"id": "2", "type": "end"}
                    ]
                }
            }
        }
        remote_dsl = {
            "workflow": {
                "graph": {
                    "nodes": [
                        {"id": "1", "type": "start"}
                    ]
                }
            }
        }
        result = self.service.diff(local_dsl, remote_dsl)
        self.assertTrue(result["has_changes"])
        self.assertEqual(result["nodes"]["added"], ["2"])

    def test_diff_removed_node(self):
        local_dsl = {
            "workflow": {
                "graph": {
                    "nodes": [
                        {"id": "1", "type": "start"}
                    ]
                }
            }
        }
        remote_dsl = {
            "workflow": {
                "graph": {
                    "nodes": [
                        {"id": "1", "type": "start"},
                        {"id": "2", "type": "end"}
                    ]
                }
            }
        }
        result = self.service.diff(local_dsl, remote_dsl)
        self.assertTrue(result["has_changes"])
        self.assertEqual(result["nodes"]["removed"], ["2"])

    def test_diff_modified_node(self):
        local_dsl = {
            "workflow": {
                "graph": {
                    "nodes": [
                        {"id": "1", "type": "start", "data": {"v": 2}}
                    ]
                }
            }
        }
        remote_dsl = {
            "workflow": {
                "graph": {
                    "nodes": [
                        {"id": "1", "type": "start", "data": {"v": 1}}
                    ]
                }
            }
        }
        result = self.service.diff(local_dsl, remote_dsl)
        self.assertTrue(result["has_changes"])
        self.assertEqual(result["nodes"]["modified"], ["1"])


if __name__ == '__main__':
    unittest.main()
