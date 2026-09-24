from __future__ import annotations

import unittest

# Importing runtime_core installs the production completeness guard used by both
# Streamlit and sync workers.
import qlda.runtime_core  # noqa: F401
import qlda.application.contractor_data_hub.service as hub_service
from qlda.application.contractor_data_hub.service import ContractorDataHubService
from qlda.runtime_core.contractor_data_complete_rows import _include_raw_source


class ContractorDataCompleteRowsTests(unittest.TestCase):
    def test_generic_records_keep_every_nonempty_sheet_row(self):
        values = [
            [None, "BẢNG KHỐI LƯỢNG MEP"],
            [None, "Công tác", "T1", "T2"],
            [None, None, 1, 12],
            ["A4", "Lắp đặt ống", 0.5, 1.0],
            [None, "TỔNG SẢN LƯỢNG"],
            [None, "Hạng mục công việc", "Đốt tầng 1-2", "Tổng"],
            ["A1", "Phần trục kín", 0.8, 0.9],
            [None, None, None, None],
        ]
        rows = ContractorDataHubService._generic_sheet_records(
            {"name": "SME", "category": "PRODUCTION"},
            "S2 (update)",
            values,
            external_item_id="sheet-1",
        )
        self.assertEqual(len(rows), 7)
        self.assertEqual([row["source_row"] for row in rows], [1, 2, 3, 4, 5, 6, 7])
        self.assertTrue(all(row["record_type"] == "SHEET_ROW" for row in rows))
        self.assertIn("B=TỔNG SẢN LƯỢNG", rows[4]["content"])
        self.assertIn("C=Đốt tầng 1-2", rows[5]["content"])

    def test_production_source_is_synced_via_raw_plus_normalized_branch(self):
        effective = _include_raw_source({"category": "PRODUCTION", "name": "SME"})
        self.assertEqual(effective["category"], "AUTO")
        self.assertEqual(effective["_qlda_original_category"], "PRODUCTION")

    def test_primary_matrix_stops_before_lower_summary_tables(self):
        values = [
            [None, "BẢNG KHỐI LƯỢNG MEP"],
            [None, "Công tác", "T1", "T2", "TỔNG"],
            [None, None, 1, 12],
            ["A4", "Lắp đặt ống", 0.5, 1.0, 0.75],
            [None, "TỔNG SẢN LƯỢNG"],
            [None, "Hạng mục công việc", "Đốt tầng 1-2", None, "Tổng"],
            ["A1", "Phần trục kín", 0.8, None, 0.9],
        ]
        normalized = hub_service.normalize_production_sheet("S2 (update)", values)
        self.assertEqual(len(normalized), 2)
        self.assertEqual({row.source_row for row in normalized}, {4})
        self.assertEqual({row.zone for row in normalized}, {"T1", "T2"})
        self.assertEqual({row.work_item for row in normalized}, {"Lắp đặt ống"})


if __name__ == "__main__":
    unittest.main()
