from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cloud_db import CloudDatabase
from ipc_claim_delete_v622 import delete_ipc_claim, delete_ipc_revision
from ipc_claim_v622 import (
    ipc_claim_revisions,
    list_ipc_claims,
    load_ipc_workbook,
    save_ipc_claim,
)


def _result(batch: str, requested: float, current_value: float) -> dict:
    return {
        "filename": f"IPC01_{batch}.xlsx",
        "batch_id": batch,
        "claim_no": "01",
        "claim_code": "IPC-01",
        "metadata": {
            "contractor": "SIGMA",
            "contract_no": "HD-01",
            "package": "MEP",
            "from_date": "2026-08-01",
            "to_date": "2026-08-31",
        },
        "summary": {
            "contract_value": 1_000_000_000,
            "requested_amount": requested,
            "cumulative_completed": requested,
            "previous_approved": 0,
            "cumulative_retention": 0,
            "contract_advance": 100_000_000,
            "cumulative_advance_recovery": 0,
            "current_deductions": 0,
        },
        "workbook_sheet_names": ["Thanh toán", "GTHT"],
        "workbook_sheets": {
            "Thanh toán": {"columns": ["Dòng", "A"], "rows": [[1, requested]], "row_count": 1, "col_count": 1, "truncated": False},
            "GTHT": {"columns": ["Dòng", "A"], "rows": [[1, current_value]], "row_count": 1, "col_count": 1, "truncated": False},
        },
        "detail_items": [
            {
                "sheet_name": "GTHT", "row_no": 10, "seq": 1, "boq_item": "Hạng mục A",
                "contract_qty": 1, "unit": "lot", "spec": "", "item_code": "", "brand": "", "origin": "",
                "material_unit_price": 0, "labor_unit_price": 0, "contract_amount": 1_000_000_000,
                "material_previous_qty": 0, "material_current_qty": 1, "material_cumulative_qty": 1,
                "installation_previous_pct": 0, "installation_current_pct": 0, "installation_cumulative_pct": 0,
                "material_previous_value": 0, "material_current_value": current_value, "material_cumulative_value": current_value,
                "installation_previous_value": 0, "installation_current_value": 0, "installation_cumulative_value": 0,
                "deduction_previous": 0, "deduction_current": 0, "deduction_cumulative": 0,
                "current_value": current_value, "cumulative_value": current_value, "completion_ratio": 0,
                "note": "", "cost_code": "", "system": "",
            }
        ],
        "detail_line_count": 1,
        "warnings": [],
    }


class IPCClaimDeleteTests(unittest.TestCase):
    def test_delete_latest_revision_restores_previous_workbook(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = CloudDatabase(Path(tmp) / "delete.db")
            pid = db.add_project("DEL01", "Delete IPC")
            s0 = save_ipc_claim(db, pid, _result("batch0", 100_000_000, 100_000_000))
            s1 = save_ipc_claim(db, pid, _result("batch1", 200_000_000, 200_000_000))
            self.assertEqual(s0["revision_no"], 0)
            self.assertEqual(s1["revision_no"], 1)

            claim_id = s1["claim_id"]
            result = delete_ipc_revision(db, claim_id, 1)
            self.assertTrue(result["restored_previous"])
            self.assertEqual(result["restored_revision"], 0)

            claim = list_ipc_claims(db, pid)[0]
            self.assertEqual(int(claim["latest_revision"]), 0)
            self.assertEqual(float(claim["requested_amount"]), 100_000_000)
            workbook = load_ipc_workbook(db, claim_id)
            self.assertEqual(workbook["batch_id"], "batch0")
            self.assertEqual(len(ipc_claim_revisions(db, claim_id)), 1)

    def test_revision_zero_is_protected_and_delete_claim_removes_everything(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = CloudDatabase(Path(tmp) / "delete_all.db")
            pid = db.add_project("DEL02", "Delete Claim")
            stats = save_ipc_claim(db, pid, _result("batch0", 100_000_000, 100_000_000))
            claim_id = stats["claim_id"]

            with self.assertRaises(ValueError):
                delete_ipc_revision(db, claim_id, 0)

            deleted = delete_ipc_claim(db, claim_id)
            self.assertEqual(deleted["claim_code"], "IPC-01")
            self.assertEqual(list_ipc_claims(db, pid), [])
            with db.connect() as c:
                count = c.execute(
                    "SELECT COUNT(*) AS n FROM payment_tracking WHERE project_id=? AND payment_code=?",
                    (pid, "IPC-01"),
                ).fetchone()[0]
            self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
