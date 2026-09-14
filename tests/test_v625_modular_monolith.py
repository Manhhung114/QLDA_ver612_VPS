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
from qlda.modules.excel import jobs
from qlda.modules.excel import worker
from qlda.runtime import REPO_ROOT, ensure_repo_root_on_path


class ModularMonolithV625Tests(unittest.TestCase):
    def test_package_version_and_repo_root(self):
        self.assertEqual(qlda.__version__, "6.25")
        self.assertEqual(qlda.ARCHITECTURE, "modular-monolith")
        self.assertEqual(REPO_ROOT, ROOT)
        self.assertEqual(ensure_repo_root_on_path(), ROOT)

    def test_business_boundaries_registered(self):
        self.assertEqual(module_names(), tuple(MODULES))
        self.assertEqual(set(MODULES), {"boq", "ipc", "vo", "schedule", "excel"})
        self.assertEqual(MODULES["excel"].migration_state, "active-facade")
        self.assertIn("infrastructure", MODULES["excel"].dependencies)

    def test_excel_public_facades_are_lazy_and_callable(self):
        self.assertTrue(callable(jobs.build_upload_purpose))
        self.assertTrue(callable(jobs.enqueue_job))
        self.assertTrue(callable(jobs.list_jobs))
        self.assertTrue(callable(worker.main))

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
