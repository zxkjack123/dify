import os
import tempfile
import unittest

from automation.services.trace_service import TraceService


class TestTraceService(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.log_path = os.path.join(self.temp_dir.name, "traces.jsonl")
        self.service = TraceService(log_file=self.log_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_record_and_get_trace(self):
        trace_id = self.service.new_trace_id()
        self.service.record(trace_id, "workflow_run", "start", {"step": 1})
        self.service.record(
            trace_id,
            "workflow_run",
            "finish",
            {"status": "ok"}
        )

        entries = self.service.get_trace(trace_id)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["stage"], "start")
        self.assertEqual(entries[1]["metadata"]["status"], "ok")

    def test_list_recent(self):
        ids = []
        for _ in range(3):
            tid = self.service.new_trace_id()
            ids.append(tid)
            self.service.record(
                tid,
                "push_app",
                "finish",
                {"app_id": f"app-{_}"}
            )

        recent = self.service.list_recent(limit=2)
        self.assertEqual(len(recent), 2)
        self.assertEqual(recent[0]["trace_id"], ids[-1])
        self.assertEqual(recent[1]["trace_id"], ids[-2])


if __name__ == "__main__":
    unittest.main()
