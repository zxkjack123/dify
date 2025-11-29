import unittest
import json
import os
import tempfile
from automation.services.audit_service import AuditService


class TestAuditService(unittest.TestCase):
    def setUp(self):
        self.temp_file = tempfile.NamedTemporaryFile(delete=False)
        self.temp_file.close()
        self.service = AuditService(log_file=self.temp_file.name)

    def tearDown(self):
        os.remove(self.temp_file.name)

    def test_get_logs_empty(self):
        logs = self.service.get_logs()
        self.assertEqual(logs, [])

    def test_get_logs_content(self):
        data = [
            {"run_id": "1", "status": "succeeded", "duration": 1.0},
            {"run_id": "2", "status": "failed", "duration": 0.5},
            {"run_id": "3", "status": "succeeded", "duration": 1.5}
        ]
        with open(self.temp_file.name, "w") as f:
            for entry in data:
                f.write(json.dumps(entry) + "\n")

        logs = self.service.get_logs(limit=2)
        self.assertEqual(len(logs), 2)
        self.assertEqual(logs[0]["run_id"], "3")
        self.assertEqual(logs[1]["run_id"], "2")

    def test_get_stats(self):
        data = [
            {"status": "succeeded", "duration": 1.0, "total_tokens": 10},
            {"status": "failed", "duration": 0.5, "total_tokens": 5},
            {"status": "succeeded", "duration": 1.5, "total_tokens": 20}
        ]
        with open(self.temp_file.name, "w") as f:
            for entry in data:
                f.write(json.dumps(entry) + "\n")

        stats = self.service.get_stats()
        self.assertEqual(stats["total_runs"], 3)
        self.assertEqual(stats["success_rate"], 66.67)
        self.assertEqual(stats["avg_duration"], 1.0)
        self.assertEqual(stats["total_tokens"], 35)


if __name__ == '__main__':
    unittest.main()
