import os
import tempfile
import unittest

from automation.cli.main import _resolve_app_id
from automation.services.alias_service import AliasService


class TestAliasService(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store_path = os.path.join(self.temp_dir.name, "aliases.json")
        self.service = AliasService(store_path=self.store_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_set_list_and_get_alias(self):
        entry = self.service.set_alias(
            alias="prod",
            app_id="app-123",
            description="Production"
        )
        self.assertEqual(entry["app_id"], "app-123")

        aliases = self.service.list_aliases()
        self.assertEqual(len(aliases), 1)
        self.assertEqual(aliases[0]["alias"], "prod")
        self.assertEqual(aliases[0]["description"], "Production")

        fetched = self.service.get_alias("prod")
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched["app_id"], "app-123")

    def test_delete_and_resolve_alias(self):
        self.service.set_alias("staging", "app-456")
        self.assertEqual(self.service.resolve("staging"), "app-456")
        self.assertEqual(self.service.resolve("app-789"), "app-789")

        removed = self.service.delete_alias("staging")
        self.assertTrue(removed)
        self.assertFalse(self.service.delete_alias("staging"))
        self.assertEqual(self.service.list_aliases(), [])

    def test_resolve_helper_prefers_app_id(self):
        result = _resolve_app_id(self.service, "app-direct", None)
        self.assertEqual(result, "app-direct")

    def test_resolve_helper_uses_alias(self):
        self.service.set_alias("prod", "app-123")
        result = _resolve_app_id(self.service, None, "prod")
        self.assertEqual(result, "app-123")

    def test_resolve_helper_missing_inputs(self):
        with self.assertRaises(ValueError):
            _resolve_app_id(self.service, None, None)

    def test_resolve_helper_unknown_alias(self):
        with self.assertRaises(ValueError):
            _resolve_app_id(self.service, None, "unknown")


if __name__ == "__main__":
    unittest.main()
