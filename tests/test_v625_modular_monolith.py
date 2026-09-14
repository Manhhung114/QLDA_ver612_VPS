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


class ModularMonolithV625Tests(unittest.TestCase):
    def test_package_version_and_repo_root(self):
        version = tuple(int(x) for x in qlda.__version__.split(".")[:2])
        self.assertGreaterEqual(version, (6, 25))
        self.assertTrue((ROOT / "src" / "qlda").is_dir())
        if version >= (7, 0):
            self.assertEqual(qlda.ARCHITECTURE, "clean-architecture")
        if version >= (7, 6):
            self.assertFalse((SRC / "qlda" / "runtime.py").exists())
            self.assertFalse(qlda.LEGACY_RUNTIME)

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

    def test_clean_core_has_no_streamlit_dependency(self):
        for layer in ("domain", "application", "infrastructure"):
            for path in (SRC / "qlda" / layer).rglob("*.py"):
                source = path.read_text(encoding="utf-8")
                self.assertNotIn("import streamlit", source, str(path))
                self.assertNotIn("from streamlit", source, str(path))

    def test_worker_systemd_uses_packaged_entrypoint(self):
        service = (ROOT / "vps" / "qlda-excel-worker.service").read_text(encoding="utf-8")
        version = tuple(int(x) for x in qlda.__version__.split(".")[:2])
        if version >= (7, 6):
            self.assertIn("PYTHONPATH=/opt/qlda/app/src", service)
            self.assertNotIn("PYTHONPATH=/opt/qlda/app/src:/opt/qlda/app", service)
        else:
            self.assertIn("PYTHONPATH=/opt/qlda/app/src:/opt/qlda/app", service)
        self.assertIn("-m qlda.modules.excel.worker --poll-seconds 2", service)


if __name__ == "__main__":
    unittest.main()
