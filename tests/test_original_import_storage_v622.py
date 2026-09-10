from __future__ import annotations

import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import original_import_storage_v622 as store
from build_v621_webopt import _finalize_source
from v622_auth_refresh_v4 import patch_auth_refresh_v4
from v622_boq_multisheet_patch import patch_boq_multisheet
from v622_contractor_access_patch import patch_contractor_access
from v622_contractor_sidebar_patch import patch_contractor_sidebar_ui
from v622_contractor_workspace_patch import patch_contractor_workspace
from v622_ipc_claim_patch import patch_ipc_claims
from v622_legal_qlda_patch import patch_legal_qlda
from v622_local_vps_patch import patch_local_vps
from v622_original_import_patch import PATCH_MARKER, patch_original_import_storage
from v622_report_cost_patch import patch_report_cost
from v622_schedule_management_patch import patch_schedule_management
from v622_single_session_patch import patch_single_session
from v622_vo_claim_patch import patch_vo_claims


class _DB:
    def project(self, project_id):
        return {"id": int(project_id), "code": "E3__NT__NT-01"}


class OriginalImportStorageV622Test(unittest.TestCase):
    def test_archive_uses_same_local_physical_file_store(self):
        calls = []

        def save_bytes(token, **kwargs):
            calls.append((token, kwargs))
            return {
                "id": "f1",
                "name": kwargs["name"],
                "download_url": "https://qlda.test/file/f1?mode=download",
                "history": False,
            }

        def list_record_files(token, **kwargs):
            return {
                "ok": True,
                "files": [
                    {
                        "id": "f1",
                        "name": "BOQ.xlsx",
                        "download_url": "https://qlda.test/file/f1?mode=download",
                        "history": False,
                        "modified_time": "2026-09-10T10:00:00Z",
                    }
                ],
            }

        fake = types.SimpleNamespace(save_bytes=save_bytes, list_record_files=list_record_files)
        with patch.dict(os.environ, {"QLDA_STORAGE_BACKEND": "local"}, clear=False), patch.dict(
            sys.modules, {"local_vps_backend_v622": fake}
        ):
            item = store.archive_original_upload(
                _DB(), 7, "session", "BOQ", "BOQ", "BOQ.xlsx", b"ORIGINAL-BYTES"
            )
            latest = store.latest_original_upload(_DB(), 7, "session", "BOQ", "BOQ")

        self.assertTrue(item["stored"])
        self.assertEqual(len(calls), 1)
        token, kwargs = calls[0]
        self.assertEqual(token, "session")
        self.assertEqual(kwargs["project_code"], "E3__NT__NT-01")
        self.assertEqual(kwargs["kind"], "source")
        self.assertEqual(kwargs["subtype"], "BOQ")
        self.assertEqual(kwargs["record_code"], "BOQ")
        self.assertEqual(kwargs["content"], b"ORIGINAL-BYTES")
        self.assertEqual(kwargs["upload_purpose"], "ORIGINAL_IMPORT")
        self.assertEqual(latest["name"], "BOQ.xlsx")

    def test_non_local_mode_does_not_touch_vps(self):
        with patch.dict(os.environ, {"QLDA_STORAGE_BACKEND": "drive"}, clear=False):
            out = store.archive_original_upload(_DB(), 1, "x", "BOQ", "BOQ", "a.xlsx", b"abc")
        self.assertFalse(out["stored"])

    def test_production_patch_archives_all_business_import_sources(self):
        source = Path("dist/streamlit_app.py").read_text(encoding="utf-8")
        with patch.dict(os.environ, {"QLDA_STORAGE_BACKEND": "local"}, clear=False):
            source = _finalize_source(source)
            source = patch_boq_multisheet(source)
            source = patch_ipc_claims(source)
            source = patch_vo_claims(source)
            source = patch_report_cost(source)
            source = patch_auth_refresh_v4(source)
            source = patch_local_vps(source)
            source = patch_contractor_workspace(source)
            source = patch_contractor_access(source)
            source = patch_single_session(source)
            source = patch_contractor_sidebar_ui(source)
            source = patch_schedule_management(source)
            source = patch_legal_qlda(source)
            source = patch_original_import_storage(source)

        compile(source, "streamlit_app_original_storage_test.py", "exec")
        self.assertIn(PATCH_MARKER, source)
        self.assertIn('"BOQ", "BOQ"', source)
        self.assertIn('"SCHEDULE_MPP", "SCHEDULE"', source)
        self.assertIn('"SCHEDULE_EXCEL", "TASKS"', source)
        self.assertIn("render_ipc_claim_ui_with_original", source)
        self.assertIn("render_vo_ui_with_original", source)
        self.assertIn("session_token=_gateway_session_token()", source)
        self.assertIn("⬇️ Tải workbook BOQ gốc trên VPS", source)
        self.assertIn("⬇️ Tải file MPP gốc trên VPS", source)


if __name__ == "__main__":
    unittest.main()
