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


class V730NativeAITests(unittest.TestCase):
    def test_version_and_adapter_inventory(self):
        import qlda

        version = tuple(int(x) for x in qlda.__version__.split(".")[:2])
        self.assertGreaterEqual(version, (7, 3))
        self.assertIn("ai", qlda.NATIVE_ADAPTERS)
        if version >= (7, 4):
            self.assertIn("excel", qlda.NATIVE_ADAPTERS)
            self.assertEqual(qlda.LEGACY_ADAPTERS, ())
        else:
            self.assertEqual(qlda.LEGACY_ADAPTERS, ("excel",))

    def test_native_ai_boundary_has_no_deprecated_service_dependency(self):
        import qlda

        version = tuple(int(x) for x in qlda.__version__.split(".")[:2])
        path = SRC / "qlda" / "infrastructure" / "native_ai.py"
        source = path.read_text(encoding="utf-8")
        modules = imports_of(path)
        self.assertIn("qlda.domain.errors", modules)
        if version >= (7, 6):
            self.assertTrue(any(m == "qlda.runtime_core" or m.startswith("qlda.runtime_core.") for m in modules))
            self.assertFalse(any(m == "qlda.runtime" or m.startswith("qlda.runtime.") for m in modules))
        else:
            self.assertIn("qlda.runtime", modules)
        for forbidden in ("qlda.services", "qlda.shared.legacy"):
            self.assertFalse(any(m == forbidden or m.startswith(forbidden + ".") for m in modules))
        self.assertNotIn("LegacyAIAdapter", source)
        self.assertNotIn("load_module(", source)
        if version >= (7, 6):
            access = SRC / "qlda" / "runtime_core" / "contractor_access_control.py"
        else:
            access = ROOT / "contractor_access_control_v622.py"
        self.assertIn("ContextVar", access.read_text(encoding="utf-8"))

    def test_composition_wires_native_ai_without_eager_engine_bootstrap(self):
        from qlda.bootstrap import get_application, reset_application

        before = set(sys.modules)
        reset_application()
        app = get_application()
        loaded = set(sys.modules) - before
        self.assertEqual(app.ai._port.__class__.__name__, "NativeAIAdapter")
        self.assertNotIn("ai_service", loaded)
        self.assertNotIn("contractor_access_control_v622", loaded)

    def test_ai_error_mapping_preserves_domain_metadata(self):
        from qlda.infrastructure.native_ai import NativeAIAdapter

        class LegacyError(RuntimeError):
            code = "rate_limit"
            retryable = True
            action = "Thử lại sau"

        error = NativeAIAdapter._domain_error(LegacyError("Quá giới hạn"))
        self.assertEqual(error.code, "rate_limit")
        self.assertTrue(error.retryable)
        self.assertEqual(error.action, "Thử lại sau")
        self.assertEqual(str(error), "Quá giới hạn")

    def test_legacy_ai_and_access_facades_are_removed(self):
        import qlda

        service_root = SRC / "qlda" / "services"
        self.assertFalse((service_root / "ai.py").exists())
        self.assertFalse((service_root / "access.py").exists())
        legacy = SRC / "qlda" / "infrastructure" / "legacy_adapters.py"
        version = tuple(int(x) for x in qlda.__version__.split(".")[:2])
        if version >= (7, 4):
            self.assertFalse(legacy.exists())
        else:
            source = legacy.read_text(encoding="utf-8")
            self.assertNotIn("LegacyAIAdapter", source)
            self.assertIn("LegacyExcelImportAdapter", source)

    def test_fastapi_ai_contract_is_unchanged(self):
        from qlda.presentation.api.app import app

        paths = set(app.openapi().get("paths", {}))
        expected = {
            "/api/v1/ai/ask",
            "/api/v1/ai/schedule-risk",
            "/api/v1/ai/report",
            "/api/v1/ai/legal",
            "/api/v1/ai/test",
        }
        self.assertTrue(expected.issubset(paths), expected - paths)


if __name__ == "__main__":
    unittest.main()
