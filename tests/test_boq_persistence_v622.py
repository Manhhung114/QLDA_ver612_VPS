from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from boq_persistence_v622 import (
    format_table_number,
    load_saved_boq_workbook,
    save_saved_boq_workbook,
)
from cloud_db import CloudDatabase


class BOQPersistenceTests(unittest.TestCase):
    def test_formats_thousands_and_decimals(self):
        self.assertEqual(format_table_number(108099905000), "108,099,905,000")
        self.assertEqual(format_table_number(228528553204.4444), "228,528,553,204.4444")
        self.assertEqual(format_table_number(1234.5), "1,234.5")
        self.assertEqual(format_table_number("E.1 THÁP S2"), "E.1 THÁP S2")

    def test_workbook_persists_across_database_instances(self):
        result = {
            "filename": "BOQ_MEP.xlsx",
            "batch_id": "abc123",
            "workbook_sheet_names": ["SUM", "E.1 THÁP S2"],
            "workbook_sheets": {
                "SUM": {
                    "sheet": "SUM",
                    "columns": ["Dòng", "A", "B", "C"],
                    "rows": [[1, "STT", "NỘI DUNG", "TỔNG"], [2, 1, "Điện", 108099905000]],
                    "row_count": 2,
                    "col_count": 3,
                    "truncated": False,
                },
                "E.1 THÁP S2": {
                    "sheet": "E.1 THÁP S2",
                    "columns": ["Dòng", "A", "B", "C"],
                    "rows": [[1, "STT", "NỘI DUNG", "THÀNH TIỀN"], [2, 1, "Cáp điện", 1000000]],
                    "row_count": 2,
                    "col_count": 3,
                    "truncated": False,
                },
            },
            "summary_sheet": {
                "sheet": "SUM",
                "rows": [{"row_no": 2, "stt": 1, "item": "Điện", "amount": 108099905000.0, "note": ""}],
                "before_tax_total": 108099905000.0,
                "vat_total": 8647992400.0,
                "after_tax_total": 116747897400.0,
            },
            "detected_sheets": ["E.1 THÁP S2"],
            "summary": [{"sheet": "E.1 THÁP S2", "line_count": 1, "budget_total": 1000000.0, "header_row": 1}],
            "detail_items": [
                {
                    "sheet": "E.1 THÁP S2",
                    "row_no": 2,
                    "boq_item": "Cáp điện",
                    "quantity": 1.0,
                    "unit": "m",
                    "unit_price": 1000000.0,
                    "budget_total": 1000000.0,
                    "task_ref": "",
                }
            ],
            "detail_line_count": 1,
            "detail_grand_total": 1000000.0,
            "before_tax_total": 108099905000.0,
            "vat_total": 8647992400.0,
            "after_tax_total": 116747897400.0,
            "grand_total": 116747897400.0,
            "warnings": [],
        }

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "qlda_test.db"
            db_account_a = CloudDatabase(path)
            pid = db_account_a.add_project("P01", "Dự án thử")
            meta = save_saved_boq_workbook(db_account_a, pid, result)
            self.assertEqual(meta["project_id"], pid)

            # Simulates opening the same project from another account/session.
            db_account_b = CloudDatabase(path)
            loaded = load_saved_boq_workbook(db_account_b, pid)
            self.assertIsNotNone(loaded)
            self.assertTrue(loaded["_persisted"])
            self.assertEqual(loaded["filename"], "BOQ_MEP.xlsx")
            self.assertEqual(loaded["workbook_sheet_names"], ["SUM", "E.1 THÁP S2"])
            self.assertEqual(loaded["after_tax_total"], 116747897400.0)
            self.assertEqual(
                loaded["workbook_sheets"]["SUM"]["rows"][1][3],
                108099905000,
            )


if __name__ == "__main__":
    unittest.main()
