from __future__ import annotations

import unittest
from datetime import datetime, timezone

from vn_datetime_v622 import (
    format_tabular_vn,
    format_vn_date,
    format_vn_datetime,
    to_vn_datetime,
)


class VietnamDatetimeV622Test(unittest.TestCase):
    def test_utc_iso_is_shifted_to_vietnam(self):
        self.assertEqual(
            format_vn_datetime("2026-09-10T15:08:28.698078Z"),
            "10/09/2026 22:08:28",
        )
        self.assertEqual(
            format_vn_datetime("2026-09-10T15:08:28+00:00"),
            "10/09/2026 22:08:28",
        )

    def test_aware_datetime_is_shifted_to_vietnam(self):
        value = datetime(2026, 9, 10, 15, 8, 28, tzinfo=timezone.utc)
        result = to_vn_datetime(value)
        self.assertIsNotNone(result)
        self.assertEqual(result.strftime("%Y-%m-%d %H:%M:%S %z"), "2026-09-10 22:08:28 +0700")

    def test_naive_legacy_wall_clock_is_not_shifted_twice(self):
        self.assertEqual(
            format_vn_datetime("2026-09-10 22:08:28"),
            "10/09/2026 22:08:28",
        )

    def test_date_only_uses_vietnam_display_format(self):
        self.assertEqual(format_vn_date("2026-09-09"), "09/09/2026")
        self.assertEqual(format_vn_datetime("2026-09-09"), "09/09/2026")

    def test_tabular_formatter_only_changes_temporal_fields(self):
        rows = [{
            "Mã": "TASK-00001",
            "created_at": "2026-09-10T15:08:28Z",
            "Ban hành": "2021-06-30",
            "Khối lượng": 12.5,
        }]
        out = format_tabular_vn(rows)
        self.assertEqual(out[0]["Mã"], "TASK-00001")
        self.assertEqual(out[0]["created_at"], "10/09/2026 22:08:28")
        self.assertEqual(out[0]["Ban hành"], "30/06/2021")
        self.assertEqual(out[0]["Khối lượng"], 12.5)
        self.assertEqual(rows[0]["created_at"], "2026-09-10T15:08:28Z")

    def test_unparseable_temporal_text_is_preserved(self):
        rows = [{"Ngày ghi chú": "Chưa xác định", "Tên": "ABC"}]
        self.assertEqual(format_tabular_vn(rows), rows)


if __name__ == "__main__":
    unittest.main()
