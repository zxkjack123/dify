import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Dict, List, Optional


class RequestMetricsRecorder:
    """Persistent store + summary helper for console API request metrics."""

    def __init__(
        self,
        log_file: str = "automation/logs/console_request_metrics.jsonl"
    ):
        self.log_path = Path(log_file)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def record(
        self,
        *,
        method: str,
        endpoint: str,
        status_code: Optional[int],
        duration_ms: float,
        trace_id: str,
        success: bool,
        error_type: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "method": method,
            "endpoint": endpoint,
            "status_code": status_code,
            "duration_ms": round(duration_ms, 3),
            "trace_id": trace_id,
            "success": success,
            "error_type": error_type,
            "metadata": metadata or {}
        }
        with self.log_path.open("a", encoding="utf-8") as handler:
            handler.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def tail(self, limit: int = 20) -> List[Dict[str, Any]]:
        if limit <= 0:
            return []
        entries = self._read_entries(limit=limit)
        return entries[-limit:]

    def summarize(self, window: Optional[int] = None) -> Dict[str, Any]:
        entries = self._read_entries(limit=window)
        if not entries:
            return {
                "total_requests": 0,
                "success_rate": 0.0,
                "avg_duration_ms": 0.0,
                "p95_duration_ms": 0.0,
                "status_distribution": {},
                "failure_types": {},
            }

        durations = [entry["duration_ms"] for entry in entries]
        total_requests = len(entries)
        successes = sum(1 for entry in entries if entry["success"])
        success_rate = round(successes / total_requests * 100, 2)
        durations_sorted = sorted(durations)
        p95_index = max(int(0.95 * len(durations_sorted)) - 1, 0)
        summary = {
            "total_requests": total_requests,
            "success_rate": success_rate,
            "avg_duration_ms": round(mean(durations), 2),
            "p95_duration_ms": round(durations_sorted[p95_index], 2),
            "status_distribution": dict(
                Counter(str(entry["status_code"]) for entry in entries)
            ),
            "failure_types": dict(
                Counter(
                    entry["error_type"]
                    for entry in entries
                    if entry["error_type"]
                )
            ),
        }
        return summary

    def _read_entries(
        self,
        limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        if not self.log_path.exists():
            return []

        entries: List[Dict[str, Any]] = []
        with self.log_path.open("r", encoding="utf-8") as handler:
            for line in handler:
                if not line.strip():
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                entries.append(payload)

        if limit is None or limit <= 0:
            return entries
        return entries[-limit:]
