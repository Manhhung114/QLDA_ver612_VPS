from __future__ import annotations

import unittest

from ipc_payment_semantic_v622 import (
    semantic_payment_summary_from_rows,
    semantic_payment_summary_from_saved_result,
)


class IPCPaymentSemanticTests(unittest.TestCase):
    def _rows(self):
        # Deliberately shifted rows/columns and a renamed sheet-style layout.
        return [
            [None, "BẢNG TỔNG HỢP THANH TOÁN MỚI", None, None, None],
            [None, "Lũy kế giá trị nghiệm thu kỳ trước [4]", None, 382_541_760_847, 0.75],
            [None, "- Giá trị nghiệm thu vật tư [4a]", None, 314_556_903_884, None],
            [None, "- Giá trị nghiệm thu lắp đặt [4b]", None, 67_984_856_963, None],
            [None, None, None, None, None],
            [None, "Giá trị nghiệm thu kỳ này [5]", None, 29_373_263_888, 0.06],
            [None, "- Giá trị nghiệm thu vật tư [5a]", None, 24_246_399_333, None],
            [None, "- Giá trị nghiệm thu lắp đặt [5b]", None, 5_126_864_555, None],
            [None, "Lũy kế giá trị nghiệm thu đến hết kỳ này [6]", None, 411_915_024_735, 0.81],
            [None, "- Giá trị nghiệm thu vật tư [6a]=[4a]+[5a]", None, 338_803_303_217, None],
            [None, "- Giá trị nghiệm thu lắp đặt [6b]=[4b]+[5b]", None, 73_111_721_518, None],
            [None, "Lũy kế giá trị khấu trừ, giữ lại đến hết kỳ này [7]", None, 155_618_318_624, None],
        ]

    def test_shifted_form_uses_semantics_and_period_identity(self):
        result = semantic_payment_summary_from_rows(self._rows(), sheet_name="03.DNTT-NEW")
        self.assertTrue(result["ok"])
        self.assertEqual(result["previous_material_acceptance"], 314_556_903_884)
        self.assertEqual(result["current_material_acceptance"], 24_246_399_333)
        self.assertEqual(result["cumulative_material_acceptance"], 338_803_303_217)
        self.assertTrue(result["validation"]["material_period_sum_ok"])
        self.assertEqual(
            result["cumulative_material_acceptance"] - result["previous_material_acceptance"],
            result["current_material_acceptance"],
        )

    def test_wrong_direct_current_is_replaced_by_cumulative_minus_previous(self):
        rows = self._rows()
        rows[6][3] = 35_016_926_930  # old wrong subtotal/mapping
        result = semantic_payment_summary_from_rows(rows, sheet_name="FORM-CHANGED")
        self.assertTrue(result["ok"])
        self.assertEqual(result["current_material_acceptance"], 24_246_399_333)
        self.assertFalse(result["validation"]["material_current_identity_ok"])
        self.assertEqual(result["validation"]["material_current_direct"], 35_016_926_930)

    def test_saved_workbook_scans_all_sheets_and_ignores_sheet_name(self):
        snapshot = {
            "sheet": "Bảng 9 - đổi tên hoàn toàn",
            "columns": ["Dòng", "A", "B", "C", "D", "E"],
            "rows": [[idx + 1] + row for idx, row in enumerate(self._rows())],
            "row_count": len(self._rows()),
            "col_count": 5,
            "truncated": False,
        }
        saved = {
            "summary": {},
            "workbook_sheets": {
                "README": {"sheet": "README", "columns": ["Dòng", "A"], "rows": [[1, "Ghi chú"]]},
                "Bảng 9 - đổi tên hoàn toàn": snapshot,
            },
        }
        result = semantic_payment_summary_from_saved_result(saved)
        self.assertTrue(result["ok"])
        self.assertEqual(result["sheet"], "Bảng 9 - đổi tên hoàn toàn")
        self.assertEqual(result["current_material_acceptance"], 24_246_399_333)
        self.assertGreaterEqual(len(result["candidates"]), 1)


if __name__ == "__main__":
    unittest.main()
