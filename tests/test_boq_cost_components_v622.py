from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from openpyxl import Workbook

import ai_service
import boq_multisheet_v622 as boq
from boq_cost_components_v622 import install_boq_cost_components
from cloud_db import CloudDatabase


class BOQCostComponentsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        install_boq_cost_components()

    @staticmethod
    def _split_workbook() -> bytes:
        wb = Workbook()
        ws = wb.active
        ws.title = "Điện"
        ws.append(["STT", "Nội dung công việc", "ĐVT", "Khối lượng", "Đơn giá", None, "Thành tiền"])
        ws.append([None, None, None, None, "Vật tư", "Nhân công", None])
        ws.append([1, "Cáp điện", "m", 10, 100, 20, 1200])
        ws.append([2, "Ống luồn", "m", 5, 40, 10, 250])
        out = io.BytesIO()
        wb.save(out)
        return out.getvalue()

    @staticmethod
    def _legacy_workbook() -> bytes:
        wb = Workbook()
        ws = wb.active
        ws.title = "Điện"
        ws.append(["STT", "Nội dung công việc", "ĐVT", "Khối lượng", "Đơn giá", "Thành tiền"])
        ws.append([1, "Tủ điện", "bộ", 2, 500, 1000])
        out = io.BytesIO()
        wb.save(out)
        return out.getvalue()

    def test_parser_splits_material_and_labor_without_changing_legacy_total(self):
        result = boq.parse_boq_workbook(self._split_workbook(), "BOQ_split.xlsx")
        self.assertEqual(result["detail_line_count"], 2)
        first = result["detail_items"][0]
        self.assertEqual(first["material_unit_price"], 100.0)
        self.assertEqual(first["labor_unit_price"], 20.0)
        self.assertEqual(first["material_cost"], 1000.0)
        self.assertEqual(first["labor_cost"], 200.0)
        self.assertEqual(first["unit_price"], 120.0)
        self.assertEqual(first["budget_total"], 1200.0)
        self.assertEqual(result["material_cost_total"], 1200.0)
        self.assertEqual(result["labor_cost_total"], 250.0)
        self.assertEqual(result["component_discrepancy_count"], 0)

    def test_legacy_boq_is_not_auto_split(self):
        result = boq.parse_boq_workbook(self._legacy_workbook(), "BOQ_legacy.xlsx")
        item = result["detail_items"][0]
        self.assertIsNone(item["material_unit_price"])
        self.assertIsNone(item["labor_unit_price"])
        self.assertIsNone(item["material_cost"])
        self.assertIsNone(item["labor_cost"])
        self.assertEqual(item["unit_price"], 500.0)
        self.assertEqual(item["budget_total"], 1000.0)

    def test_database_persists_four_component_columns(self):
        result = boq.parse_boq_workbook(self._split_workbook(), "BOQ_split.xlsx")
        with tempfile.TemporaryDirectory() as tmp:
            db = CloudDatabase(Path(tmp) / "boq_components.db")
            pid = db.add_project("BC01", "BOQ components")
            boq.save_boq_summary_to_project(db, pid, result)
            row = db.cost_budgets(pid)[-1]
            self.assertIn("material_unit_price", row.keys())
            self.assertIn("labor_unit_price", row.keys())
            self.assertIn("material_cost", row.keys())
            self.assertIn("labor_cost", row.keys())
            self.assertEqual(float(row["material_unit_price"]), 100.0)
            self.assertEqual(float(row["labor_unit_price"]), 20.0)
            self.assertEqual(float(row["material_cost"]), 1000.0)
            self.assertEqual(float(row["labor_cost"]), 200.0)

    def test_ai_context_receives_component_prices_and_costs(self):
        result = boq.parse_boq_workbook(self._split_workbook(), "BOQ_split.xlsx")
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "boq_ai_components.db"
            db = CloudDatabase(db_path)
            pid = db.add_project("BC02", "BOQ AI components")
            boq.save_boq_summary_to_project(db, pid, result)

            snapshot = ai_service.ProjectContextBuilder(db_path).build(
                pid,
                "chi phí nhân công và vật tư của cáp điện",
            )
            self.assertIn("PHÂN TÁCH CHI PHÍ VẬT TƯ / NHÂN CÔNG", snapshot)
            self.assertIn("[BOQ-COMPONENT:", snapshot)
            self.assertIn("đơn_giá_vật_tư=100 VND", snapshot)
            self.assertIn("chi_phí_vật_tư=1,000 VND", snapshot)
            self.assertIn("đơn_giá_nhân_công=20 VND", snapshot)
            self.assertIn("chi_phí_nhân_công=200 VND", snapshot)
            self.assertIn("không được tự chia", snapshot.lower())


if __name__ == "__main__":
    unittest.main()
