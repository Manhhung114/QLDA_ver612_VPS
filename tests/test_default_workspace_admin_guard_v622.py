from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import cloud_db
import contractor_access_control_v622 as access
from contractor_workspace_v622 import (
    add_contractor,
    ensure_default_contractor,
    install_contractor_workspace,
)
from default_workspace_admin_guard_v622 import (
    PATCH_MARKER,
    install_default_workspace_admin_guard,
    patch_generated_source_admin_only,
)
from v622_contractor_workspace_patch import patch_contractor_workspace
import v622_contractor_access_patch as access_patch


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

    def test_generated_ui_hides_default_and_scopes_non_admin_ai(self):
        source = Path("dist/streamlit_app.py").read_text(encoding="utf-8")
        patched = patch_contractor_workspace(source)
        # installer wraps access_patch.patch_contractor_access in this test process
        patched = access_patch.patch_contractor_access(patched)
        if PATCH_MARKER not in patched:
            patched = patch_generated_source_admin_only(patched)
        self.assertIn(PATCH_MARKER, patched)
        self.assertIn("_v622_is_admin_user = bool(_is_admin())", patched)
        self.assertIn(
            "render_ai_assistant(_master_pid if _v622_is_admin_user else pid)",
            patched,
        )
        self.assertIn("int(pid) if not bool(_is_admin()) else None", patched)
        self.assertIn("Workspace mặc định chỉ Admin", patched)
        self.assertIn("_v622_project_contractors_all", patched)
        self.assertIn('x.get("is_default")', patched)
        compile(patched, "default_workspace_admin_only_ui.py", "exec")


if __name__ == "__main__":
    unittest.main()
