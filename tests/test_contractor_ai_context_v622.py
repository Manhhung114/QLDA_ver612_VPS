from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cloud_db
from ai_service import ProjectContextBuilder
from contractor_ai_context_v622 import install_contractor_ai_context, multi_contractor_component_aggregate
from contractor_workspace_v622 import add_contractor, ensure_default_contractor, install_contractor_workspace


class ContractorAIContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        install_contractor_workspace()
        install_contractor_ai_context()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "ai_contractors.db"
        self.db = cloud_db.CloudDatabase(self.path)
        self.master = self.db.add_project("DA-AI", "Dự án AI đa nhà thầu")
        default = ensure_default_contractor(self.db, self.master)
        self.default_pid = int(default["workspace_project_id"])
        self.second = add_contractor(self.db, self.master, "REE", "Công ty REE", contract_no="HD-02")
        self.second_pid = int(self.second["workspace_project_id"])

        self.db.save_cost_budget(self.default_pid, {
            "task_ref": "", "boq_item": "BOQ nhà thầu hiện tại", "quantity": 1.0, "unit": "ls",
            "unit_price": 100.0, "budget_total": 100.0, "contract_type": "Trọn gói",
            "contractor": "Nhà thầu hiện tại", "note": "",
        })
        self.db.save_cost_budget(self.second_pid, {
            "task_ref": "", "boq_item": "BOQ REE", "quantity": 1.0, "unit": "ls",
            "unit_price": 250.0, "budget_total": 250.0, "contract_type": "Trọn gói",
            "contractor": "REE", "note": "",
        })

    def tearDown(self):
        self.tmp.cleanup()

    def test_default_project_ai_context_contains_every_contractor(self):
        builder = ProjectContextBuilder(self.path)
        text = builder.build(self.master, "Đánh giá tổng quan dự án")
        self.assertIn("PHẠM VI TOÀN DỰ ÁN / TẤT CẢ NHÀ THẦU", text)
        self.assertIn("[CONTRACTOR:NT-01|", text)
        self.assertIn("[CONTRACTOR:REE|Công ty REE]", text)
        self.assertIn("BOQ nhà thầu hiện tại", text)
        self.assertIn("BOQ REE", text)
        self.assertIn("BAC 350 VND", text)

    def test_component_aggregate_uses_highest_ipc_separately_per_contractor(self):
        contractors = [
            {"workspace_project_id": 10, "contractor_code": "A", "contractor_name": "Nhà thầu A"},
            {"workspace_project_id": 20, "contractor_code": "B", "contractor_name": "Nhà thầu B"},
        ]

        def fake_remaining(_connection, workspace_id):
            if workspace_id == 10:
                return {
                    "ok": True, "valid": True, "latest_claim_code": "IPC-10",
                    "boq_material_total": 200.0, "boq_labor_total": 40.0,
                    "ipc_material_cumulative": 150.0, "ipc_labor_cumulative": 30.0,
                    "remaining_material": 50.0, "remaining_labor": 10.0,
                }
            self.assertEqual(workspace_id, 20)
            return {
                "ok": True, "valid": True, "latest_claim_code": "IPC-06",
                "boq_material_total": 120.0, "boq_labor_total": 30.0,
                "ipc_material_cumulative": 80.0, "ipc_labor_cumulative": 18.0,
                "remaining_material": 40.0, "remaining_labor": 12.0,
            }

        with patch("project_remaining_components_v622.project_remaining_components", side_effect=fake_remaining) as calc:
            result = multi_contractor_component_aggregate(object(), contractors)

        self.assertTrue(result["valid"])
        self.assertEqual(calc.call_count, 2)
        self.assertEqual([row["latest_claim_code"] for row in result["rows"]], ["IPC-10", "IPC-06"])
        self.assertAlmostEqual(result["boq_material_total"], 320.0)
        self.assertAlmostEqual(result["ipc_material_cumulative"], 230.0)
        self.assertAlmostEqual(result["remaining_material"], 90.0)
        self.assertAlmostEqual(result["remaining_labor"], 22.0)
        self.assertAlmostEqual(result["remaining_total"], 112.0)

    def test_incomplete_contractor_prevents_project_total_being_marked_valid(self):
        contractors = [
            {"workspace_project_id": 10, "contractor_code": "A", "contractor_name": "A"},
            {"workspace_project_id": 20, "contractor_code": "B", "contractor_name": "B"},
        ]
        side_effect = [
            {
                "ok": True, "valid": True, "latest_claim_code": "IPC-03",
                "boq_material_total": 100.0, "boq_labor_total": 20.0,
                "ipc_material_cumulative": 40.0, "ipc_labor_cumulative": 10.0,
                "remaining_material": 60.0, "remaining_labor": 10.0,
            },
            {"ok": False, "valid": False, "reason": "no_numeric_ipc"},
        ]
        with patch("project_remaining_components_v622.project_remaining_components", side_effect=side_effect):
            result = multi_contractor_component_aggregate(object(), contractors)
        self.assertFalse(result["valid"])
        self.assertEqual(result["invalid_contractors"], ["B"])
        # Valid contractor subtotal can be retained for diagnostics, but callers
        # are explicitly forbidden from presenting it as the whole-project total.
        self.assertAlmostEqual(result["remaining_material"], 60.0)


if __name__ == "__main__":
    unittest.main()
