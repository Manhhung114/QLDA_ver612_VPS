from __future__ import annotations

import unittest

import pandas as pd

from qlda.composition import RuntimeStage, install_stage

install_stage(RuntimeStage.DATA)

import qlda.application.contractor_data_hub.service as hub_service
from qlda.runtime_core.contractor_data_official_ai import extract_official_summaries
from qlda.runtime_core.production_progress_source_exact import build_source_exact_pivot


class ContractorDataSourceExactTests(unittest.TestCase):
    def test_duplicate_work_item_labels_are_not_averaged(self):
        # Real S2 has this same label at rows 5, 47 and 83 with different values.
        df = pd.DataFrame([
            {"Nhà thầu": "NT-01", "Nguồn": "SME", "Worksheet": "S2 (update)", "Dòng nguồn": 5,
             "Công tác": "I. Thi công lắp đặt phần thô", "Zone": "T1", "Tiến độ (%)": 50.0},
            {"Nhà thầu": "NT-01", "Nguồn": "SME", "Worksheet": "S2 (update)", "Dòng nguồn": 47,
             "Công tác": "I. Thi công lắp đặt phần thô", "Zone": "T1", "Tiến độ (%)": 32.5},
            {"Nhà thầu": "NT-01", "Nguồn": "SME", "Worksheet": "S2 (update)", "Dòng nguồn": 83,
             "Công tác": "I. Thi công lắp đặt phần thô", "Zone": "T1", "Tiến độ (%)": 92.0833333333},
        ])
        pivot = build_source_exact_pivot(df, ["T1"])
        self.assertEqual(len(pivot), 3)
        self.assertEqual(pivot["Dòng nguồn"].tolist(), [5, 47, 83])
        self.assertAlmostEqual(float(pivot.iloc[0]["T1"]), 50.0)
        self.assertAlmostEqual(float(pivot.iloc[1]["T1"]), 32.5)
        self.assertAlmostEqual(float(pivot.iloc[2]["T1"]), 92.0833333333)

    def test_floor_matrix_keeps_source_total_column(self):
        values = [
            [None, "BẢNG KHỐI LƯỢNG MEP"],
            [None, "Công tác", "T1", "TL", "T2", "TỔNG"],
            [None, None, 1, 12, 12, None],
            [None, "1. CĂN HỘ", 0.35, 0.5075, 0.5855, 0.7054846491],
            [None, "I. Thi công lắp đặt phần thô", 0.50, 0.725, 0.825, 0.9507675439],
            [None, "TỔNG SẢN LƯỢNG", None, None, None, None],
        ]
        rows = hub_service.normalize_production_sheet("S2 (update)", values)
        total_rows = [row for row in rows if row.zone == "TỔNG"]
        self.assertEqual([(x.source_row, x.work_item) for x in total_rows], [
            (4, "1. CĂN HỘ"),
            (5, "I. Thi công lắp đặt phần thô"),
        ])
        self.assertAlmostEqual(total_rows[0].progress_percent, 70.54846491, places=6)
        self.assertAlmostEqual(total_rows[1].progress_percent, 95.07675439, places=6)

    def test_official_summary_uses_workbook_totals_not_record_average(self):
        records = [
            {"workspace_project_id": 101, "worksheet": "S2 (update)", "record_type": "SHEET_ROW", "source_row": 122,
             "content": "B=TỔNG SẢN LƯỢNG"},
            {"workspace_project_id": 101, "worksheet": "S2 (update)", "record_type": "SHEET_ROW", "source_row": 123,
             "content": "B=Hạng mục công việc | AF=Thi công phần thô (Tính tỉ trọng 70%) | AI=Lắp đặt thiết bị (Tính tỉ trọng 20%) | AL=T&C (Tính tỉ trọng 10%) | AO=Tổng"},
            {"workspace_project_id": 101, "worksheet": "S2 (update)", "record_type": "SHEET_ROW", "source_row": 124,
             "content": "AF=0.944265350877193 | AI=0.30225 | AL=0 | AO=0.721435745614035"},
            {"workspace_project_id": 101, "worksheet": "S2 (update)", "record_type": "SHEET_ROW", "source_row": 125,
             "content": "B=Điện, điện nhẹ, báo cháy | AF=0.9435964912 | AI=0.0957894737 | AL=0 | AO=0.6796754386"},
            {"workspace_project_id": 101, "worksheet": "S2 (update)", "record_type": "SHEET_ROW", "source_row": 129,
             "content": "B=Tổng hợp sản lượng theo đầu mục tiến độ"},
            {"workspace_project_id": 101, "worksheet": "S2 (update)", "record_type": "SHEET_ROW", "source_row": 130,
             "content": "B=Hạng mục công việc | C=Đốt tầng 1-2 | F=Đốt 3-7 | AO=Tổng"},
            {"workspace_project_id": 101, "worksheet": "S2 (update)", "record_type": "SHEET_ROW", "source_row": 131,
             "content": "A=A1 | B=Phần trục kín căn hộ & hành lang | C=0.8866666667 | F=1 | AO=0.9486666667"},
            {"workspace_project_id": 101, "worksheet": "S2 (update)", "record_type": "SHEET_ROW", "source_row": 140,
             "content": "AO=0.6607712270093222"},
        ]
        summaries = extract_official_summaries(records)
        self.assertEqual(len(summaries), 1)
        item = summaries[0]
        self.assertAlmostEqual(float(item["weighted_total"]), 72.1435745614035, places=6)
        self.assertEqual(item["weighted_total_row"], 124)
        self.assertAlmostEqual(float(item["package_total"]), 66.07712270093222, places=6)
        self.assertEqual(item["package_total_row"], 140)
        self.assertAlmostEqual(float(item["discipline_totals"]["Điện, điện nhẹ, báo cháy"]), 67.96754386, places=6)


if __name__ == "__main__":
    unittest.main()
