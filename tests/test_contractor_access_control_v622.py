from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import cloud_db
from contractor_access_control_v622 import (
    CONTRACTOR,
    PROJECT_VIEWER,
    authorized_contractor_rows,
    effective_user_classification,
    get_user_project_access,
    install_contractor_access_control,
    set_user_project_access,
    user_can_view_all_contractors,
    user_contractor_scope_label,
)
from contractor_workspace_v622 import (
    add_contractor,
    ensure_default_contractor,
    install_contractor_workspace,
)
from v622_contractor_access_patch import PATCH_MARKER, patch_contractor_access
from v622_contractor_workspace_patch import patch_contractor_workspace


class ContractorAccessControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        install_contractor_workspace()
        install_contractor_access_control()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = cloud_db.CloudDatabase(Path(self.tmp.name) / "access.db")
        self.master = self.db.add_project(
            "DA-ACCESS", "Dự án kiểm thử phân quyền", "2026-01-01", "2027-12-31", "PM", ""
        )
        self.default = ensure_default_contractor(self.db, self.master)
        self.ree = add_contractor(self.db, self.master, "REE", "Công ty REE")

    def tearDown(self):
        self.tmp.cleanup()

    def test_contractor_account_sees_only_assigned_workspace(self):
        set_user_project_access(
            self.db, self.master, "ree.user@example.com", CONTRACTOR, int(self.ree["id"])
        )
        rows = authorized_contractor_rows(
            self.db, self.master, "ree.user@example.com", CONTRACTOR, active_only=True
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["contractor_code"], "REE")
        self.assertEqual(int(rows[0]["workspace_project_id"]), int(self.ree["workspace_project_id"]))
        self.assertFalse(user_can_view_all_contractors(CONTRACTOR))

    def test_unassigned_contractor_is_blocked_when_project_has_multiple_contractors(self):
        with self.assertRaises(PermissionError):
            authorized_contractor_rows(
                self.db, self.master, "not-assigned@example.com", CONTRACTOR, active_only=True
            )

    def test_project_viewer_is_read_all_scope(self):
        access = set_user_project_access(
            self.db, self.master, "viewer@example.com", PROJECT_VIEWER, None
        )
        self.assertEqual(access["access_mode"], PROJECT_VIEWER)
        self.assertEqual(
            effective_user_classification(self.db, self.master, "viewer@example.com", ""),
            PROJECT_VIEWER,
        )
        self.assertEqual(
            user_contractor_scope_label(self.db, self.master, "viewer@example.com", ""),
            "Tất cả nhà thầu • chỉ xem",
        )
        rows = authorized_contractor_rows(
            self.db, self.master, "viewer@example.com", "", active_only=True
        )
        self.assertEqual({r["contractor_code"] for r in rows}, {"NT-01", "REE"})
        self.assertTrue(user_can_view_all_contractors(PROJECT_VIEWER))

    def test_assignment_is_project_specific(self):
        set_user_project_access(
            self.db, self.master, "ree.user@example.com", CONTRACTOR, int(self.ree["id"])
        )
        row = get_user_project_access(self.db, self.master, "ree.user@example.com")
        self.assertEqual(int(row["contractor_id"]), int(self.ree["id"]))
        self.assertEqual(row["access_mode"], CONTRACTOR)

    def test_generated_ui_enforces_roles_and_hides_other_contractors(self):
        source = Path("dist/streamlit_app.py").read_text(encoding="utf-8")
        patched = patch_contractor_workspace(source)
        patched = patch_contractor_access(patched)
        self.assertIn(PATCH_MARKER, patched)
        self.assertIn("Chỉ xem toàn bộ nhà thầu", patched)
        self.assertIn("Nhà thầu được phép", patched)
        self.assertIn("approval_role=_v622_approval_role", patched)
        self.assertIn("else \"update\" if papproval == \"CONTRACTOR\"", patched)
        self.assertIn("render_ai_assistant(_master_pid if _v622_can_view_all_contractors else pid)", patched)
        self.assertIn("_v622_set_ai_workspace_scope", patched)
        self.assertIn("if _v622_can_view_all_contractors else []", patched)
        self.assertIn("render_settings(_master_pid)", patched)
        compile(patched, "contractor_access_ui_test.py", "exec")


if __name__ == "__main__":
    unittest.main()
