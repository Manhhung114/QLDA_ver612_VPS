from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import qlda.runtime_core.google_oauth_settings as gos
from qlda.infrastructure.google_sheets.client import GoogleSheetsClient


class GoogleOAuthAdminSettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_config_file = gos.CONFIG_FILE
        gos.CONFIG_FILE = Path(self.tmp.name) / "google_oauth.json"

    def tearDown(self):
        gos.CONFIG_FILE = self.old_config_file
        self.tmp.cleanup()

    def test_admin_settings_are_encrypted_and_applied_without_editing_env_file(self):
        env = {
            "QLDA_LOCAL_UPLOAD_SECRET": "oauth-test-master-secret-long-enough",
            "QLDA_PUBLIC_BASE_URL": "https://qldaxd.id.vn",
            "GOOGLE_OAUTH_CLIENT_ID": "",
            "GOOGLE_OAUTH_CLIENT_SECRET": "",
            "GOOGLE_OAUTH_REDIRECT_URI": "",
        }
        client_id = "1234567890-example.apps.googleusercontent.com"
        client_secret = "GOCSPX-test-secret-value"
        redirect_uri = "https://qldaxd.id.vn"

        with patch.dict(os.environ, env, clear=False):
            path = gos.save_google_oauth_settings(client_id, client_secret, redirect_uri)
            self.assertTrue(path.exists())

            raw_text = path.read_text(encoding="utf-8")
            self.assertNotIn(client_secret, raw_text)
            raw = json.loads(raw_text)
            self.assertTrue(raw.get("_encrypted_client_secret"))
            self.assertEqual(raw.get("client_id"), client_id)
            self.assertEqual(raw.get("redirect_uri"), redirect_uri)

            cfg = gos.get_google_oauth_settings()
            self.assertTrue(cfg["managed"])
            self.assertEqual(cfg["source"], "App / Admin")
            self.assertEqual(cfg["client_id"], client_id)
            self.assertEqual(cfg["client_secret"], client_secret)
            self.assertEqual(cfg["redirect_uri"], redirect_uri)

            self.assertEqual(GoogleSheetsClient.oauth_client_id(), client_id)
            self.assertEqual(GoogleSheetsClient.oauth_client_secret(), client_secret)
            self.assertEqual(GoogleSheetsClient.oauth_redirect_uri(), redirect_uri)
            self.assertTrue(GoogleSheetsClient.oauth_available())

    def test_environment_remains_backward_compatible_fallback(self):
        env = {
            "QLDA_LOCAL_UPLOAD_SECRET": "oauth-test-master-secret-long-enough",
            "GOOGLE_OAUTH_CLIENT_ID": "fallback.apps.googleusercontent.com",
            "GOOGLE_OAUTH_CLIENT_SECRET": "fallback-secret",
            "GOOGLE_OAUTH_REDIRECT_URI": "https://qldaxd.id.vn",
        }
        with patch.dict(os.environ, env, clear=False):
            cfg = gos.get_google_oauth_settings()
            self.assertFalse(cfg["managed"])
            self.assertEqual(cfg["source"], "qlda.env / Secrets")
            self.assertEqual(cfg["client_id"], env["GOOGLE_OAUTH_CLIENT_ID"])
            self.assertEqual(cfg["client_secret"], env["GOOGLE_OAUTH_CLIENT_SECRET"])

    def test_validation_requires_https_in_production(self):
        ok, message = gos.validate_google_oauth(
            "123.apps.googleusercontent.com",
            "secret",
            "http://qldaxd.id.vn",
        )
        self.assertFalse(ok)
        self.assertIn("HTTPS", message)


if __name__ == "__main__":
    unittest.main()
