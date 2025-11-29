import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from automation.infra.logger import logger


class SecretService:
    """Manage local workflow automation secrets with rotation metadata."""

    def __init__(
        self,
        store_path: Optional[str] = None,
        warn_threshold_days: int = 7,
        policy_path: Optional[str] = None
    ) -> None:
        base_dir = Path(__file__).resolve().parents[1] / "secrets"
        base_dir.mkdir(parents=True, exist_ok=True)
        self.store_path = (
            Path(store_path) if store_path else base_dir / "secrets.json"
        )
        self.policy_path = (
            Path(policy_path) if policy_path else base_dir / "policy.json"
        )
        self.warn_threshold_days = warn_threshold_days
        self._policy_cache: Optional[Dict[str, Any]] = None
        if not self.store_path.exists():
            self._save({})

    def list_secrets(self) -> List[Dict[str, Any]]:
        now = self._now()
        secrets = self._load()
        results: List[Dict[str, Any]] = []
        for name, meta in secrets.items():
            results.append({
                "name": name,
                "description": meta.get("description", ""),
                "created_at": meta.get("created_at"),
                "last_rotated_at": meta.get("last_rotated_at"),
                "expires_at": meta.get("expires_at"),
                "value_preview": self._mask(meta.get("value", "")),
                "rotation_pending": meta.get("rotation_pending", False),
                "status": self._status(meta, now)
            })
        return sorted(results, key=lambda item: item["name"])

    def set_secret(
        self,
        name: str,
        value: str,
        expires_in_days: Optional[int] = None,
        description: str = ""
    ) -> Dict[str, Any]:
        secrets = self._load()
        now = self._now()
        expires_in_days = self._coerce_expiry_window(name, expires_in_days)
        expires_at = None
        if expires_in_days:
            expires_at = (now + timedelta(days=expires_in_days)).isoformat()
        entry = secrets.get(name, {})
        entry.update({
            "value": value,
            "description": description,
            "created_at": entry.get("created_at", now.isoformat()),
            "last_rotated_at": now.isoformat(),
            "expires_at": expires_at or entry.get("expires_at"),
            "rotation_pending": False
        })
        secrets[name] = entry
        self._save(secrets)
        return entry

    def rotate_secret(
        self,
        name: str,
        new_value: Optional[str] = None,
        expires_in_days: Optional[int] = None
    ) -> Dict[str, Any]:
        secrets = self._load()
        if name not in secrets:
            raise ValueError(f"Secret '{name}' not found")

        now = self._now()
        expires_in_days = self._coerce_expiry_window(name, expires_in_days)
        entry = secrets[name]
        if new_value is None:
            entry["value"] = "<pending-update>"
            entry["rotation_pending"] = True
        else:
            entry["value"] = new_value
            entry["rotation_pending"] = False
        entry["last_rotated_at"] = now.isoformat()
        if expires_in_days:
            entry["expires_at"] = (
                now + timedelta(days=expires_in_days)
            ).isoformat()
        secrets[name] = entry
        self._save(secrets)
        return entry

    def validate_secrets(
        self,
        required_names: List[str],
        warn_within_days: Optional[int] = None
    ) -> Dict[str, Any]:
        secrets = self._load()
        policy = self._load_policy()
        policy_secrets = policy.get("secrets", {})
        policy_required = [
            name
            for name, meta in policy_secrets.items()
            if meta.get("required")
        ]
        warn_days_default = (
            warn_within_days
            if warn_within_days is not None
            else policy.get(
                "default_warn_within_days",
                self.warn_threshold_days
            )
        )
        target_required = required_names or policy_required
        now = self._now()

        missing = [name for name in target_required if name not in secrets]
        expiring: List[Dict[str, Any]] = []
        for name, meta in secrets.items():
            expires_at = self._parse_date(meta.get("expires_at"))
            if not expires_at:
                continue
            days_left = (expires_at - now).total_seconds() / 86400
            if days_left <= 0:
                expiring.append({
                    "name": name,
                    "status": "expired",
                    "days_left": 0
                })
            elif days_left <= self._warn_threshold_for(
                name,
                warn_days_default,
                policy_secrets
            ):
                expiring.append({
                    "name": name,
                    "status": "expiring",
                    "days_left": round(days_left, 2)
                })
        return {
            "missing": missing,
            "expiring": sorted(expiring, key=lambda item: item["name"]),
            "policy": {
                "required": sorted(policy_required),
                "default_warn_within_days": warn_days_default,
                "source": str(self.policy_path),
                "enabled": bool(policy)
            }
        }

    def get_policy_summary(self) -> Dict[str, Any]:
        policy = self._load_policy()
        entries: List[Dict[str, Any]] = []
        for name, meta in (policy.get("secrets") or {}).items():
            if not isinstance(meta, dict):
                continue
            entries.append({
                "name": name,
                "required": meta.get("required", False),
                "warn_within_days": meta.get("warn_within_days"),
                "default_ttl_days": meta.get("default_ttl_days"),
                "max_ttl_days": meta.get("max_ttl_days"),
                "scopes": meta.get("scopes", []),
                "description": meta.get("description", "")
            })
        return {
            "path": str(self.policy_path),
            "enabled": bool(policy),
            "default_warn_within_days": policy.get(
                "default_warn_within_days",
                self.warn_threshold_days
            ),
            "default_ttl_days": policy.get("default_ttl_days"),
            "secrets": sorted(entries, key=lambda item: item["name"])
        }

    def _load_policy(self) -> Dict[str, Any]:
        if self._policy_cache is not None:
            return self._policy_cache
        if not self.policy_path.exists():
            self._policy_cache = {}
            return self._policy_cache
        try:
            with open(self.policy_path, "r", encoding="utf-8") as handler:
                self._policy_cache = json.load(handler)
        except (ValueError, json.JSONDecodeError) as exc:
            logger.warning(
                "Failed to parse secret policy %s: %s",
                self.policy_path,
                exc
            )
            self._policy_cache = {}
        return self._policy_cache

    def _secret_policy(self, name: str) -> Dict[str, Any]:
        policy = self._load_policy()
        secrets_meta = policy.get("secrets") or {}
        entry = secrets_meta.get(name, {})
        return entry if isinstance(entry, dict) else {}

    def _coerce_expiry_window(
        self,
        name: str,
        requested_days: Optional[int]
    ) -> Optional[int]:
        policy = self._load_policy()
        entry = self._secret_policy(name)
        default_ttl = entry.get("default_ttl_days")
        if default_ttl is None:
            default_ttl = policy.get("default_ttl_days")
        expires_in_days = (
            requested_days if requested_days is not None else default_ttl
        )
        max_ttl = entry.get("max_ttl_days")
        if (
            expires_in_days is not None
            and max_ttl is not None
            and expires_in_days > max_ttl
        ):
            raise ValueError(
                f"Secret '{name}' expiry ({expires_in_days} days) exceeds "
                f"policy max ({max_ttl} days)."
            )
        return expires_in_days

    def _warn_threshold_for(
        self,
        name: str,
        fallback: int,
        policy_secrets: Dict[str, Dict[str, Any]]
    ) -> int:
        entry = policy_secrets.get(name)
        if not isinstance(entry, dict):
            return fallback
        return entry.get("warn_within_days", fallback)

    def _status(self, meta: Dict[str, Any], now: datetime) -> str:
        if meta.get("rotation_pending"):
            return "rotation_pending"
        expires_at = self._parse_date(meta.get("expires_at"))
        if not expires_at:
            return "active"
        if expires_at <= now:
            return "expired"
        days_left = (expires_at - now).days
        if days_left <= self.warn_threshold_days:
            return "expiring"
        return "active"

    def _mask(self, value: str) -> str:
        if not value:
            return ""
        if len(value) <= 4:
            return "*" * len(value)
        return f"{value[:2]}{'*' * (len(value) - 4)}{value[-2:]}"

    def _load(self) -> Dict[str, Any]:
        try:
            with open(self.store_path, "r", encoding="utf-8") as handler:
                return json.load(handler)
        except FileNotFoundError:
            return {}

    def _save(self, data: Dict[str, Any]) -> None:
        with open(self.store_path, "w", encoding="utf-8") as handler:
            json.dump(data, handler, indent=2, ensure_ascii=False)

    def _parse_date(self, value: Optional[str]) -> Optional[datetime]:
        if not value:
            return None
        try:
            normalized = value.replace("Z", "+00:00")
            return datetime.fromisoformat(normalized)
        except ValueError:
            return None

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)
