from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

from qlda.application.contractor_data_hub import ContractorDataHubAI, ContractorDataHubService
from qlda.infrastructure.contractor_data_hub import ContractorDataHubRepository
from qlda.infrastructure.google_sheets.drive import GoogleWorkspaceClient, parse_drive_folder_id
import qlda.runtime_core.google_connection_store as google_connection_store


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


class ContractorDataHubV1V5Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = _SqliteDB(Path(self.tmp.name) / "hub.db")
        self.repo = ContractorDataHubRepository(self.db)
        self.c1 = {
            "id": 11,
            "workspace_project_id": 101,
            "contractor_code": "SME",
            "contractor_name": "SME MEP",
        }
        self.c2 = {
            "id": 12,
            "workspace_project_id": 102,
            "contractor_code": "PCCC",
            "contractor_name": "Nhà thầu PCCC",
        }
        self.s1 = self.repo.ensure_space(1, self.c1)
        self.s2 = self.repo.ensure_space(1, self.c2)

    def tearDown(self):
        self.tmp.cleanup()

    def _source(self, space, name):
        source_id = self.repo.save_source(
            space["data_space_id"],
            source_kind="SHEET",
            name=name,
            external_id="1AbCdEfGhIjKlMnOpQrStUvWxYz123456789",
            category="PRODUCTION",
        )
        return next(x for x in self.repo.list_sources(space["data_space_id"]) if x["source_id"] == source_id)

    def test_v1_each_contractor_has_isolated_data_space(self):
        self.assertNotEqual(self.s1["data_space_id"], self.s2["data_space_id"])
        self.assertEqual(self.s1["workspace_project_id"], 101)
        self.assertEqual(self.s2["workspace_project_id"], 102)

        source1 = self._source(self.s1, "SME Production")
        source2 = self._source(self.s2, "PCCC Production")
        self.repo.replace_records(source1, [{
            "record_type": "PRODUCTION",
            "category": "PRODUCTION",
            "worksheet": "Hầm",
            "work_item": "Ống cấp nước",
            "zone": "Zone 1",
            "progress_percent": 75.0,
            "content": "SME only",
        }])
        self.repo.replace_records(source2, [{
            "record_type": "PRODUCTION",
            "category": "PRODUCTION",
            "worksheet": "Hầm",
            "work_item": "Ống PCCC",
            "zone": "Zone 1",
            "progress_percent": 45.0,
            "content": "PCCC only",
        }])

        sme = self.repo.records(1, workspace_ids=[101])
        pccc = self.repo.records(1, workspace_ids=[102])
        self.assertEqual(len(sme), 1)
        self.assertEqual(len(pccc), 1)
        self.assertEqual(sme[0]["content"], "SME only")
        self.assertEqual(pccc[0]["content"], "PCCC only")

    def test_v2_google_oauth_requests_sheets_and_drive_readonly(self):
        with patch.dict(os.environ, {
            "GOOGLE_OAUTH_CLIENT_ID": "client.apps.googleusercontent.com",
            "GOOGLE_OAUTH_CLIENT_SECRET": "secret",
            "GOOGLE_OAUTH_REDIRECT_URI": "https://qldaxd.id.vn",
        }, clear=False):
            url = GoogleWorkspaceClient.build_authorization_url("state")
        self.assertIn("spreadsheets.readonly", url)
        self.assertIn("drive.readonly", url)
        self.assertIn("access_type=offline", url)
        self.assertEqual(
            parse_drive_folder_id("https://drive.google.com/drive/folders/ABCdef_123456789"),
            "ABCdef_123456789",
        )

    def test_v3_snapshot_only_created_when_source_content_changes(self):
        source = self._source(self.s1, "SME Production")
        record = [{
            "record_type": "PRODUCTION",
            "category": "PRODUCTION",
            "worksheet": "S3",
            "work_item": "Ống gió",
            "zone": "Zone 2",
            "progress_percent": 50.0,
            "content": "Tiến độ 50%",
        }]
        first = self.repo.replace_records(source, record)
        second = self.repo.replace_records(source, record)
        changed = dict(record[0], progress_percent=75.0, content="Tiến độ 75%")
        third = self.repo.replace_records(source, [changed])
        self.assertTrue(first["snapshot_created"])
        self.assertFalse(second["snapshot_created"])
        self.assertTrue(third["snapshot_created"])

    def test_v4_ai_retrieval_respects_workspace_scope(self):
        source1 = self._source(self.s1, "SME")
        source2 = self._source(self.s2, "PCCC")
        self.repo.replace_records(source1, [{
            "record_type": "SHEET_ROW", "category": "MATERIAL", "content": "SME van bướm đã giao"
        }])
        self.repo.replace_records(source2, [{
            "record_type": "SHEET_ROW", "category": "MATERIAL", "content": "PCCC đầu phun chưa giao"
        }])
        ai = ContractorDataHubAI(self.db)
        rows = ai.retrieve(1, "vật tư giao", workspace_ids=[101], limit=20)
        text = " ".join(str(x.get("content") or "") for x in rows)
        self.assertIn("SME", text)
        self.assertNotIn("PCCC", text)

    def test_v5_project_context_scans_both_authorized_warehouses(self):
        source1 = self._source(self.s1, "SME")
        source2 = self._source(self.s2, "PCCC")
        self.repo.replace_records(source1, [{
            "record_type": "PRODUCTION", "category": "PRODUCTION", "worksheet": "Hầm",
            "work_item": "MEP", "zone": "Zone 1", "progress_percent": 80.0, "content": "SME 80%"
        }])
        self.repo.replace_records(source2, [{
            "record_type": "PRODUCTION", "category": "PRODUCTION", "worksheet": "Hầm",
            "work_item": "PCCC", "zone": "Zone 1", "progress_percent": 40.0, "content": "PCCC 40%"
        }])
        context = ContractorDataHubAI(self.db).build_context(1, "so sánh sản lượng", workspace_ids=[101, 102])
        self.assertIn("WAREHOUSE:SME", context)
        self.assertIn("WAREHOUSE:PCCC", context)
        self.assertIn("SME 80%", context)
        self.assertIn("PCCC 40%", context)

    def test_encrypted_refresh_token_is_not_stored_plaintext(self):
        connection_path = Path(self.tmp.name) / "connections.json"
        with patch.object(google_connection_store, "CONNECTION_FILE", connection_path), patch.dict(
            os.environ,
            {"QLDA_SETTINGS_MASTER_KEY": "unit-test-master-secret"},
            clear=False,
        ):
            google_connection_store.save_project_connection(
                1,
                {
                    "access_token": "access-token-secret",
                    "refresh_token": "refresh-token-secret",
                    "expires_at": 9999999999,
                    "token_type": "Bearer",
                },
                account_email="qlda@example.com",
                scopes=[GoogleWorkspaceClient.SHEETS_SCOPE, GoogleWorkspaceClient.DRIVE_SCOPE],
            )
            raw = connection_path.read_text(encoding="utf-8")
            self.assertNotIn("refresh-token-secret", raw)
            self.assertNotIn("access-token-secret", raw)
            loaded = google_connection_store.load_project_connection(1)
            self.assertEqual(loaded["token_state"]["refresh_token"], "refresh-token-secret")
            self.assertEqual(loaded["account_email"], "qlda@example.com")

    def test_sheet_parser_generates_structured_production_records(self):
        source = {"name": "Theo dõi sản lượng", "category": "PRODUCTION"}
        rows = [
            ["BẢNG KHỐI LƯỢNG"],
            ["Công tác", "Zone 1", "Zone 2"],
            ["Thi công ống", "50%", "100%"],
        ]
        records = ContractorDataHubService._production_records(
            source,
            "Hầm",
            rows,
            external_item_id="sheet-id",
        )
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["record_type"], "PRODUCTION")
        self.assertEqual(records[0]["work_item"], "Thi công ống")
        self.assertEqual(records[1]["progress_percent"], 100.0)


if __name__ == "__main__":
    unittest.main()
