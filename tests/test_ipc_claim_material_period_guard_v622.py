from __future__ import annotations

import sqlite3
import unittest

from claim_material_period_guard_v622 import _augment_one
from ipc_claim_v622 import _encode_result


class ClaimMaterialPeriodGuardTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.executescript(
            """
            CREATE TABLE payment_claim_items(
                claim_id TEXT NOT NULL,
                material_previous_value REAL DEFAULT 0,
                material_current_value REAL DEFAULT 0,
                material_cumulative_value REAL DEFAULT 0
            );
            CREATE TABLE payment_claim_workbooks(
                claim_id TEXT PRIMARY KEY,
                payload TEXT NOT NULL
            );
            """
        )

        rows = [
            [1, None, "Lũy kế giá trị nghiệm thu kỳ trước [4]", None, 382_541_760_847, 0.75],
            [2, None, "- Giá trị nghiệm thu vật tư [4a]", None, 314_556_903_884, None],
            [3, None, "- Giá trị nghiệm thu lắp đặt [4b]", None, 67_984_856_963, None],
            [4, None, "Giá trị nghiệm thu kỳ này [5]", None, 29_373_263_888, 0.06],
            [5, None, "- Giá trị nghiệm thu vật tư [5a]", None, 24_246_399_333, None],
            [6, None, "- Giá trị nghiệm thu lắp đặt [5b]", None, 5_126_864_555, None],
            [7, None, "Lũy kế giá trị nghiệm thu đến hết kỳ này [6]", None, 411_915_024_735, 0.81],
            [8, None, "- Giá trị nghiệm thu vật tư [6a]=[4a]+[5a]", None, 338_803_303_217, None],
            [9, None, "- Giá trị nghiệm thu lắp đặt [6b]=[4b]+[5b]", None, 73_111_721_518, None],
        ]
        snapshot = {
            "sheet": "Form thanh toán đổi tên",
            "columns": ["Dòng", "A", "B", "C", "D", "E"],
            "rows": rows,
            "row_count": len(rows),
            "col_count": 5,
            "truncated": False,
        }
        payload = {
            "summary": {"cumulative_material_acceptance": 338_803_303_217},
            "workbook_sheets": {"Form thanh toán đổi tên": snapshot},
            "detail_items": [],
        }
        self.db.execute(
            "INSERT INTO payment_claim_workbooks(claim_id,payload) VALUES(?,?)",
            ("c10", _encode_result(payload)),
        )
        # Simulate the wrong 35.016b detail subtotal that previously reached AI.
        self.db.execute(
            "INSERT INTO payment_claim_items(claim_id,material_previous_value,material_current_value,material_cumulative_value) VALUES(?,?,?,?)",
            ("c10", 300_000_000_000, 35_016_926_930, 335_016_926_930),
        )
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_ipc10_material_current_uses_summary_period_identity(self):
        original = {
            "ok": True,
            "claim_id": "c10",
            "claim_code": "IPC-10",
            "contract_value": 511_000_000_000,
            "control_ceiling": 411_915_024_735,
            "material_previous_total": 300_000_000_000,
            "material_current_total": 35_016_926_930,
            "material_cumulative_total": 335_016_926_930,
            "selected_material_cumulative": 338_803_303_217,
        }
        result = _augment_one(self.db, original)
        self.assertTrue(result["material_period_identity_ok"])
        self.assertEqual(result["selected_material_period_source"], "payment_summary_semantic_period_identity")
        self.assertEqual(result["selected_material_previous"], 314_556_903_884)
        self.assertEqual(result["selected_material_current"], 24_246_399_333)
        self.assertEqual(result["selected_material_cumulative"], 338_803_303_217)
        self.assertNotEqual(result["selected_material_current"], 35_016_926_930)
        self.assertEqual(
            result["selected_material_cumulative"] - result["selected_material_previous"],
            result["selected_material_current"],
        )


if __name__ == "__main__":
    unittest.main()
