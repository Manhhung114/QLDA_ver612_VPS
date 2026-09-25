from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import qlda.runtime_core.project_store as cloud_db
import qlda.runtime_core.contractor_access_control as access
from qlda.runtime_core.contractor_workspace import (
    add_contractor,
    ensure_default_contractor,
    install_contractor_workspace,
)
from qlda.runtime_core.default_workspace_admin_guard import (
    PATCH_MARKER,
    install_default_workspace_admin_guard,
)


class DefaultWorkspaceAdminGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        install_contractor_workspace()
        access.install_contractor_access_control()
        install_default_workspace_admin_guard()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = cloud_db.CloudDatabase(Path(self.tmp.name) / "admin_only.db")
        self.master = self.db.add_project(
            "DA-ADMIN-ONLY",
            "Dự án kiểm thử workspace mặc định",
            "2026-01-01",
            "2027-12-31",
            "PM",
            "",
        )
        self.default = ensure_default_contractor(self.db, self.master)
        self.ree = add_contractor(self.db, self.master, "REE", "Công ty REE")

    def tearDown(self):
        self.tmp.cleanup()

    def test_native_guard_is_installed_without_generated_source_patch(self):
        self.assertTrue(access._qlda_default_workspace_admin_guard_installed)
        self.assertEqual(access._qlda_default_workspace_admin_guard_marker, PATCH_MARKER)
        runtime = Path(__file__).resolve().parents[1] / "src/qlda/runtime_core"
        self.assertFalse((runtime / "contractor_access_patch.py").exists())

    def test_admin_sees_default_and_other_contractors(self):
        rows = access.authorized_contractor_rows(
            self.db,
            self.master,
            "admin@example.com",
            "PROJECT_MANAGEMENT",
            active_only=True,
            can_admin=True,
        )
        self.assertEqual({r["contractor_code"] for r in rows}, {"NT-01", "REE"})
        self.assertTrue(any(int(r["workspace_project_id"]) == self.master for r in rows))

    def test_non_admin_never_sees_default_workspace(self):
        for role in ("SITE_MANAGEMENT", "CONSULTANT", access.PROJECT_VIEWER, ""):
            rows = access.authorized_contractor_rows(
                self.db,
                self.master,
                f"{role or 'read'}@example.com",
                role,
                active_only=True,
                can_admin=False,
            )
            self.assertEqual([r["contractor_code"] for r in rows], ["REE"])
            self.assertTrue(all(int(r["workspace_project_id"]) != self.master for r in rows))

    def test_contractor_cannot_be_assigned_default_workspace(self):
        with self.assertRaisesRegex(ValueError, "Workspace mặc định chỉ Admin"):
            access.set_user_project_access(
                self.db,
                self.master,
                "contractor@example.com",
                access.CONTRACTOR,
                int(self.default["id"]),
            )

    def test_contractor_can_use_only_non_default_assigned_workspace(self):
        access.set_user_project_access(
            self.db,
            self.master,
            "ree@example.com",
            access.CONTRACTOR,
            int(self.ree["id"]),
        )
        rows = access.authorized_contractor_rows(
            self.db,
            self.master,
            "ree@example.com",
            access.CONTRACTOR,
            active_only=True,
            can_admin=False,
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["contractor_code"], "REE")

    def test_legacy_default_assignment_is_denied(self):
        # Simulate an old DB row created before the Admin-only rule existed.
        with self.db.connect() as connection:
            access.ensure_schema_connection(connection)
            connection.execute(
                f"""INSERT INTO {access.TABLE_NAME}(
                       master_project_id,user_email,access_mode,contractor_id,workspace_project_id,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?)""",
                (
                    self.master,
                    "legacy@example.com",
                    access.CONTRACTOR,
                    int(self.default["id"]),
                    self.master,
                    "2026-01-01 00:00:00",
                    "2026-01-01 00:00:00",
                ),
            )
        with self.assertRaisesRegex(PermissionError, "Workspace mặc định chỉ Admin"):
            access.authorized_contractor_rows(
                self.db,
                self.master,
                "legacy@example.com",
                access.CONTRACTOR,
                active_only=True,
                can_admin=False,
            )


if __name__ == "__main__":
    unittest.main()
