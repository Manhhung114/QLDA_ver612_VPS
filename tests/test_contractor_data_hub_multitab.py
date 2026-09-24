from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from qlda.application.contractor_data_hub import ContractorDataHubService
from qlda.infrastructure.google_sheets.client import GoogleSheetsClient


class _SqliteDB:
    def __init__(self, path: Path):
        self.path = Path(path)

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()


class _MultiTabClient:
    authorized = True

    def metadata(self, spreadsheet_id: str):
        return {
            "title": "Theo dõi sản lượng",
            "sheets": [
                {"sheet_id": 0, "title": "Hầm", "index": 0, "hidden": False},
                {"sheet_id": 111, "title": "Tầng 1", "index": 1, "hidden": False},
                {"sheet_id": 222, "title": "Tầng 2", "index": 2, "hidden": False},
                {"sheet_id": 333, "title": "Cấu hình", "index": 3, "hidden": True},
            ],
        }

    def values(self, spreadsheet_id: str, a1_range: str):
        worksheet = a1_range.split("!", 1)[0].strip("'").replace("''", "'")
        progress = {"Hầm": "25%", "Tầng 1": "50%", "Tầng 2": "75%"}[worksheet]
        return [
            ["Công tác", "Zone 1"],
            [f"Thi công {worksheet}", progress],
        ]


class ContractorDataHubMultiTabTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = _SqliteDB(Path(self.tmp.name) / "hub.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_oauth_source_ignores_stale_single_worksheet_filter(self):
        service = ContractorDataHubService(self.db, client=_MultiTabClient())
        source = {
            "name": "Sản lượng",
            "category": "PRODUCTION",
            "worksheet_names": ["Hầm"],
        }

        records, production_points, snapshot = service._sheet_file_records(
            source,
            "spreadsheet-id",
            worksheet_names=["Hầm"],
        )

        self.assertEqual(snapshot["worksheets"], ["Hầm", "Tầng 1", "Tầng 2"])
        self.assertEqual(snapshot["worksheet_discovery"], "ALL_VISIBLE_TABS")
        self.assertEqual(snapshot["legacy_worksheet_filter_ignored"], ["Hầm"])
        self.assertEqual(production_points, 3)
        self.assertEqual(
            {row["worksheet"] for row in records if row["record_type"] == "PRODUCTION"},
            {"Hầm", "Tầng 1", "Tầng 2"},
        )

    def test_public_link_uses_oauth_to_discover_every_visible_tab(self):
        service = ContractorDataHubService(self.db, client=_MultiTabClient())
        source = {
            "name": "Sản lượng public",
            "category": "PRODUCTION",
            "external_id": "spreadsheet-id",
            "worksheet_names": ["__gid__:0"],
        }

        records, production_points, snapshot = service._public_sheet_records(source)

        self.assertEqual(snapshot["worksheets"], ["Hầm", "Tầng 1", "Tầng 2"])
        self.assertEqual(snapshot["access_mode"], "PUBLIC_LINK_WITH_OAUTH_DISCOVERY")
        self.assertEqual(production_points, 3)
        self.assertEqual(
            {row["worksheet"] for row in records if row["record_type"] == "PRODUCTION"},
            {"Hầm", "Tầng 1", "Tầng 2"},
        )

    def test_anonymous_public_link_keeps_all_configured_gids(self):
        service = ContractorDataHubService(self.db, client=None)
        source = {
            "name": "Sản lượng public",
            "category": "PRODUCTION",
            "external_id": "spreadsheet-id",
            "worksheet_names": ["__gid__:0", "__gid__:111", "__gid__:111"],
        }

        def values_for_gid(spreadsheet_id: str, gid: int = 0):
            return [
                ["Công tác", "Zone 1"],
                [f"Thi công gid {gid}", "50%"],
            ]

        with patch.object(GoogleSheetsClient, "public_values", side_effect=values_for_gid) as mocked:
            records, production_points, snapshot = service._public_sheet_records(source)

        self.assertEqual(mocked.call_count, 2)
        self.assertEqual(snapshot["gids"], [0, 111])
        self.assertEqual(snapshot["worksheets"], ["gid=0", "gid=111"])
        self.assertEqual(production_points, 2)
        self.assertEqual(
            {row["worksheet"] for row in records if row["record_type"] == "PRODUCTION"},
            {"gid=0", "gid=111"},
        )


if __name__ == "__main__":
    unittest.main()
