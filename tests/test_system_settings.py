from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import qlda.runtime_core.settings_store as ss
import qlda.runtime_core.system_settings as syscfg


class SystemSettingsV622Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.old_settings = ss.APP_SETTINGS_FILE
        self.old_audit = ss.AUDIT_FILE
        ss.APP_SETTINGS_FILE = root / "app_settings.json"
        ss.AUDIT_FILE = root / "audit.jsonl"
        self.env_patch = patch.dict(
            os.environ,
            {
                "QLDA_SETTINGS_MASTER_KEY": "test-master-key-that-is-long-and-private-123456789",
                "QLDA_APP_SETTINGS_FILE": str(ss.APP_SETTINGS_FILE),
                "OPENAI_API_KEY": "env-openai-key",
                "GEMINI_API_KEY": "env-gemini-key",
                "OPENAI_MODEL": "env-openai-model",
                "GEMINI_MODEL": "env-gemini-model",
                "QLDA_CPU_WORKERS": "1",
                "QLDA_LOCAL_DIRECT_MAX_UPLOAD_MB": "100",
            },
            clear=False,
        )
        self.env_patch.start()

    def tearDown(self):
        self.env_patch.stop()
        ss.APP_SETTINGS_FILE = self.old_settings
        ss.AUDIT_FILE = self.old_audit
        self.tmp.cleanup()

    def test_api_keys_are_encrypted_at_rest_and_roundtrip(self):
        self.assertTrue(ss.encryption_available())
        ss.save_app_settings(
            {
                "managed_ai": True,
                "ai_provider": "openai",
                "openai_api_key": "sk-admin-secret-value",
                "gemini_api_key": "gemini-admin-secret-value",
                "openai_model": "gpt-admin-model",
            }
        )
        raw = ss.APP_SETTINGS_FILE.read_text(encoding="utf-8")
        self.assertNotIn("sk-admin-secret-value", raw)
        self.assertNotIn("gemini-admin-secret-value", raw)
        self.assertIn("_encrypted_secrets", raw)
        cfg = ss.load_app_settings()
        self.assertEqual(cfg["openai_api_key"], "sk-admin-secret-value")
        self.assertEqual(cfg["gemini_api_key"], "gemini-admin-secret-value")
        self.assertEqual(ss.get_ai_runtime_settings()["model"], "gpt-admin-model")

    def test_managed_values_override_env_and_can_return_to_env(self):
        ss.save_app_settings(
            {
                "managed_performance": True,
                "cpu_workers": 3,
                "managed_storage": True,
                "local_direct_max_upload_mb": 2048,
            }
        )
        self.assertEqual(ss.get_runtime_value("QLDA_CPU_WORKERS", "2"), "3")
        self.assertEqual(ss.get_runtime_value("QLDA_LOCAL_DIRECT_MAX_UPLOAD_MB", "200"), "2048")

        ss.save_app_settings({"managed_performance": False, "managed_storage": False})
        self.assertEqual(ss.get_runtime_value("QLDA_CPU_WORKERS", "2"), "1")
        self.assertEqual(ss.get_runtime_value("QLDA_LOCAL_DIRECT_MAX_UPLOAD_MB", "200"), "100")

    def test_managed_ai_overrides_environment_then_falls_back(self):
        ss.save_app_settings(
            {
                "managed_ai": True,
                "ai_provider": "gemini",
                "gemini_api_key": "admin-gemini-key",
                "gemini_model": "admin-gemini-model",
            }
        )
        managed = ss.get_ai_runtime_settings()
        self.assertEqual(managed["provider"], "gemini")
        self.assertEqual(managed["api_key"], "admin-gemini-key")
        self.assertEqual(managed["model"], "admin-gemini-model")

        ss.save_app_settings({"managed_ai": False})
        fallback = ss.get_ai_runtime_settings()
        self.assertEqual(fallback["provider"], "gemini")
        self.assertEqual(fallback["api_key"], "env-gemini-key")
        self.assertEqual(fallback["model"], "env-gemini-model")

    def test_native_provider_settings_read_admin_managed_settings_without_runtime_bridge(self):
        ss.save_app_settings(
            {
                "managed_ai": True,
                "ai_provider": "openai",
                "openai_api_key": "admin-openai-key",
                "openai_model": "admin-openai-model",
                "openai_web_search": True,
            }
        )
        from qlda.infrastructure.ai.provider_settings import get_provider_settings
        import qlda.runtime_core.runtime_settings_bridge as bridge

        value = get_provider_settings()
        self.assertEqual(value["api_key"], "admin-openai-key")
        self.assertEqual(value["model"], "admin-openai-model")
        self.assertTrue(value["use_web"])
        self.assertFalse(bridge.runtime_bridge_status()["ai"])

    def test_native_provider_settings_preserve_explicit_gemini_25_flash(self):
        from qlda.infrastructure.ai.provider_settings import get_provider_settings

        ss.save_app_settings(
            {
                "managed_ai": True,
                "ai_provider": "gemini",
                "gemini_api_key": "admin-gemini-key",
                "gemini_model": "gemini-2.5-flash",
            }
        )
        managed = get_provider_settings("gemini")
        self.assertEqual(managed["model"], "gemini-2.5-flash")

        ss.save_app_settings({"managed_ai": False})
        with patch.dict(os.environ, {"GEMINI_MODEL": "models/gemini-2.5-flash"}, clear=False):
            runtime = get_provider_settings("gemini")
        self.assertEqual(runtime["model"], "gemini-2.5-flash")

    def test_gemini_human_label_is_normalized_without_switching_model(self):
        from qlda.infrastructure.ai.provider_settings import normalize_gemini_model

        self.assertEqual(normalize_gemini_model("Gemini 3.5 Flash"), "gemini-3.5-flash")
        self.assertEqual(normalize_gemini_model("models/gemini-3.5-flash"), "gemini-3.5-flash")
        self.assertEqual(syscfg._canonical_gemini_model("Gemini 3.5 Flash"), "gemini-3.5-flash")

    def test_auto_legacy_value_resolves_to_fixed_default(self):
        from qlda.infrastructure.ai.provider_settings import normalize_gemini_model

        self.assertEqual(normalize_gemini_model("auto"), "gemini-3.8-flash")
        self.assertEqual(syscfg._canonical_gemini_model("auto"), "gemini-3.8-flash")

    def test_audit_contains_metadata_not_values(self):
        ss.append_settings_audit("admin@example.com", "update_ai", ["openai_api_key", "ai_provider"])
        raw = ss.AUDIT_FILE.read_text(encoding="utf-8")
        self.assertIn("admin@example.com", raw)
        self.assertIn("openai_api_key", raw)
        self.assertNotIn("sk-admin-secret-value", raw)
        rows = ss.read_settings_audit()
        self.assertEqual(rows[0]["action"], "update_ai")

    def test_storage_path_guard(self):
        with patch.object(syscfg, "_ALLOWED_STORAGE_ROOT", Path("/opt/qlda")):
            self.assertEqual(str(syscfg._safe_storage_path("/opt/qlda/data")), "/opt/qlda/data")
            with self.assertRaises(ValueError):
                syscfg._safe_storage_path("/etc/qlda")
            with self.assertRaises(ValueError):
                syscfg._safe_storage_path("relative/data")

    def test_settings_file_mode_is_private(self):
        ss.save_app_settings({"managed_ai": False})
        mode = ss.APP_SETTINGS_FILE.stat().st_mode & 0o777
        self.assertEqual(mode, 0o600)


if __name__ == "__main__":
    unittest.main()
