from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import qlda
from qlda.infrastructure.database import make_database
from qlda.modules.excel import worker


class ServiceLayerV626Tests(unittest.TestCase):
    def test_version_and_service_layer_marker(self):
        version = tuple(int(x) for x in qlda.__version__.split(".")[:2])
        self.assertGreaterEqual(version, (6, 26))
        if version >= (7, 0):
            self.assertEqual(qlda.ARCHITECTURE, "clean-architecture")
            self.assertEqual(qlda.SERVICE_LAYER, "application-use-cases")

    def test_v75_service_facades_are_retired(self):
        version = tuple(int(x) for x in qlda.__version__.split(".")[:2])
        root = SRC / "qlda" / "services"
        if version >= (7, 5):
            self.assertFalse(root.exists())
        else:
            self.assertTrue(root.exists())
        self.assertTrue(callable(make_database))
        self.assertTrue(callable(worker.main))

    def test_production_worker_keeps_clean_application_boundary(self):
        source = (SRC / "qlda" / "modules" / "excel" / "worker.py").read_text(encoding="utf-8")
        self.assertIn("get_application", source)
        self.assertNotIn("from qlda.services", source)
        self.assertNotIn("from qlda.infrastructure", source)
        for forbidden in (
            "excel_worker_v624", "boq_background_v624", "boq_persist_v624",
            "ipc_background_v624", "ipc_persist_v624", "vo_background_v624",
            "vo_persist_v624", "schedule_background_v624", "schedule_persist_v624",
            "local_vps_backend_v622",
        ):
            self.assertNotIn(forbidden, source)

    def test_native_excel_owns_import_dispatch_after_v75(self):
        version = tuple(int(x) for x in qlda.__version__.split(".")[:2])
        if version < (7, 5):
            self.skipTest("V7.5 check")
        source = (SRC / "qlda" / "infrastructure" / "native_excel.py").read_text(encoding="utf-8")
        self.assertIn("qlda.import_engines", source)
        self.assertNotIn("qlda.runtime", source)
        self.assertNotIn("qlda.services", source)
        self.assertNotIn("qlda.modules", source)


if __name__ == "__main__":
    unittest.main()
