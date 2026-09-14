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
from qlda.modules import boq, ipc, schedule, vo
from qlda.modules.excel import jobs, worker
from qlda.runtime import REPO_ROOT, ensure_repo_root_on_path


class ModularMonolithV625Tests(unittest.TestCase):
    def test_package_version_and_repo_root(self):
        version = tuple(int(x) for x in qlda.__version__.split(".")[:2])
        self.assertGreaterEqual(version, (6, 25))
        self.assertIn(qlda.ARCHITECTURE, {"modular-monolith", "clean-architecture"})
        if version >= (7, 0):
            self.assertEqual(qlda.ARCHITECTURE, "clean-architecture")
        self.assertEqual(REPO_ROOT, ROOT)
        self.assertEqual(ensure_repo_root_on_path(), ROOT)

    def test_business_boundaries_registered(self):
        self.assertEqual(module_names(), tuple(MODULES))
        self.assertEqual(set(MODULES), {"boq", "ipc", "vo", "schedule", "excel"})
        for name in ("boq", "ipc", "vo", "schedule"):
            self.assertEqual(MODULES[name].migration_state, "compatibility-facade")
        self.assertEqual(MODULES["excel"].migration_state, "active-facade")
        self.assertIn("infrastructure", MODULES["excel"].dependencies)

    def test_domain_facades_are_lazy(self):
        facades = (
            (boq.background, "boq_background_v624"),
            (boq.persistence, "boq_persist_v624"),
            (ipc.background, "ipc_background_v624"),
            (ipc.persistence, "ipc_persist_v624"),
            (vo.background, "vo_background_v624"),
            (vo.persistence, "vo_persist_v624"),
            (schedule.background, "schedule_background_v624"),
            (schedule.persistence, "schedule_persist_v624"),
        )
        for facade, legacy_name in facades:
            self.assertIn("__getattr__", facade.__dict__)
            self.assertEqual(facade.IMPLEMENTATION, legacy_name)
            self.assertNotIn(legacy_name, sys.modules)

    def test_excel_public_facades_are_callable_without_eager_legacy_import(self):
        self.assertTrue(callable(jobs.build_upload_purpose))
        self.assertTrue(callable(jobs.enqueue_job))
        self.assertTrue(callable(jobs.list_jobs))
        self.assertTrue(callable(worker.main))
        self.assertNotIn("excel_jobs_v624", sys.modules)
        self.assertNotIn("excel_worker_v624", sys.modules)

    def test_legacy_access_is_centralized(self):
        modules_root = SRC / "qlda" / "modules"
        for path in modules_root.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("from qlda.runtime import legacy_import", source, str(path))
            self.assertNotIn("import excel_worker_v624", source, str(path))
            self.assertNotIn("import excel_jobs_v624", source, str(path))

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

    def test_main_service_exposes_src_namespace(self):
        service = (ROOT / "vps" / "qlda.service").read_text(encoding="utf-8")
        self.assertIn("PYTHONPATH=/opt/qlda/app/src:/opt/qlda/app", service)


if __name__ == "__main__":
    unittest.main()
