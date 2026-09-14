from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for path in (str(SRC), str(ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)


class V700CleanArchitectureTests(unittest.TestCase):
    def test_version_and_architecture_markers(self):
        import qlda

        self.assertEqual(qlda.__version__, "7.0")
        self.assertEqual(qlda.ARCHITECTURE, "clean-architecture")
        self.assertEqual(qlda.SERVICE_LAYER, "application-use-cases")
        self.assertTrue(qlda.CLEAN_CORE)

    def test_domain_is_pure(self):
        root = SRC / "qlda" / "domain"
        source = "\n".join(path.read_text(encoding="utf-8") for path in root.rglob("*.py"))
        for forbidden in (
            "qlda.infrastructure",
            "qlda.presentation",
            "qlda.services",
            "qlda.modules",
            "qlda.shared",
            "fastapi",
            "streamlit",
        ):
            self.assertNotIn(forbidden, source)

    def test_application_depends_only_inward(self):
        root = SRC / "qlda" / "application"
        source = "\n".join(path.read_text(encoding="utf-8") for path in root.rglob("*.py"))
        for forbidden in (
            "qlda.infrastructure",
            "qlda.presentation",
            "qlda.services",
            "qlda.modules",
            "qlda.shared",
            "fastapi",
            "streamlit",
        ):
            self.assertNotIn(forbidden, source)

    def test_outer_adapters_do_not_call_v6_service_layer_directly(self):
        api_root = SRC / "qlda" / "presentation" / "api"
        api_source = "\n".join(path.read_text(encoding="utf-8") for path in api_root.rglob("*.py"))
        worker_source = (SRC / "qlda" / "modules" / "excel" / "worker.py").read_text(
            encoding="utf-8"
        )
        for source in (api_source, worker_source):
            self.assertNotIn("qlda.services", source)
            self.assertNotIn("qlda.shared.legacy", source)
            self.assertNotIn("_v622", source)
            self.assertNotIn("_v624", source)
        self.assertIn("get_application", api_source)
        self.assertIn("get_application", worker_source)

    def test_legacy_coupling_is_quarantined_in_infrastructure_adapter(self):
        source = (SRC / "qlda" / "infrastructure" / "legacy_adapters.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("qlda.services", source)
        self.assertIn("Anti-corruption adapters", source)

    def test_application_contract_is_importable_without_legacy_runtime(self):
        before = set(sys.modules)
        from qlda.application import ApplicationServices, JobUseCases
        from qlda.domain import ProjectScope

        self.assertTrue(ApplicationServices)
        self.assertTrue(JobUseCases)
        self.assertEqual(ProjectScope(1, 1, 1, "P").workspace_project_id, 1)
        loaded = set(sys.modules) - before
        self.assertFalse(any(name.endswith("_v622") or name.endswith("_v624") for name in loaded))

    def test_fastapi_contract_survives_v7_boundary(self):
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


if __name__ == "__main__":
    unittest.main()
