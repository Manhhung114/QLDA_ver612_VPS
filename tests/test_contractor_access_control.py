from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import qlda.runtime_core.project_store as cloud_db
from qlda.runtime_core.contractor_access_control import (
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
from qlda.runtime_core.contractor_workspace import (
    add_contractor,
    ensure_default_contractor,
    install_contractor_workspace,
)


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

if __name__ == "__main__":
    unittest.main()
