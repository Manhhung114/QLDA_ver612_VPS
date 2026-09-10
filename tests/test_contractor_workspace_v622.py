from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import cloud_db
from contractor_workspace_v622 import (
    add_contractor,
    delete_contractor,
    ensure_default_contractor,
    install_contractor_workspace,
    list_contractors,
)
from v622_contractor_workspace_patch import PATCH_MARKER, patch_contractor_workspace


class ContractorWorkspaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        install_contractor_workspace()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = cloud_db.CloudDatabase(Path(self.tmp.name) / "multi_contractor.db")
        self.master = self.db.add_project("DA-MULTI", "Dự án đa nhà thầu", "2026-01-01", "2027-12-31", "PM", "")

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def _boq(item: str, amount: float, contractor: str = "") -> dict:
        return {
            "task_ref": "",
            "boq_item": item,
            "quantity": 1.0,
            "unit": "ls",
            "unit_price": amount,
            "budget_total": amount,
            "contract_type": "Trọn gói",
            "contractor": contractor,
            "note": "",
        }

    def test_existing_project_becomes_default_workspace_without_moving_data(self):
        self.db.save_cost_budget(self.master, self._boq("BOQ lịch sử", 100.0, "SIGMA"))
        default = ensure_default_contractor(self.db, self.master)
        self.assertEqual(int(default["master_project_id"]), self.master)
        self.assertEqual(int(default["workspace_project_id"]), self.master)
        self.assertEqual(int(default["is_default"]), 1)
        self.assertEqual(len(self.db.cost_budgets(self.master)), 1)
        self.assertEqual(float(self.db.cost_budgets(self.master)[0]["budget_total"]), 100.0)

    def test_new_contractor_gets_full_isolated_project_workspace(self):
        ensure_default_contractor(self.db, self.master)
        second = add_contractor(
            self.db,
            self.master,
            "REE",
            "Công ty REE",
            contract_no="HD-REE-01",
            package_name="MEP-02",
        )
        second_pid = int(second["workspace_project_id"])
        self.assertNotEqual(second_pid, self.master)

        self.db.save_cost_budget(self.master, self._boq("BOQ SIGMA", 100.0, "SIGMA"))
        self.db.save_cost_budget(second_pid, self._boq("BOQ REE", 250.0, "REE"))

        self.assertEqual([r["boq_item"] for r in self.db.cost_budgets(self.master)], ["BOQ SIGMA"])
        self.assertEqual([r["boq_item"] for r in self.db.cost_budgets(second_pid)], ["BOQ REE"])

        contractors = list_contractors(self.db, self.master)
        self.assertEqual(len(contractors), 2)
        self.assertEqual({r["contractor_code"] for r in contractors}, {"NT-01", "REE"})

        # Child workspace projects must not appear as independent projects in the
        # main project selector. Only the master project is visible there.
        visible_ids = {int(row["id"]) for row in self.db.projects()}
        self.assertIn(self.master, visible_ids)
        self.assertNotIn(second_pid, visible_ids)

    def test_deleting_non_default_contractor_does_not_delete_master(self):
        ensure_default_contractor(self.db, self.master)
        second = add_contractor(self.db, self.master, "PCCC", "Nhà thầu PCCC")
        second_pid = int(second["workspace_project_id"])
        self.db.save_cost_budget(second_pid, self._boq("BOQ PCCC", 55.0, "PCCC"))

        delete_contractor(self.db, int(second["id"]))
        self.assertIsNotNone(self.db.project(self.master))
        self.assertIsNone(self.db.project(second_pid))
        self.assertEqual(len(list_contractors(self.db, self.master)), 1)

    def test_default_contractor_cannot_be_deleted(self):
        default = ensure_default_contractor(self.db, self.master)
        with self.assertRaises(ValueError):
            delete_contractor(self.db, int(default["id"]))

    def test_generated_ui_keeps_operations_on_workspace_but_ai_on_master(self):
        source = '''\ndef project_selector():\n    return 1, []\n\ndef render_ai_assistant(pid):\n    pass\n\ndef render_project_info(pid):\n    pass\n\ndef render_schedule(pid):\n    pass\n\ndef render_documents(pid):\n    pass\n\ndef render_drawings(pid):\n    pass\n\ndef render_cost_management(pid):\n    pass\n\ndef render_material_management(pid):\n    pass\n\ndef render_site_diary(pid):\n    pass\n\ndef render_reports(pid):\n    pass\n\ndef render_legal_documents():\n    pass\n\ndef render_settings():\n    pass\n\n_require_cloud_login_and_access()\nsidebar_project_tools()\npid, projects = project_selector()\n\nst.title("QLDA")\nif not pid:\n    st.stop()\n\np = db.project(pid)\n_ui_note(f"Dự án: **{p['code']} - {p['name']}**")\n_role = _cloud_access_role()\n_main_sections = [\n    ("📅 Tiến độ", lambda: render_schedule(pid)),\n    ("📁 Hồ sơ", lambda: render_documents(pid)),\n    ("📐 Bản vẽ", lambda: render_drawings(pid)),\n    ("💰 Chi phí", lambda: render_cost_management(pid)),\n    ("📦 Vật tư", lambda: render_material_management(pid)),\n    ("📷 Nhật ký", lambda: render_site_diary(pid)),\n    ("📊 Báo cáo", lambda: render_reports(pid)),\n    ("📚 Văn bản", lambda: render_legal_documents()),\n    ("🤖 AI", lambda: render_ai_assistant(pid)),\n    ("⚙️ Cài đặt", lambda: render_settings()),\n    ("🏗️ Dự án", lambda: render_project_info(pid)),\n]\n'''
        patched = patch_contractor_workspace(source)
        self.assertIn(PATCH_MARKER, patched)
        self.assertIn("render_ai_assistant(_master_pid)", patched)
        self.assertIn("render_schedule(pid)", patched)
        self.assertIn("_v622_render_contractor_management", patched)
        self.assertIn("render_project_info(_master_pid)", patched)
        compile(patched, "multi_contractor_ui_test.py", "exec")


if __name__ == "__main__":
    unittest.main()
