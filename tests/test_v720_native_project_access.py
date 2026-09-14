from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for path in (str(SRC), str(ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)


def imports_of(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


class V720NativeProjectAccessTests(unittest.TestCase):
    def test_version_and_adapter_inventory(self):
        import qlda

        self.assertEqual(qlda.__version__, "7.2")
        self.assertIn("project-access", qlda.NATIVE_ADAPTERS)
        self.assertEqual(qlda.LEGACY_ADAPTERS, ("ai", "excel"))

    def test_project_access_adapter_is_native_and_import_clean(self):
        path = SRC / "qlda" / "infrastructure" / "native_project_access.py"
        source = path.read_text(encoding="utf-8")
        modules = imports_of(path)
        self.assertIn("qlda.infrastructure.postgres", modules)
        for forbidden in ("qlda.services", "qlda.shared.legacy"):
            self.assertFalse(any(m == forbidden or m.startswith(forbidden + ".") for m in modules))
        self.assertNotIn("load_module(", source)
        self.assertNotIn("_v622", source)
        self.assertIn("project_contractors", source)
        self.assertIn("project_user_contractor_access", source)
        self.assertIn("ON CONFLICT", source)

    def test_composition_uses_native_project_access(self):
        from qlda.bootstrap import get_application, reset_application

        reset_application()
        app = get_application()
        self.assertEqual(app.access._port.__class__.__name__, "NativeProjectAccessAdapter")

    def test_legacy_adapter_surface_is_only_ai_and_excel(self):
        source = (SRC / "qlda" / "infrastructure" / "legacy_adapters.py").read_text(encoding="utf-8")
        self.assertNotIn("LegacyProjectAccessAdapter", source)
        self.assertIn("LegacyAIAdapter", source)
        self.assertIn("LegacyExcelImportAdapter", source)

    def test_v621_build_artifacts_are_removed_but_runtime_regression_is_kept(self):
        self.assertFalse((ROOT / "build_v621_webopt.py").exists())
        self.assertFalse((ROOT / "v621_webopt_source").exists())
        self.assertTrue((ROOT / "v621_webopt_runtime.py").exists())
        self.assertTrue((ROOT / "tests" / "test_v621_webopt_runtime.py").exists())

    def test_fastapi_contract_is_unchanged(self):
        from qlda.presentation.api.app import app

        paths = set(app.openapi().get("paths", {}))
        expected = {
            "/api/health", "/api/v1/jobs", "/api/v1/jobs/enqueue",
            "/api/v1/files", "/api/v1/files/upload-ticket",
            "/api/v1/ai/ask", "/api/v1/ai/schedule-risk", "/api/v1/search",
        }
        self.assertTrue(expected.issubset(paths), expected - paths)


if __name__ == "__main__":
    unittest.main()
