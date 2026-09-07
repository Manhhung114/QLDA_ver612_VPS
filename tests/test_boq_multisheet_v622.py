from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook, load_workbook

from boq_multisheet_v622 import (
    AUTO_NOTE_PREFIX,
    SUMMARY_SHEET_NAME,
    build_summary_excel,
    parse_boq_workbook,
    save_boq_summary_to_project,
)
from cloud_db import CloudDatabase


def _sample_workbook() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Điện"
    ws.append(["BẢNG BOQ HỆ ĐIỆN"])
    ws.append(["STT", "Nội dung công việc", "ĐVT", "Khối lượng", "Đơn giá", "Thành tiền"])
    # Group/subtotal row: must not be imported because it would double-count child rows.
    ws.append(["A", "PHẦN TỦ CẤP NGUỒN", None, 0, 0, 600])
    ws.append([1, "Cáp điện", "m", 1, 100, 100])
    ws.append([2, "Tủ điện", "bộ", 2, 200, 400])
    # Regression: 'Vật tư...' must not be mistaken for VAT; 'Công tắc...' must not be mistaken for CỘNG.
    ws.append([3, "Vật tư phụ", "gói", 1, 50, 50])
    ws.append([4, "Công tắc đơn", "cái", 1, 50, 50])
    ws.append([None, "TỔNG CỘNG", None, None, None, 600])

    ws = wb.create_sheet("Nước")
    ws.append(["STT", "Hạng mục", "ĐVT", "Khối lượng", "Đơn giá", "Thành tiền"])
    ws.append([1, "Ống", "m", 3, 100, 300])
    ws.append([2, "Van", "cái", 2, 250, 500])

    ws = wb.create_sheet("Ghi chú")
    ws.append(["Nội dung", "Ghi chú"])
    ws.append(["Không phải BOQ", "Bỏ qua"])

    ws = wb.create_sheet("SUM")
    ws.append(["BẢNG TỔNG HỢP GIÁ TRỊ"])
    ws.append(["STT", "NỘI DUNG CÔNG VIỆC", "TỔNG", "GHI CHÚ"])
    ws.append([1, "Hệ thống điện", 600, None])
    ws.append([2, "Hệ thống nước", 800, None])
    ws.append([None, "Cộng giá trị trước thuế", 1400, None])
    ws.append([None, "Thuế VAT 8%", 112, None])
    ws.append([None, "Cộng giá trị sau thuế", 1512, None])

    hidden = wb.create_sheet("Hidden helper")
    hidden.sheet_state = "veryHidden"
    hidden["A1"] = "Không hiển thị"

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


class BOQMultiSheetTests(unittest.TestCase):
    def test_detects_detail_sheets_and_preserves_excel_sheet_view(self):
        result = parse_boq_workbook(_sample_workbook(), "BOQ_MEP.xlsx")
        self.assertEqual(result["detected_sheets"], ["Điện", "Nước"])
        self.assertEqual(result["workbook_sheet_names"], ["Điện", "Nước", "Ghi chú", "SUM"])
        self.assertNotIn("Hidden helper", result["workbook_sheet_names"])
        self.assertEqual(result["detail_line_count"], 6)
        self.assertEqual(result["detail_grand_total"], 1400.0)
        self.assertEqual(result["before_tax_total"], 1400.0)
        self.assertEqual(result["vat_total"], 112.0)
        self.assertEqual(result["after_tax_total"], 1512.0)
        self.assertEqual(result["grand_total"], 1512.0)
        self.assertEqual(result["summary_sheet"]["sheet"], "SUM")
        self.assertEqual(result["workbook_sheets"]["Điện"]["row_count"], 8)
        self.assertTrue(any("không cộng lặp" in x.lower() for x in result["warnings"]))

    def test_subtotals_are_not_double_counted(self):
        result = parse_boq_workbook(_sample_workbook(), "BOQ_MEP.xlsx")
        electric = next(row for row in result["summary"] if row["sheet"] == "Điện")
        self.assertEqual(electric["line_count"], 4)
        self.assertEqual(electric["budget_total"], 600.0)
        names = [item["boq_item"] for item in result["detail_items"] if item["sheet"] == "Điện"]
        self.assertIn("Vật tư phụ", names)
        self.assertIn("Công tắc đơn", names)
        self.assertNotIn("PHẦN TỦ CẤP NGUỒN", names)
        self.assertNotIn("TỔNG CỘNG", names)

    def test_exports_summary_appendix_and_detail_sheet_totals(self):
        result = parse_boq_workbook(_sample_workbook(), "BOQ_MEP.xlsx")
        payload = build_summary_excel(result)
        wb = load_workbook(io.BytesIO(payload), data_only=True)
        self.assertIn(SUMMARY_SHEET_NAME, wb.sheetnames)
        self.assertIn("Tổng theo sheet BOQ", wb.sheetnames)
        ws = wb[SUMMARY_SHEET_NAME]
        self.assertEqual(ws["A1"].value, SUMMARY_SHEET_NAME.upper())
        self.assertEqual(ws.cell(row=9, column=3).value, 1512.0)

    def test_saves_all_detail_lines_per_project_and_preserves_manual_boq(self):
        result = parse_boq_workbook(_sample_workbook(), "BOQ_MEP.xlsx")
        with tempfile.TemporaryDirectory() as tmp:
            db = CloudDatabase(Path(tmp) / "qlda_test.db")
            pid = db.add_project("P01", "Dự án thử")
            db.save_cost_budget(
                pid,
                {
                    "task_ref": "",
                    "boq_item": "BOQ thủ công",
                    "quantity": 1,
                    "unit": "Gói",
                    "unit_price": 99,
                    "budget_total": 99,
                    "contract_type": "",
                    "contractor": "",
                    "note": "manual",
                },
            )

            stats = save_boq_summary_to_project(db, pid, result, replace_existing_excel=True)
            self.assertEqual(stats["inserted"], 6)
            self.assertEqual(stats["before_tax_total"], 1400.0)
            self.assertEqual(stats["after_tax_total"], 1512.0)
            rows = db.cost_budgets(pid)
            self.assertEqual(len(rows), 7)
            self.assertEqual(sum(1 for r in rows if str(r["note"]).startswith(AUTO_NOTE_PREFIX)), 6)
            self.assertTrue(any(r["boq_item"] == "BOQ thủ công" for r in rows))

            # Re-importing the same workbook must replace, not duplicate, its Excel rows.
            save_boq_summary_to_project(db, pid, result, replace_existing_excel=False)
            rows = db.cost_budgets(pid)
            self.assertEqual(len(rows), 7)
            self.assertTrue(any(r["boq_item"] == "BOQ thủ công" for r in rows))


if __name__ == "__main__":
    unittest.main()
