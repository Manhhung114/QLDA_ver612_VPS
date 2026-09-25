from __future__ import annotations

import sqlite3
import unittest
from unittest.mock import patch

from qlda.runtime_core.project_remaining_components import largest_ipc_claim, project_remaining_components


class ProjectRemainingComponentsTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.executescript(
            """
            CREATE TABLE payment_claims(
                claim_id TEXT PRIMARY KEY,
                project_id INTEGER NOT NULL,
                claim_no TEXT,
                claim_code TEXT,
                filename TEXT,
                updated_at TEXT
            );
            """
        )
        self.db.executemany(
            "INSERT INTO payment_claims(claim_id,project_id,claim_no,claim_code,filename,updated_at) VALUES(?,?,?,?,?,?)",
            [
                ("c02", 1, "02", "IPC-02", "IPC02.xlsx", "2026-09-10 10:00:00"),
                ("c09", 1, "09", "IPC-09", "IPC09.xlsx", "2026-09-11 10:00:00"),
                ("c10", 1, "10", "IPC-10", "IPC10.xlsx", "2026-09-09 10:00:00"),
                ("other", 1, "A", "CLAIM-A", "A.xlsx", "2026-09-12 10:00:00"),
            ],
        )
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_highest_ipc_is_numeric_period_not_text_or_updated_time(self):
        latest = largest_ipc_claim(self.db, 1)
        self.assertIsNotNone(latest)
        self.assertEqual(latest["ipc_number"], 10)
        self.assertEqual(latest["claim_code"], "IPC-10")
        self.assertEqual(latest["claim_id"], "c10")

    def test_project_remaining_uses_boq_minus_cumulative_of_ipc10_only(self):
        boq = {
            "ok": True,
            "complete": True,
            "material_total": 400.0,
            "labor_total": 100.0,
            "scanned_rows": 4107,
            "total_rows": 4107,
        }
        latest_claim_scan = [{
            "ok": True,
            "claim_no": "10",
            "claim_code": "IPC-10",
            "selected_material_cumulative": 250.0,
            "selected_labor_cumulative": 60.0,
            "selected_source": "payment_summary_identity",
            "scanned_rows": 4029,
            "total_rows": 4029,
        }]

        with patch("qlda.runtime_core.boq_component_fullscan.fullscan_boq_component_totals", return_value=boq), \
             patch("qlda.runtime_core.claim_component_fullscan.fullscan_claim_components", return_value=latest_claim_scan) as claim_scan:
            result = project_remaining_components(self.db, 1)

        self.assertTrue(result["valid"])
        self.assertEqual(result["latest_ipc_number"], 10)
        self.assertEqual(result["latest_claim_code"], "IPC-10")
        self.assertAlmostEqual(result["remaining_material"], 150.0)
        self.assertAlmostEqual(result["remaining_labor"], 40.0)
        self.assertAlmostEqual(result["remaining_total"], 190.0)
        args = claim_scan.call_args.args
        self.assertEqual(args[1], 1)
        self.assertIn("IPC-10", args[2])
        self.assertEqual(claim_scan.call_count, 1)

    def test_negative_remaining_is_flagged_not_clamped_or_presented_as_valid(self):
        boq = {
            "ok": True,
            "complete": True,
            "material_total": 200.0,
            "labor_total": 50.0,
            "scanned_rows": 4107,
            "total_rows": 4107,
        }
        latest_claim_scan = [{
            "ok": True,
            "claim_no": "10",
            "claim_code": "IPC-10",
            "selected_material_cumulative": 150.0,
            "selected_labor_cumulative": 60.0,
            "selected_source": "payment_summary_identity",
            "scanned_rows": 4029,
            "total_rows": 4029,
        }]

        with patch("qlda.runtime_core.boq_component_fullscan.fullscan_boq_component_totals", return_value=boq), \
             patch("qlda.runtime_core.claim_component_fullscan.fullscan_claim_components", return_value=latest_claim_scan):
            result = project_remaining_components(self.db, 1)

        self.assertFalse(result["valid"])
        self.assertAlmostEqual(result["remaining_labor"], -10.0)
        self.assertIn("âm", result["reason"])

    def test_incomplete_boq_fullscan_cannot_be_final_remaining(self):
        boq = {
            "ok": True,
            "complete": False,
            "material_total": 400.0,
            "labor_total": 100.0,
            "scanned_rows": 4000,
            "total_rows": 4107,
        }
        latest_claim_scan = [{
            "ok": True,
            "claim_no": "10",
            "claim_code": "IPC-10",
            "selected_material_cumulative": 250.0,
            "selected_labor_cumulative": 60.0,
            "selected_source": "payment_summary_identity",
            "scanned_rows": 4029,
            "total_rows": 4029,
        }]

        with patch("qlda.runtime_core.boq_component_fullscan.fullscan_boq_component_totals", return_value=boq), \
             patch("qlda.runtime_core.claim_component_fullscan.fullscan_claim_components", return_value=latest_claim_scan):
            result = project_remaining_components(self.db, 1)

        self.assertFalse(result["valid"])
        self.assertIn("chưa quét đủ", result["reason"])


if __name__ == "__main__":
    unittest.main()
