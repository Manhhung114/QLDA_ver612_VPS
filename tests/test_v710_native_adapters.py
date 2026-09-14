from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for path in (str(SRC), str(ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)


class V710NativeAdaptersTests(unittest.TestCase):
    def test_version_and_adapter_markers(self):
        import qlda

        self.assertEqual(qlda.__version__, "7.1")
        self.assertEqual(qlda.ARCHITECTURE, "clean-architecture")
        self.assertEqual(
            qlda.NATIVE_ADAPTERS,
            ("sessions", "files", "jobs", "search"),
        )
        self.assertEqual(
            qlda.LEGACY_ADAPTERS,
            ("project-access", "ai", "excel"),
        )

    def test_native_adapters_have_no_service_or_legacy_module_dependency(self):
        infrastructure = SRC / "qlda" / "infrastructure"
        for name in ("native_session.py", "native_files.py", "native_jobs.py", "native_search.py"):
            source = (infrastructure / name).read_text(encoding="utf-8")
            for forbidden_import in (
                "from qlda.services",
                "import qlda.services",
                "from qlda.shared.legacy",
                "import qlda.shared.legacy",
                "load_module(",
                "from local_vps_backend_v622",
                "import local_vps_backend_v622",
                "from excel_jobs_v624",
                "import excel_jobs_v624",
            ):
                self.assertNotIn(forbidden_import, source, name)

    def test_legacy_adapter_surface_is_reduced_to_three_business_heavy_areas(self):
        source = (
            SRC / "qlda" / "infrastructure" / "legacy_adapters.py"
        ).read_text(encoding="utf-8")
        for retired in (
            "LegacySessionAdapter",
            "LegacyFileAdapter",
            "LegacyJobAdapter",
            "LegacySearchAdapter",
        ):
            self.assertNotIn(retired, source)
        for remaining in (
            "LegacyProjectAccessAdapter",
            "LegacyAIAdapter",
            "LegacyExcelImportAdapter",
        ):
            self.assertIn(remaining, source)

    def test_composition_root_wires_native_adapters_without_db_side_effect(self):
        from qlda.bootstrap import get_application, reset_application

        reset_application()
        app = get_application()
        self.assertEqual(app.sessions._port.__class__.__name__, "NativeSessionAdapter")
        self.assertEqual(app.files._port.__class__.__name__, "NativeFileAdapter")
        self.assertEqual(app.jobs._port.__class__.__name__, "NativeJobAdapter")
        self.assertEqual(app.search._port.__class__.__name__, "NativeSearchAdapter")
        self.assertEqual(app.access._port.__class__.__name__, "LegacyProjectAccessAdapter")
        self.assertEqual(app.ai._port.__class__.__name__, "LegacyAIAdapter")
        self.assertEqual(app.excel._port.__class__.__name__, "LegacyExcelImportAdapter")

    def test_job_upload_purpose_remains_v624_wire_compatible(self):
        from qlda.infrastructure.native_jobs import NativeJobAdapter

        jobs = NativeJobAdapter()
        purpose = jobs.build_upload_purpose(
            "BOQ_IMPORT",
            7,
            workspace_project_id=9,
            status_date="2026-09-14",
        )
        self.assertTrue(purpose.startswith("QLDA_EXCEL_JOB|V624|BOQ|7|9|"))
        parsed = jobs.parse_upload_purpose(purpose)
        self.assertEqual(parsed["job_type"], "BOQ")
        self.assertEqual(parsed["project_id"], 7)
        self.assertEqual(parsed["workspace_project_id"], 9)
        self.assertEqual(parsed["options"]["status_date"], "2026-09-14")

        legacy = jobs.parse_upload_purpose(
            'QLDA_EXCEL_JOB|BOQ_IMPORT|11|{"status_date":"2026-09-15"}'
        )
        self.assertEqual(legacy["job_type"], "BOQ")
        self.assertEqual(legacy["workspace_project_id"], 11)

    def test_native_file_public_contract_contains_project_linkage_for_job_enqueue(self):
        from qlda.infrastructure.native_files import file_public

        with patch.dict(os.environ, {"QLDA_PUBLIC_BASE_URL": ""}, clear=False):
            data = file_public(
                {
                    "id": "abc123",
                    "project_code": "DA-01",
                    "kind": "BOQ",
                    "subtype": "Khac",
                    "record_code": "Chung",
                    "name": "boq.xlsx",
                    "mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    "size": 1234,
                    "sha256": "deadbeef",
                    "storage_path": "projects/DA-01/BOQ/Khac/Chung/abc123__boq.xlsx",
                    "history": False,
                }
            )
        self.assertEqual(data["project_code"], "DA-01")
        self.assertEqual(data["sha256"], "deadbeef")
        self.assertEqual(data["id"], "abc123")

    def test_fastapi_contract_is_unchanged(self):
        from qlda.presentation.api.app import app

        paths = set(app.openapi().get("paths", {}))
        expected = {
            "/api/health",
            "/api/v1/jobs",
            "/api/v1/jobs/enqueue",
            "/api/v1/files",
            "/api/v1/files/upload-ticket",
            "/api/v1/ai/ask",
            "/api/v1/ai/schedule-risk",
            "/api/v1/search",
        }
        self.assertTrue(expected.issubset(paths), expected - paths)
        self.assertEqual(app.docs_url, "/api/docs")
        self.assertEqual(app.openapi_url, "/api/openapi.json")


if __name__ == "__main__":
    unittest.main()
