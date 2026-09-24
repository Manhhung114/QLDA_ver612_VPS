from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from qlda.application.google_sheets.service import parse_sheet_gid, parse_spreadsheet_id
from qlda.infrastructure.google_sheets.client import (
    GoogleSheetsClient,
    GoogleSheetsConfigError,
    make_oauth_state,
    verify_oauth_state,
)


class _Response:
    def __init__(self, text: str, *, status_code: int = 200, url: str = "https://docs.google.com/x", content_type: str = "text/csv"):
        self.text = text
        self.status_code = status_code
        self.url = url
        self.headers = {"content-type": content_type}


class GoogleSheetsAccessTests(unittest.TestCase):
    def test_parse_sheet_link_and_gid(self):
        url = "https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz123456789/edit#gid=987654321"
        self.assertEqual(parse_spreadsheet_id(url), "1AbCdEfGhIjKlMnOpQrStUvWxYz123456789")
        self.assertEqual(parse_sheet_gid(url), 987654321)
        self.assertEqual(parse_sheet_gid("1AbCdEfGhIjKlMnOpQrStUvWxYz123456789"), 0)

    @patch("qlda.infrastructure.google_sheets.client.requests.get")
    def test_public_values_requires_no_credentials(self, mocked_get):
        mocked_get.return_value = _Response("Cong tac,Zone 1\nOng gio,50%\n")
        rows = GoogleSheetsClient.public_values("1AbCdEfGhIjKlMnOpQrStUvWxYz123456789", 12)
        self.assertEqual(rows[0], ["Cong tac", "Zone 1"])
        self.assertEqual(rows[1], ["Ong gio", "50%"])
        kwargs = mocked_get.call_args.kwargs
        self.assertEqual(kwargs["params"]["gid"], 12)
        self.assertNotIn("Authorization", kwargs["headers"])

    @patch("qlda.infrastructure.google_sheets.client.requests.get")
    def test_public_values_falls_back_from_gviz_401_to_export(self, mocked_get):
        mocked_get.side_effect = [
            _Response("Unauthorized", status_code=401),
            _Response("Cong tac,Zone 1\nOng gio,75%\n"),
        ]
        rows = GoogleSheetsClient.public_values("1AbCdEfGhIjKlMnOpQrStUvWxYz123456789", 44)
        self.assertEqual(rows[1], ["Ong gio", "75%"])
        self.assertEqual(mocked_get.call_count, 2)
        second_url = mocked_get.call_args_list[1].args[0]
        self.assertTrue(second_url.endswith("/export"))
        self.assertEqual(mocked_get.call_args_list[1].kwargs["params"]["gid"], 44)

    @patch("qlda.infrastructure.google_sheets.client.requests.get")
    def test_public_values_explains_private_sheet(self, mocked_get):
        mocked_get.return_value = _Response(
            "<html>Sign in</html>",
            url="https://accounts.google.com/signin",
            content_type="text/html; charset=utf-8",
        )
        with self.assertRaisesRegex(GoogleSheetsConfigError, "Anyone with the link"):
            GoogleSheetsClient.public_values("1AbCdEfGhIjKlMnOpQrStUvWxYz123456789", 0)
        self.assertEqual(mocked_get.call_count, 2)

    def test_oauth_state_is_signed_and_actor_bound(self):
        env = {
            "QLDA_LOCAL_UPLOAD_SECRET": "test-secret-that-is-long-enough-for-state-signing",
            "GOOGLE_OAUTH_CLIENT_ID": "client.apps.googleusercontent.com",
            "GOOGLE_OAUTH_CLIENT_SECRET": "client-secret",
            "GOOGLE_OAUTH_REDIRECT_URI": "https://qldaxd.id.vn",
        }
        with patch.dict(os.environ, env, clear=False):
            state = make_oauth_state(42, "admin@example.com")
            payload = verify_oauth_state(state, "admin@example.com")
            self.assertEqual(payload["p"], 42)
            with self.assertRaisesRegex(GoogleSheetsConfigError, "không khớp"):
                verify_oauth_state(state, "other@example.com")

            url = GoogleSheetsClient.build_authorization_url(state)
            self.assertIn("accounts.google.com/o/oauth2/v2/auth", url)
            self.assertIn("spreadsheets.readonly", url)
            self.assertIn("redirect_uri=https%3A%2F%2Fqldaxd.id.vn", url)


if __name__ == "__main__":
    unittest.main()
