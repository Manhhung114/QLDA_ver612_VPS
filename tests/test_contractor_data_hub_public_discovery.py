from __future__ import annotations

import io
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from openpyxl import Workbook

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


class _HtmlResponse:
    status_code = 200
    url = "https://docs.google.com/spreadsheets/d/sheet-id/edit"
    headers = {"content-type": "text/html; charset=utf-8"}
    content = b""
    text = (
        '<html><script>var boot={"sheets":['
        '{"sheetId":0,"title":"Hầm"},'
        '{"sheetId":123,"title":"Tầng 1"},'
        '{"sheetId":456,"title":"Tầng 2"}'
        ']};</script></html>'
    )


class _BinaryResponse:
    status_code = 200
    url = "https://docs.google.com/spreadsheets/d/sheet-id/export?format=xlsx"
    headers = {"content-type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}
    text = ""

    def __init__(self, content: bytes):
        self.content = content


def _workbook_bytes() -> bytes:
    wb = Workbook()
    first = wb.active
    first.title = "Hầm"
    for ws_name, pct in (("Hầm", "25%"), ("Tầng 1", "50%"), ("Tầng 2", "75%")):
        ws = first if ws_name == "Hầm" else wb.create_sheet(ws_name)
        ws.append(["Công tác", "Zone 1", "Zone 2"])
        ws.append([f"Thi công {ws_name}", pct, "100%"])
    buffer = io.BytesIO()
    wb.save(buffer)
    wb.close()
    return buffer.getvalue()


class ContractorDataHubPublicDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = _SqliteDB(Path(self.tmp.name) / "hub.db")

    def tearDown(self):
        self.tmp.cleanup()

    def _source(self):
        return {
            "name": "Sản lượng công khai",
            "category": "PRODUCTION",
            "external_id": "sheet-id",
            "source_url": "https://docs.google.com/spreadsheets/d/sheet-id/edit#gid=0",
            "worksheet_names": ["__gid__:0"],
        }

    def test_public_xlsx_export_syncs_all_worksheets_with_real_titles(self):
        service = ContractorDataHubService(self.db, client=None)
        response = _BinaryResponse(_workbook_bytes())
        with patch("qlda.application.contractor_data_hub.requests.get", return_value=response):
            records, production_points, snapshot = service._public_sheet_records(self._source())

        self.assertEqual(snapshot["worksheet_discovery"], "PUBLIC_XLSX_ALL_TABS")
        self.assertEqual(snapshot["worksheets"], ["Hầm", "Tầng 1", "Tầng 2"])
        self.assertEqual(production_points, 6)
        self.assertEqual(
            {x["worksheet"] for x in records if x["record_type"] == "PRODUCTION"},
            {"Hầm", "Tầng 1", "Tầng 2"},
        )

    def test_html_gid_discovery_remains_fallback_when_xlsx_is_unavailable(self):
        service = ContractorDataHubService(self.db, client=None)

        def values_for_gid(spreadsheet_id: str, gid: int = 0):
            return [
                ["Công tác", "Zone 1", "Zone 2"],
                [f"Thi công {gid}", "50%", "100%"],
            ]

        with patch("qlda.application.contractor_data_hub.requests.get", return_value=_HtmlResponse()), patch.object(
            GoogleSheetsClient,
            "public_values",
            side_effect=values_for_gid,
        ) as public_values:
            records, production_points, snapshot = service._public_sheet_records(self._source())

        self.assertEqual(public_values.call_count, 3)
        self.assertEqual(snapshot["gids"], [0, 123, 456])
        self.assertEqual(snapshot["worksheets"], ["Hầm", "Tầng 1", "Tầng 2"])
        self.assertEqual(snapshot["worksheet_discovery"], "PUBLIC_PAGE_ALL_TABS")
        self.assertEqual(production_points, 6)
        self.assertEqual(
            {x["worksheet"] for x in records if x["record_type"] == "PRODUCTION"},
            {"Hầm", "Tầng 1", "Tầng 2"},
        )


if __name__ == "__main__":
    unittest.main()
