from __future__ import annotations

import unittest

from qlda.runtime_core.production_progress_overview_patch import (
    _progress_dimension_sort_key,
    worksheet_catalog,
)


class ProductionProgressOverviewPatchTests(unittest.TestCase):
    def test_catalog_includes_raw_sheet_without_production_rows(self):
        contractors = [{
            "id": 1,
            "workspace_project_id": 101,
            "contractor_code": "NT-01",
            "contractor_name": "SIGMA",
        }]
        records = [
            {
                "workspace_project_id": 101,
                "source_name": "Theo dõi sản lượng",
                "worksheet": "Hầm",
                "record_type": "PRODUCTION",
            },
            {
                "workspace_project_id": 101,
                "source_name": "Theo dõi sản lượng",
                "worksheet": "Tầng 1",
                "record_type": "SHEET_ROW",
            },
        ]

        catalog = worksheet_catalog(records, contractors)
        by_sheet = {row["Worksheet"]: row for row in catalog}

        self.assertEqual(set(by_sheet), {"Hầm", "Tầng 1"})
        self.assertEqual(by_sheet["Hầm"]["Trạng thái"], "Đã chuẩn hóa")
        self.assertEqual(by_sheet["Tầng 1"]["Trạng thái"], "Chưa chuẩn hóa")
        self.assertEqual(by_sheet["Tầng 1"]["Records"], 1)
        self.assertEqual(by_sheet["Tầng 1"]["Điểm sản lượng"], 0)

    def test_floor_progress_dimensions_sort_like_source_sheet(self):
        values = ["T20", "T3A", "T2", "TL", "T19A", "T1", "T10", "T3"]
        ordered = sorted(values, key=_progress_dimension_sort_key)
        self.assertEqual(
            ordered,
            ["T1", "TL", "T2", "T3", "T3A", "T10", "T19A", "T20"],
        )


if __name__ == "__main__":
    unittest.main()
