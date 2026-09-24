from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from qlda.infrastructure.contractor_data_hub import ContractorDataHubRepository


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


class ContractorDataHubDuplicateGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = _SqliteDB(Path(self.tmp.name) / "hub.db")
        self.repo = ContractorDataHubRepository(self.db)
        self.space = self.repo.ensure_space(
            1,
            {
                "id": 11,
                "workspace_project_id": 101,
                "contractor_code": "SME",
                "contractor_name": "SME MEP",
            },
        )

    def tearDown(self):
        self.tmp.cleanup()

    def _source(self, *, source_id: str = "", mode: str = "GOOGLE_OAUTH") -> dict:
        sid = self.repo.save_source(
            self.space["data_space_id"],
            source_id=source_id,
            source_kind="SHEET",
            name="Theo dõi sản lượng",
            external_id="1AbCdEfGhIjKlMnOpQrStUvWxYz123456789",
            source_url="https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz123456789/edit",
            category="AUTO",
            access_mode=mode,
        )
        return next(
            x for x in self.repo.list_sources(self.space["data_space_id"])
            if x["source_id"] == sid
        )

    def test_same_row_zone_with_different_semantics_no_longer_collides(self):
        source = self._source()
        rows = [
            {
                "external_item_id": source["external_id"],
                "worksheet": "Hầm",
                "record_type": "PRODUCTION",
                "record_ref": "Hầm:row:10:Zone 1:a",
                "source_row": 10,
                "zone": "Zone 1",
                "work_item": "Ống cấp nước",
                "progress_percent": 50.0,
                "content": "Ống cấp nước | Zone 1 | 50%",
            },
            {
                "external_item_id": source["external_id"],
                "worksheet": "Hầm",
                "record_type": "PRODUCTION",
                "record_ref": "Hầm:row:10:Zone 1:b",
                "source_row": 10,
                "zone": "Zone 1",
                "work_item": "Ống thoát nước",
                "progress_percent": 75.0,
                "content": "Ống thoát nước | Zone 1 | 75%",
            },
        ]
        result = self.repo.replace_records(source, rows)
        self.assertEqual(result["records"], 2)
        stored = self.repo.records(1, workspace_ids=[101], limit=20)
        self.assertEqual(len(stored), 2)

    def test_exact_duplicate_input_is_skipped_before_insert(self):
        source = self._source()
        row = {
            "external_item_id": source["external_id"],
            "worksheet": "S3",
            "record_type": "SHEET_ROW",
            "record_ref": "row:22",
            "source_row": 22,
            "content": "Công tác=Ống gió | Zone 2=100%",
        }
        result = self.repo.replace_records(source, [row, dict(row)])
        self.assertEqual(result["records"], 1)
        self.assertEqual(result["duplicates_skipped"], 1)

    def test_failed_public_source_is_upgraded_in_place_when_oauth_is_added(self):
        public = self._source(source_id="public-source", mode="PUBLIC_LINK")
        self.repo.mark_source_error(public["source_id"], "gviz: HTTP 401; export: HTTP 401")
        oauth_id = self.repo.save_source(
            self.space["data_space_id"],
            source_kind="SHEET",
            name="Theo dõi sản lượng",
            external_id=public["external_id"],
            source_url=public["source_url"],
            category="AUTO",
            access_mode="GOOGLE_OAUTH",
        )
        self.assertEqual(oauth_id, "public-source")
        sources = self.repo.list_sources(self.space["data_space_id"])
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["access_mode"], "GOOGLE_OAUTH")

    def test_successful_oauth_sync_removes_failed_public_duplicate(self):
        public = self._source(source_id="public-old", mode="PUBLIC_LINK")
        self.repo.mark_source_error(public["source_id"], "Google Sheet ẩn danh gviz HTTP 401")
        oauth = self._source(source_id="oauth-new", mode="GOOGLE_OAUTH")
        result = self.repo.replace_records(
            oauth,
            [{
                "external_item_id": oauth["external_id"],
                "worksheet": "Hầm",
                "record_type": "SHEET_ROW",
                "record_ref": "row:2",
                "source_row": 2,
                "content": "Công tác=Test áp",
            }],
        )
        self.assertEqual(result["legacy_public_sources_removed"], 1)
        sources = self.repo.list_sources(self.space["data_space_id"])
        self.assertEqual([x["source_id"] for x in sources], ["oauth-new"])


if __name__ == "__main__":
    unittest.main()
