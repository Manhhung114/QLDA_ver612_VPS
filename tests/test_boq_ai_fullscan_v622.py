from __future__ import annotations

import sqlite3
import unittest

import boq_persistence_v622 as persistence
from boq_ai_fullscan_v622 import PATCH_MARKER, fullscan_boq_component_totals


def _row(row_no: int, values: dict[int, object], width: int = 14) -> list[object]:
    cells: list[object] = [None] * width
    for col_1based, value in values.items():
        cells[col_1based - 1] = value
    return [row_no] + cells


def _snapshot(sheet: str, header_row: int, data_row: int, material: float, labor: float) -> dict:
    return {
        "sheet": sheet,
        "columns": ["Dòng"] + [f"C{i}" for i in range(1, 15)],
        "rows": [
            _row(header_row, {1: "TT", 2: "Nội dung công việc", 3: "Khối lượng", 9: "Đơn giá", 11: "Thành tiền"}),
            _row(header_row + 1, {9: "Vật Tư", 10: "Nhân công"}),
            _row(data_row, {1: 1, 2: f"Công việc {sheet}", 3: 1, 9: material, 10: labor}),
        ],
        "row_count": data_row,
        "col_count": 14,
        "truncated": False,
    }


class BOQAIFullscanTests(unittest.TestCase):
    def test_scans_all_saved_sheets_and_header_below_row_30(self):
        c = sqlite3.connect(":memory:")
        c.row_factory = sqlite3.Row
        c.execute(
            "CREATE TABLE boq_excel_workbooks(project_id INTEGER PRIMARY KEY,filename TEXT,batch_id TEXT,payload TEXT,updated_at TEXT)"
        )
        c.execute(
            "CREATE TABLE cost_budgets(id INTEGER PRIMARY KEY,project_id INTEGER,quantity REAL,note TEXT)"
        )

        saved = {
            "workbook_sheets": {
                "E.1 THÁP S2": _snapshot("E.1 THÁP S2", 2, 6, 10, 5),
                # Regression: the old recovery detector only scanned through row 30.
                "E.9 THÁP S4": _snapshot("E.9 THÁP S4", 40, 50, 100, 20),
            }
        }
        payload = persistence._encode_result(saved)
        c.execute(
            "INSERT INTO boq_excel_workbooks VALUES(1,'boq.xlsx','batch1',?,'2026-09-10')",
            (payload,),
        )
        c.execute(
            "INSERT INTO cost_budgets VALUES(1,1,3,'[QLDA_BOQ_EXCEL] file=boq.xlsx|sheet=E.1 THÁP S2|row=6|batch=batch1')"
        )
        c.execute(
            "INSERT INTO cost_budgets VALUES(2,1,2,'[QLDA_BOQ_EXCEL] file=boq.xlsx|sheet=E.9 THÁP S4|row=50|batch=batch1')"
        )

        stats = fullscan_boq_component_totals(c, 1)
        self.assertTrue(stats["ok"])
        self.assertTrue(stats["complete"])
        self.assertEqual(stats["total_rows"], 2)
        self.assertEqual(stats["scanned_rows"], 2)
        self.assertEqual(stats["component_sheet_count"], 2)
        self.assertEqual(stats["material_total"], 230.0)
        self.assertEqual(stats["labor_total"], 55.0)

    def test_marker(self):
        self.assertIn("FULLSCAN", PATCH_MARKER)


if __name__ == "__main__":
    unittest.main()
