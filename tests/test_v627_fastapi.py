from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for path in (str(SRC), str(ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)


class V627FastAPITests(unittest.TestCase):
    def test_version_and_api_marker(self):
        import qlda

        version = tuple(int(x) for x in qlda.__version__.split(".")[:2])
        self.assertGreaterEqual(version, (6, 27))
        self.assertEqual(qlda.HTTP_API, "fastapi")

    def test_expected_routes_exist(self):
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

    def test_http_adapter_has_no_streamlit_or_direct_legacy_imports(self):
        api_root = SRC / "qlda" / "presentation" / "api"
        source = "\n".join(path.read_text(encoding="utf-8") for path in api_root.rglob("*.py"))
        self.assertNotIn("import streamlit", source)
        self.assertNotIn("qlda.shared.legacy", source)
        self.assertNotIn("_v622", source)
        self.assertNotIn("_v624", source)

    def test_service_boundaries_exist(self):
        for name in ("auth.py", "access.py", "ai.py", "search.py"):
            self.assertTrue((SRC / "qlda" / "services" / name).exists(), name)
        self.assertTrue((SRC / "qlda" / "domain" / "ports.py").exists())
        self.assertTrue((SRC / "qlda" / "application" / "services.py").exists())

    def test_vps_runtime_is_separate(self):
        service = (ROOT / "vps" / "qlda-api.service").read_text(encoding="utf-8")
        nginx = (ROOT / "vps" / "nginx.conf.template").read_text(encoding="utf-8")
        reconcile = (ROOT / "vps" / "reconcile_api_v627.sh").read_text(encoding="utf-8")
        self.assertIn("qlda.presentation.api.app:app", service)
        self.assertIn("--host 127.0.0.1 --port 8001", service)
        self.assertIn("location /api/", nginx)
        self.assertIn("127.0.0.1:8001", nginx)
        self.assertIn("/api/health", reconcile)
        self.assertIn("qlda-api.service", reconcile)


if __name__ == "__main__":
    unittest.main()
