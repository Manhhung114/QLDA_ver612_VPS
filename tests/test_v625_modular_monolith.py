from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import qlda
from qlda.module_registry import MODULES, module_names
from qlda.runtime import REPO_ROOT, ensure_repo_root_on_path


class ModularMonolithV625Tests(unittest.TestCase):
    def test_package_version_and_repo_root(self):
        version = tuple(int(x) for x in qlda.__version__.split(".")[:2])
        self.assertGreaterEqual(version, (6, 25))
        self.assertEqual(REPO_ROOT, ROOT)
        self.assertEqual(ensure_repo_root_on_path(), ROOT)
        if version >= (7, 0):
            self.assertEqual(qlda.ARCHITECTURE, "clean-architecture")

    def test_business_boundaries_registered(self):
        self.assertEqual(module_names(), tuple(MODULES))
        self.assertEqual(set(MODULES), {"boq", "ipc", "vo", "schedule", "excel"})
        version = tuple(int(x) for x in qlda.__version__.split(".")[:2])
        if version >= (7, 5):
            for name in ("boq", "ipc", "vo", "schedule"):
                self.assertEqual(MODULES[name].migration_state, "native-engine")
                self.assertEqual(MODULES[name].package, "qlda.import_engines")
            self.assertEqual(MODULES["excel"].migration_state, "native-entrypoint")
        else:
            for name in ("boq", "ipc", "vo", "schedule"):
                self.assertEqual(MODULES[name].migration_state, "compatibility-facade")

    def test_v75_retires_old_module_facades(self):
        version = tuple(int(x) for x in qlda.__version__.split(".")[:2])
        if version < (7, 5):
            self.skipTest("V7.5 retirement check")
        modules_root = SRC / "qlda" / "modules"
        for name in ("boq", "ipc", "vo", "schedule"):
            self.assertFalse((modules_root / name).exists(), name)
        self.assertFalse((modules_root / "excel" / "jobs.py").exists())
        self.assertTrue((modules_root / "excel" / "worker.py").exists())

    def test_core_package_has_no_streamlit_dependency(self):
        for path in (SRC / "qlda").rglob("*.py"):
            if "presentation" in path.parts:
                continue
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("import streamlit", source, str(path))
            self.assertNotIn("from streamlit", source, str(path))

    def test_worker_systemd_uses_modular_entrypoint(self):
        service = (ROOT / "vps" / "qlda-excel-worker.service").read_text(encoding="utf-8")
        self.assertIn("PYTHONPATH=/opt/qlda/app/src:/opt/qlda/app", service)
        self.assertIn("-m qlda.modules.excel.worker --poll-seconds 2", service)


if __name__ == "__main__":
    unittest.main()
