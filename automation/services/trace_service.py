import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


class TraceService:
    """Persistent trace logging for workflow automation actions."""

    def __init__(self, log_file: str = "automation/logs/trace_history.jsonl"):
        self.log_path = Path(log_file)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def new_trace_id(self) -> str:
        return f"trace_{uuid.uuid4().hex}"

    def record(
        self,
        trace_id: str,
        action: str,
        stage: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        entry = {
            "trace_id": trace_id,
            "action": action,
            "stage": stage,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "metadata": metadata or {}
        }
        with open(self.log_path, "a", encoding="utf-8") as handler:
            handler.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def get_trace(self, trace_id: str) -> List[Dict[str, Any]]:
        if not self.log_path.exists():
            return []

        entries: List[Dict[str, Any]] = []
        with open(self.log_path, "r", encoding="utf-8") as handler:
            for line in handler:
                if not line.strip():
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if payload.get("trace_id") == trace_id:
                    entries.append(payload)

        entries.sort(key=lambda item: item.get("timestamp", ""))
        return entries

    def list_recent(self, limit: int = 50) -> List[Dict[str, Any]]:
        if not self.log_path.exists():
            return []

        entries: List[Dict[str, Any]] = []
        with open(self.log_path, "r", encoding="utf-8") as handler:
            for line in handler:
                if not line.strip():
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                entries.append(payload)

        return entries[-limit:][::-1]
