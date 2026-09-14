from __future__ import annotations

import unittest
from pathlib import Path

from qlda.runtime_core import single_session as ss

ROOT = Path(__file__).resolve().parents[1]


class SingleSessionTests(unittest.TestCase):
    def test_token_hash_is_stable_and_not_raw_token(self):
        token = "example-session-token"
        digest = ss._token_hash(token)
        self.assertEqual(len(digest), 64)
        self.assertNotEqual(digest, token)
        self.assertEqual(digest, ss._token_hash(token))

    def test_packaged_backend_contains_atomic_replace_upload_binding_and_history10(self):
        text = (ROOT / "src/qlda/runtime_core/single_session.py").read_text(encoding="utf-8")
        self.assertEqual(ss.SESSION_HISTORY_LIMIT, 10)
        self.assertIn("FOR UPDATE", text)
        self.assertIn('DELETE FROM qlda_local_sessions WHERE email=%s', text)
        self.assertIn('payload["session_hash"] = _token_hash(token)', text)
        self.assertIn("Phiên đăng nhập tạo link upload đã bị thay thế", text)
        self.assertIn("qlda_local_session_history", text)
        self.assertIn("LIMIT %s", text)
        self.assertIn('if action == "list_session_history"', text)
        self.assertIn('if action == "force_logout"', text)
        self.assertIn('"REPLACED"', text)
        self.assertIn('"ADMIN_FORCE_LOGOUT"', text)

    def test_apps_script_patch_contains_same_security_invariants(self):
        text = (ROOT / "google_drive_appscript/SingleSession_V622.gs").read_text(encoding="utf-8")
        self.assertIn("active_session_id", text)
        self.assertIn("LOGIN_SINGLE_SESSION", text)
        self.assertIn("action === 'list_sessions'", text)
        self.assertIn("action === 'force_logout'", text)
        self.assertIn("session_id: sid", text)
        self.assertIn("ticketSid !== activeSid", text)
        self.assertIn("state.session_id = String(meta.session_id", text)
        self.assertIn("uploadSid !== activeSid", text)
        self.assertIn("ticket_version: 3", text)

    def test_source_controlled_ui_keeps_admin_session_history(self):
        source = (ROOT / "src/qlda/presentation/streamlit/app.py").read_text(encoding="utf-8")
        self.assertIn("gw.list_sessions(token)", source)
        self.assertIn("gw.list_session_history(token)", source)
        self.assertIn("gw.force_logout(token, _session_target)", source)
        self.assertIn("gateway.logout(token)", source)
        self.assertIn("client_info=_client_info", source)
        self.assertIn("_v622_vn_time", source)
        self.assertIn("Asia/Ho_Chi_Minh", source)
        self.assertIn("10 lần đăng nhập gần nhất", source)
        self.assertIn('strftime("%d/%m/%Y %H:%M:%S")', source)
        compile(source, "qlda_v76_streamlit.py", "exec")


if __name__ == "__main__":
    unittest.main()
