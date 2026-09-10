from __future__ import annotations

import unittest
from pathlib import Path

import single_session_v622 as ss
from v622_single_session_patch import PATCH_MARKER, patch_single_session


class SingleSessionTests(unittest.TestCase):
    def test_token_hash_is_stable_and_not_raw_token(self):
        token = "example-session-token"
        digest = ss._token_hash(token)
        self.assertEqual(len(digest), 64)
        self.assertNotEqual(digest, token)
        self.assertEqual(digest, ss._token_hash(token))

    def test_local_backend_contains_atomic_replace_and_upload_binding(self):
        text = Path("single_session_v622.py").read_text(encoding="utf-8")
        self.assertIn("FOR UPDATE", text)
        self.assertIn('DELETE FROM qlda_local_sessions WHERE email=%s', text)
        self.assertIn('payload["session_hash"] = _token_hash(token)', text)
        self.assertIn("Phiên đăng nhập tạo link upload đã bị thay thế", text)
        self.assertIn('if action == "force_logout"', text)

    def test_apps_script_patch_contains_same_security_invariants(self):
        text = Path("google_drive_appscript/SingleSession_V622.gs").read_text(encoding="utf-8")
        self.assertIn("active_session_id", text)
        self.assertIn("LOGIN_SINGLE_SESSION", text)
        self.assertIn("action === 'list_sessions'", text)
        self.assertIn("action === 'force_logout'", text)
        self.assertIn("ticketStarted + 1000 < activeStarted", text)
        self.assertIn("uploadStarted + 1000 < activeStarted", text)

    def test_ui_patch_adds_admin_session_management(self):
        source = '''
def _gateway_logout() -> None:
    holder = st.session_state.pop("_qlda_drive_gateway_instance", None)
    try:
        if isinstance(holder, DriveGateway):
            holder.close()
        elif isinstance(holder, tuple) and len(holder) == 2:
            holder[1].close()
    except Exception:
        pass
    for key in ("qlda_drive_session_token", "qlda_drive_identity", "qlda_drive_error", "qlda_auth_restored_from_cookie", "_qlda_cookie_written_for_token"):
        st.session_state.pop(key, None)
    st.session_state["qlda_ignore_persistent_auth"] = True
    _clear_browser_session_cookie()


def _cloud_identity(refresh: bool = False):
    try:
        pass
    except Exception as exc:
        st.session_state["qlda_drive_error"] = str(exc)
        _gateway_logout()
        return {"role": "unknown", "email": "", "name": "", "label": "Chưa đăng nhập"}


def auth():
    if not _gateway_session_token():
        st.title("🏗️ QLDA Xây dựng V6.22 PostgreSQL Cloud")
        try:
            result = gw.login(email, password)
            token = str(result.get("session_token") or "")
            st.session_state["qlda_drive_session_token"] = token
            st.session_state.pop("qlda_drive_identity", None)
            st.session_state.pop("qlda_ignore_persistent_auth", None)
        except Exception:
            pass


def settings():
    if True:
        if True:
            if True:
                if users:
                    pass
                else:
                    st.info("Chỉ Admin mới được quản lý tài khoản và phân quyền.")
'''
        patched = patch_single_session(source)
        self.assertIn(PATCH_MARKER, patched)
        self.assertIn("gw.list_sessions(token)", patched)
        self.assertIn("gw.force_logout(token, _session_target)", patched)
        self.assertIn("gateway.logout(token)", patched)
        self.assertIn("client_info=_client_info", patched)
        compile(patched, "single_session_ui_test.py", "exec")


if __name__ == "__main__":
    unittest.main()
