import json
import os
from typing import List, Dict, Any
from automation.infra.logger import logger


class AuditService:
    def __init__(self, log_file: str = "automation/logs/runs_history.jsonl"):
        self.log_file = log_file

    def get_logs(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get recent execution logs, reverse chronological order.
        """
        if not os.path.exists(self.log_file):
            return []

        logs = []
        try:
            with open(self.log_file, "r") as f:
                for line in f:
                    if line.strip():
                        try:
                            logs.append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
        except Exception as e:
            logger.error(f"Failed to read audit logs: {str(e)}")
            return []

        # Return last N logs, reversed
        return logs[-limit:][::-1]

    def get_stats(self) -> Dict[str, Any]:
        """
        Calculate aggregate statistics from all logs.
        """
        logs = self.get_logs(limit=10000)  # Read all (with a reasonable limit)
        if not logs:
            return {
                "total_runs": 0,
                "success_rate": 0.0,
                "avg_duration": 0.0,
                "total_tokens": 0
            }

        total_runs = len(logs)
        successful_runs = sum(
            1 for log in logs if log.get("status") == "succeeded"
        )
        total_duration = sum(log.get("duration", 0) for log in logs)
        total_tokens = sum(log.get("total_tokens", 0) for log in logs)

        return {
            "total_runs": total_runs,
            "success_rate": round(successful_runs / total_runs * 100, 2),
            "avg_duration": round(total_duration / total_runs, 4),
            "total_tokens": total_tokens
        }
