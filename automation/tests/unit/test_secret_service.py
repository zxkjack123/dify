import json
import os
import tempfile
import unittest
from typing import Optional

from automation.services.secret_service import SecretService


class TestSecretService(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store_path = os.path.join(self.temp_dir.name, "secrets.json")
        self.policy_path = os.path.join(self.temp_dir.name, "policy.json")
        self.service = self._new_service()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _new_service(self, policy: Optional[dict] = None) -> SecretService:
        if os.path.exists(self.store_path):
            os.remove(self.store_path)
        if policy is None:
            if os.path.exists(self.policy_path):
                os.remove(self.policy_path)
        else:
            with open(self.policy_path, "w", encoding="utf-8") as handler:
                json.dump(policy, handler)
        return SecretService(
            store_path=self.store_path,
            warn_threshold_days=5,
            policy_path=self.policy_path
        )

    def test_set_and_list_secret(self):
        self.service.set_secret("api_key", "abcd1234", expires_in_days=30)
        secrets = self.service.list_secrets()
        self.assertEqual(len(secrets), 1)
        self.assertEqual(secrets[0]["name"], "api_key")
        self.assertEqual(secrets[0]["status"], "active")
        self.assertTrue(secrets[0]["value_preview"].startswith("ab"))

    def test_rotate_secret_pending(self):
        self.service.set_secret("db_password", "topsecret")
        self.service.rotate_secret("db_password")
        secrets = self.service.list_secrets()
        self.assertEqual(secrets[0]["status"], "rotation_pending")

    def test_validate_missing_and_expiring(self):
        # create expiring secret by mocking _now
        future_store = SecretService(
            store_path=self.store_path,
            warn_threshold_days=10
        )
        future_store.set_secret("token", "value", expires_in_days=1)
        result = future_store.validate_secrets(
            ["token", "missing"], warn_within_days=5
        )
        self.assertIn("missing", result["missing"])
        self.assertEqual(result["expiring"][0]["name"], "token")

    def test_policy_enforces_defaults_and_max_ttl(self):
        policy = {
            "default_ttl_days": 30,
            "secrets": {
                "api_key": {
                    "default_ttl_days": 15,
                    "max_ttl_days": 20
                }
            }
        }
        self.service = self._new_service(policy=policy)
        self.service.set_secret("api_key", "value")
        secrets = self.service.list_secrets()
        self.assertIsNotNone(secrets[0]["expires_at"])
        with self.assertRaises(ValueError):
            self.service.set_secret("api_key", "value", expires_in_days=25)

    def test_policy_used_for_validation_defaults(self):
        policy = {
            "default_warn_within_days": 9,
            "secrets": {
                "token": {
                    "required": True,
                    "warn_within_days": 2
                },
                "db": {"required": True}
            }
        }
        self.service = self._new_service(policy=policy)
        self.service.set_secret("token", "value", expires_in_days=1)
        result = self.service.validate_secrets([], warn_within_days=None)
        self.assertIn("db", result["missing"])
        self.assertEqual(result["expiring"][0]["name"], "token")
        self.assertTrue(result["policy"]["enabled"])

    def test_get_policy_summary_without_file(self):
        self.service = self._new_service(policy=None)
        summary = self.service.get_policy_summary()
        self.assertFalse(summary["enabled"])
        self.assertEqual(summary["secrets"], [])


if __name__ == "__main__":
    unittest.main()
