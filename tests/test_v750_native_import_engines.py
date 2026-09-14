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


class V750NativeImportEnginesTests(unittest.TestCase):
    def test_version_and_import_engine_marker(self):
        import qlda
        version = tuple(int(x) for x in qlda.__version__.split(".")[:2])
        self.assertGreaterEqual(version, (7, 5))
        self.assertEqual(qlda.IMPORT_ENGINE_LAYER, "native-packaged")
        self.assertEqual(qlda.LEGACY_ADAPTERS, ())

    def test_native_excel_no_longer_uses_legacy_runtime_import(self):
        path = SRC / "qlda" / "infrastructure" / "native_excel.py"
        modules = imports_of(path)
        self.assertIn("qlda.import_engines", modules)
        self.assertNotIn("qlda.runtime", modules)
        self.assertFalse(any(m == "qlda.services" or m.startswith("qlda.services.") for m in modules))
        self.assertFalse(any(m == "qlda.modules" or m.startswith("qlda.modules.") for m in modules))
        self.assertFalse(any(m == "qlda.shared.legacy" or m.startswith("qlda.shared.legacy.") for m in modules))

    def test_all_import_engines_are_packaged_under_src(self):
        root = SRC / "qlda" / "import_engines"
        expected = {
            "boq_background.py", "boq_persistence.py", "boq_snapshot.py",
            "ipc_background.py", "ipc_persistence.py",
            "vo_background.py", "vo_persistence.py",
            "schedule_background.py", "schedule_persistence.py",
        }
        self.assertTrue(expected.issubset({p.name for p in root.glob("*.py")}))
        loader_modules = imports_of(root / "loader.py")
        self.assertNotIn("qlda.runtime", loader_modules)
        self.assertIn("importlib", loader_modules)

    def test_v625_v626_facades_are_removed(self):
        self.assertFalse((SRC / "qlda" / "services").exists())
        for name in ("boq", "ipc", "vo", "schedule"):
            self.assertFalse((SRC / "qlda" / "modules" / name).exists(), name)
        self.assertFalse((SRC / "qlda" / "modules" / "excel" / "jobs.py").exists())
        self.assertFalse((SRC / "qlda" / "shared" / "legacy.py").exists())

    def test_boq_transaction_verification_contract_is_preserved(self):
        source = (SRC / "qlda" / "import_engines" / "boq_persistence.py").read_text(encoding="utf-8")
        for token in ("expected_rows", "scanned_rows", "prepared_rows", "written_rows", "failed_rows", "rollback"):
            self.assertIn(token, source)

    def test_ipc_revision_and_idempotency_contract_is_preserved(self):
        source = (SRC / "qlda" / "import_engines" / "ipc_persistence.py").read_text(encoding="utf-8")
        for token in ("old_batch", "latest_revision", "new_revision", "approved_amount", "payment_status", "rollback"):
            self.assertIn(token, source)

    def test_vo_revision_and_business_fields_are_preserved(self):
        source = (SRC / "qlda" / "import_engines" / "vo_persistence.py").read_text(encoding="utf-8")
        for token in ("old_batch", "revision_no", "approved_amount", "funding_source", "status", "rollback"):
            self.assertIn(token, source)

    def test_schedule_only_replaces_background_excel_tasks(self):
        source = (SRC / "qlda" / "import_engines" / "schedule_persistence.py").read_text(encoding="utf-8")
        self.assertIn('SOURCE_PREFIX = "schedule_excel:"', source)
        self.assertIn("source_type LIKE", source)
        self.assertIn("SOURCE_PREFIX + \"%\"", source)

    def test_worker_marker_and_entrypoint(self):
        import qlda
        worker = (SRC / "qlda" / "modules" / "excel" / "worker.py").read_text(encoding="utf-8")
        service = (ROOT / "vps" / "qlda-excel-worker.service").read_text(encoding="utf-8")
        version = tuple(int(x) for x in qlda.__version__.split(".")[:2])
        marker = "V7.6 PACKAGED NATIVE IMPORT WORKER" if version >= (7, 6) else "V7.5 NATIVE IMPORT ENGINE WORKER"
        self.assertIn(marker, worker)
        self.assertIn("-m qlda.modules.excel.worker --poll-seconds 2", service)

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
