import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


class AliasService:
    """Manage local alias mappings for workflow apps."""

    def __init__(self, store_path: Optional[str] = None) -> None:
        if store_path:
            self.store_path = Path(store_path)
            self.store_path.parent.mkdir(parents=True, exist_ok=True)
        else:
            base_dir = Path(__file__).resolve().parents[1] / "aliases"
            base_dir.mkdir(parents=True, exist_ok=True)
            self.store_path = base_dir / "aliases.json"
        if not self.store_path.exists():
            self._save({})

    def list_aliases(self) -> List[Dict[str, Any]]:
        entries = self._load()
        result = []
        for name, meta in entries.items():
            result.append({
                "alias": name,
                "app_id": meta.get("app_id"),
                "description": meta.get("description", ""),
                "created_at": meta.get("created_at"),
                "updated_at": meta.get("updated_at")
            })
        return sorted(result, key=lambda item: item["alias"])

    def get_alias(self, alias: str) -> Optional[Dict[str, Any]]:
        return self._load().get(alias)

    def set_alias(
        self,
        alias: str,
        app_id: str,
        description: str = "",
        metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        alias = alias.strip()
        if not alias:
            raise ValueError("Alias name cannot be empty")
        if not app_id:
            raise ValueError("App ID cannot be empty")

        entries = self._load()
        now = datetime.now(timezone.utc).isoformat()
        entry = entries.get(alias, {})
        entry.update({
            "app_id": app_id,
            "description": description,
            "metadata": metadata or entry.get("metadata", {}),
            "created_at": entry.get("created_at", now),
            "updated_at": now
        })
        entries[alias] = entry
        self._save(entries)
        return entry

    def delete_alias(self, alias: str) -> bool:
        entries = self._load()
        if alias in entries:
            entries.pop(alias)
            self._save(entries)
            return True
        return False

    def resolve(self, alias_or_id: str) -> str:
        """Return app_id if alias exists; otherwise return original string."""
        entry = self.get_alias(alias_or_id)
        if entry:
            return entry["app_id"]
        return alias_or_id

    def _load(self) -> Dict[str, Any]:
        try:
            with open(self.store_path, "r", encoding="utf-8") as handler:
                return json.load(handler)
        except FileNotFoundError:
            return {}

    def _save(self, data: Dict[str, Any]) -> None:
        with open(self.store_path, "w", encoding="utf-8") as handler:
            json.dump(data, handler, indent=2, ensure_ascii=False)
