from __future__ import annotations

import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import qlda.runtime_core.contractor_access_control as access
import qlda.runtime_core.project_store as cloud_db
from qlda.presentation.streamlit import legacy_ai_streaming_contract as ai_contract
from qlda.runtime_core.contractor_workspace import (
    add_contractor,
    ensure_default_contractor,
    install_contractor_workspace,
)
from qlda.runtime_core.default_workspace_admin_guard import install_default_workspace_admin_guard


class _Sidebar:
    def __init__(self, owner):
        self.owner = owner
        self.selectbox_calls = 0

    def markdown(self, *args, **kwargs):
        return None

    def caption(self, *args, **kwargs):
        return None

    def error(self, *args, **kwargs):
        self.owner.errors.append(str(args[0] if args else ""))

    def warning(self, *args, **kwargs):
        self.owner.warnings.append(str(args[0] if args else ""))

    def selectbox(self, _label, options, *, index=0, **_kwargs):
        self.selectbox_calls += 1
        return list(options)[index]


class _FakeStreamlit(types.ModuleType):
    def __init__(self):
        super().__init__("streamlit")
        self.session_state = {}
        self.errors = []
        self.warnings = []
        self.sidebar = _Sidebar(self)

    def error(self, *args, **kwargs):
        self.errors.append(str(args[0] if args else ""))

    def warning(self, *args, **kwargs):
        self.warnings.append(str(args[0] if args else ""))

    def stop(self):
        raise RuntimeError("streamlit.stop")


class ProjectWorkspaceScopeGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        install_contractor_workspace()
        access.install_contractor_access_control()
        install_default_workspace_admin_guard()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = cloud_db.CloudDatabase(Path(self.tmp.name) / "workspace_scope.db")
        self.master = self.db.add_project(
            "E3-SCOPE",
            "SunShine Sky City scope test",
            "2026-01-01",
            "2027-12-31",
            "PM",
            "",
        )
        self.default = ensure_default_contractor(self.db, self.master)
        self.sigma = add_contractor(
            self.db,
            self.master,
            "SIGMA",
            "CÔNG TY CỔ PHẦN KỸ THUẬT SIGMA",
        )
        self.other = add_contractor(self.db, self.master, "OTHER", "Nhà thầu khác")
        self.email = "sigma.user@example.com"
        access.set_user_project_access(
            self.db,
            self.master,
            self.email,
            access.CONTRACTOR,
            int(self.sigma["id"]),
        )

    def tearDown(self):
        access.set_ai_workspace_scope(None)
        self.tmp.cleanup()

    def test_db_contractor_assignment_overrides_stale_management_gateway_role(self):
        self.assertEqual(
            access.effective_user_classification(
                self.db,
                self.master,
                self.email,
                "SITE_MANAGEMENT",
            ),
            access.CONTRACTOR,
        )
        rows = access.authorized_contractor_rows(
            self.db,
            self.master,
            self.email,
            "SITE_MANAGEMENT",
            active_only=True,
            can_admin=False,
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(int(rows[0]["workspace_project_id"]), int(self.sigma["workspace_project_id"]))
        self.assertNotEqual(int(rows[0]["workspace_project_id"]), int(self.master))
        self.assertEqual(rows[0]["contractor_code"], "SIGMA")

    def test_selector_publishes_and_pins_only_assigned_workspace(self):
        fake = _FakeStreamlit()
        fake.session_state["qlda_drive_identity"] = {
            "email": self.email,
            "role": "update",
            # Deliberately stale/wrong gateway role. DB assignment must win.
            "approval_role": "SITE_MANAGEMENT",
        }
        with patch.dict(sys.modules, {"streamlit": fake}):
            workspace_id, info = access.render_authorized_contractor_selector(
                self.db,
                self.master,
                can_admin=False,
                current_user=self.email,
                approval_role="SITE_MANAGEMENT",
            )

            self.assertEqual(workspace_id, int(self.sigma["workspace_project_id"]))
            self.assertEqual(info["contractor_code"], "SIGMA")
            self.assertEqual(fake.sidebar.selectbox_calls, 0)
            self.assertEqual(
                fake.session_state["qlda_active_workspace_project_id"],
                int(self.sigma["workspace_project_id"]),
            )
            self.assertEqual(fake.session_state["qlda_active_master_project_id"], self.master)
            self.assertEqual(fake.session_state["qlda_effective_approval_role"], access.CONTRACTOR)
            self.assertEqual(access.current_ai_workspace_scope(), int(self.sigma["workspace_project_id"]))

            resolved, role = ai_contract._active_scope(self.master)
            self.assertEqual(resolved, int(self.sigma["workspace_project_id"]))
            self.assertEqual(role, access.CONTRACTOR)
            self.assertFalse(ai_contract._project_wide_allowed(self.master))

    def test_explicit_all_assignment_can_revoke_stale_contractor_gateway_role(self):
        manager = "manager@example.com"
        access.set_user_project_access(
            self.db,
            self.master,
            manager,
            "SITE_MANAGEMENT",
            None,
        )
        self.assertEqual(
            access.effective_user_classification(
                self.db,
                self.master,
                manager,
                "CONTRACTOR",
            ),
            access.ALL,
        )
        rows = access.authorized_contractor_rows(
            self.db,
            self.master,
            manager,
            "CONTRACTOR",
            active_only=True,
            can_admin=False,
        )
        self.assertEqual({row["contractor_code"] for row in rows}, {"SIGMA", "OTHER"})
        self.assertTrue(all(int(row["workspace_project_id"]) != self.master for row in rows))

    def test_operational_sheet_routes_keep_selected_workspace_contract(self):
        app_path = Path(__file__).resolve().parents[1] / "src/qlda/presentation/streamlit/app.py"
        source = app_path.read_text(encoding="utf-8")
        expected_routes = (
            "render_overview_v7(\n        st, db, pid,",
            "render_work_tasks_v1(\n        st, db, pid,",
            "render_schedule(pid)",
            "render_material_management(pid)",
            "render_site_diary(pid)",
            "render_documents(pid)",
            "render_drawings(pid)",
            "render_cost_management(pid)",
            "render_contract_management_v622(\n                    st, db, pid,",
        )
        for route in expected_routes:
            self.assertIn(route, source)

        # Production/Data Hub intentionally receives the master project so it can
        # resolve contractor spaces, but its authorization boundary must be the
        # guarded project/workspace API rather than an unrestricted query.
        production_path = Path(__file__).resolve().parents[1] / "src/qlda/presentation/streamlit/production_progress_ui.py"
        production_source = production_path.read_text(encoding="utf-8")
        self.assertIn("authorized_contractor_rows", production_source)
        self.assertIn("workspace_ids=workspace_ids", production_source)
        self.assertIn("allowed = set(workspace_ids)", production_source)


if __name__ == "__main__":
    unittest.main()
