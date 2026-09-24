from __future__ import annotations

import unittest

# Runtime installs the source-faithful Data Hub parser used in production.
import qlda.runtime_core  # noqa: F401
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
            ["Thi công ống", "", ""],
            ["", "30%", "40%"],
        ]
        result = normalize_production_sheet("Tầng 3", rows)
        self.assertEqual(len(result), 2)
        self.assertTrue(all(x.work_item == "Thi công ống" for x in result))

    def test_sme_upper_tower_floor_matrix_is_normalized(self):
        # Mirrors the uploaded SME S2/S3/S4 update layout:
        # two leading metadata columns, Công tác in column C, then T1/TL/T2/... .
        rows = [
            [None, None, "BẢNG KHỐI LƯỢNG MEP - THÁP S4", None, None, None],
            [None, None, "Công tác", "T1", "TL", "T2", "T3", "T19A", "T36", "TỔNG"],
            [None, None, None, 1, 12, 12, 12, 11, 6, None],
            ["THÔ", "A4", "Lắp đặt ống điện", 0.5, 0.725, 0.825, 1.0, 0.8, 0.6, 0.82],
        ]
        result = normalize_production_sheet("S2 (update)", rows)

        self.assertEqual(len(result), 7)
        self.assertEqual(
            [x.zone for x in result],
            ["T1", "TL", "T2", "T3", "T19A", "T36", "TỔNG"],
        )
        self.assertTrue(all(x.work_item == "Lắp đặt ống điện" for x in result))
        self.assertEqual(
            [round(x.progress_percent, 1) for x in result],
            [50.0, 72.5, 82.5, 100.0, 80.0, 60.0, 82.0],
        )
        # TỔNG is a workbook-authored value. Preserve it instead of replacing it
        # later with an arithmetic mean of floors or duplicate hierarchy rows.
        self.assertIn("TỔNG", {x.zone for x in result})

    def test_floor_count_metadata_row_is_not_imported_as_progress(self):
        rows = [
            [None, None, "Công tác", "T1", "TL", "T2", "T3", "TỔNG"],
            [None, None, None, 1, 12, 12, 12, None],
            ["THÔ", "A5", "Kéo cáp", 0, 0.6, 0.9, 1, 0.7],
        ]
        result = normalize_production_sheet("S3 (update)", rows)
        self.assertEqual(len(result), 5)
        self.assertTrue(all(x.source_row == 3 for x in result))
        self.assertEqual(
            [x.progress_percent for x in result],
            [0.0, 60.0, 90.0, 100.0, 70.0],
        )
        self.assertEqual([x.zone for x in result], ["T1", "TL", "T2", "T3", "TỔNG"])


if __name__ == "__main__":
    unittest.main()
