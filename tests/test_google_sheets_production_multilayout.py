from __future__ import annotations

import unittest

from qlda.composition import RuntimeStage, install_stage

# Production activates source-faithful Data Hub parsing explicitly through the
# DATA stage; tests do the same instead of relying on package-import side effects.
install_stage(RuntimeStage.DATA)

from qlda.application.google_sheets.service import normalize_production_sheet


class GoogleSheetsProductionMultiLayoutTests(unittest.TestCase):
    def test_finds_zone_header_beyond_first_30_rows(self):
        rows = [[f"Ghi chú {i}"] for i in range(40)]
        rows.extend([
            ["STT", "Công tác", "Zone 1", "Zone 2", "Zone 3"],
            [1, "Lắp đặt ống", "25%", "50%", "100%"],
        ])
        result = normalize_production_sheet("Tầng 5", rows)
        self.assertEqual(len(result), 3)
        self.assertEqual({x.zone for x in result}, {"Zone 1", "Zone 2", "Zone 3"})
        self.assertTrue(all(x.work_item == "Lắp đặt ống" for x in result))

    def test_detects_work_item_column_instead_of_assuming_first_column(self):
        rows = [
            ["STT", "Mã", "Nội dung công tác", "Zone 1", "Zone 2"],
            [1, "MEP-01", "Kéo dây điện", 0.5, 1.0],
        ]
        result = normalize_production_sheet("Tầng 1", rows)
        self.assertEqual([x.work_item for x in result], ["Kéo dây điện", "Kéo dây điện"])
        self.assertEqual([x.progress_percent for x in result], [50.0, 100.0])

    def test_supports_khu_vuc_and_kv_zone_headers(self):
        rows = [
            ["Công tác", "Khu vực 1", "KV2", "Area 3"],
            ["Lắp đặt đèn", "10 %", "0,25", 0.75],
        ]
        result = normalize_production_sheet("Tầng 2", rows)
        self.assertEqual(len(result), 3)
        self.assertEqual([x.progress_percent for x in result], [10.0, 25.0, 75.0])

    def test_carries_merged_work_item_label_for_following_progress_row(self):
        rows = [
            ["Công tác", "Zone 1", "Zone 2"],
            ["Thi công ống gió", "", ""],
            ["", "35%", "60%"],
        ]
        result = normalize_production_sheet("Tầng 3", rows)
        self.assertEqual(len(result), 2)
        self.assertEqual({x.work_item for x in result}, {"Thi công ống gió"})
        self.assertEqual([x.progress_percent for x in result], [35.0, 60.0])

    def test_floor_matrix_detects_tl_and_floor_headers_with_count_row(self):
        rows = [
            [None, "BẢNG KHỐI LƯỢNG MEP"],
            [None, "Công tác", "T1", "TL", "T2", "T3", "T4"],
            [None, None, 1, 12, 12, 12, 12],
            [None, "1. CĂN HỘ", 0.50, 0.75, 1.00, 0.25, 0.10],
        ]
        result = normalize_production_sheet("S2 (update)", rows)
        self.assertEqual({x.zone for x in result}, {"T1", "TL", "T2", "T3", "T4"})
        self.assertTrue(all(x.work_item == "1. CĂN HỘ" for x in result))
        self.assertEqual({x.source_row for x in result}, {4})

    def test_summary_below_primary_matrix_does_not_leak_into_floor_zones(self):
        rows = [
            [None, "BẢNG KHỐI LƯỢNG MEP"],
            [None, "Công tác", "T1", "T2"],
            [None, None, 1, 12],
            [None, "Thi công ống", 0.5, 1.0],
            [None, "TỔNG SẢN LƯỢNG"],
            [None, "Hạng mục công việc", "Đốt tầng 1-2", "Tổng"],
            ["A1", "Phần trục kín", 0.8, 0.9],
        ]
        result = normalize_production_sheet("S2 (update)", rows)
        self.assertEqual(len(result), 2)
        self.assertEqual({x.source_row for x in result}, {4})
        self.assertEqual({x.zone for x in result}, {"T1", "T2"})


if __name__ == "__main__":
    unittest.main()
