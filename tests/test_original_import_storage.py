from __future__ import annotations

import base64
import gzip
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import qlda.runtime_core.original_import_storage as store


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
            sys.modules, {"qlda.runtime_core.local_vps_backend": fake}
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

if __name__ == "__main__":
    unittest.main()
