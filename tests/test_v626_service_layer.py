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
from qlda.services import (
    BOQService,
    ExcelImportService,
    FileService,
    IPCService,
    JobService,
    ScheduleService,
    VOService,
)


class _FakeDomainService:
    def __init__(self, name: str):
        self.name = name
        self.calls = []

    def import_file(self, project_id, path, filename, **kwargs):
        self.calls.append((project_id, str(path), filename, kwargs))
        return {
            "job_type": self.name,
            "workspace_project_id": int(project_id),
            "filename": filename,
        }


class ServiceLayerV626Tests(unittest.TestCase):
    def test_version_and_service_layer_marker(self):
        version = tuple(int(x) for x in qlda.__version__.split(".")[:2])
        self.assertGreaterEqual(version, (6, 26))
        self.assertEqual(qlda.ARCHITECTURE, "modular-monolith")
        self.assertEqual(qlda.SERVICE_LAYER, "application-services")

    def test_public_services_are_available(self):
        for service in (
            BOQService,
            IPCService,
            VOService,
            ScheduleService,
            ExcelImportService,
            FileService,
            JobService,
        ):
            self.assertTrue(service)
        self.assertTrue(callable(make_database))
        self.assertTrue(callable(worker.main))

    def test_importing_services_does_not_eager_load_legacy_implementations(self):
        legacy = {
            "boq_background_v624",
            "boq_persist_v624",
            "ipc_background_v624",
            "ipc_persist_v624",
            "vo_background_v624",
            "vo_persist_v624",
            "schedule_background_v624",
            "schedule_persist_v624",
            "excel_jobs_v624",
            "excel_worker_v624",
            "local_vps_backend_v622",
        }
        self.assertFalse(legacy.intersection(sys.modules))

    def test_excel_dispatch_uses_domain_services(self):
        fake_boq = _FakeDomainService("BOQ")
        fake_ipc = _FakeDomainService("IPC")
        fake_vo = _FakeDomainService("VO")
        fake_schedule = _FakeDomainService("SCHEDULE_EXCEL")
        service = ExcelImportService(
            boq=fake_boq,
            ipc=fake_ipc,
            vo=fake_vo,
            schedule=fake_schedule,
        )
        row = {"name": "input.xlsx", "size": 123}
        cases = (
            ("BOQ_IMPORT", fake_boq),
            ("IPC", fake_ipc),
            ("VO", fake_vo),
            ("SCHEDULE_EXCEL", fake_schedule),
        )
        for job_type, fake in cases:
            output = service.process_job(
                {
                    "job_type": job_type,
                    "workspace_project_id": 9,
                    "options": {"status_date": "2026-09-14"},
                },
                ROOT / "dummy.xlsx",
                row,
            )
            self.assertEqual(output["workspace_project_id"], 9)
            self.assertEqual(fake.calls[-1][0], 9)

    def test_production_worker_depends_on_services_not_versioned_domain_files(self):
        source = (SRC / "qlda" / "modules" / "excel" / "worker.py").read_text(encoding="utf-8")
        self.assertIn("ExcelImportService", source)
        self.assertIn("FileService", source)
        self.assertIn("JobService", source)
        for forbidden in (
            "excel_worker_v624",
            "boq_background_v624",
            "boq_persist_v624",
            "ipc_background_v624",
            "ipc_persist_v624",
            "vo_background_v624",
            "vo_persist_v624",
            "schedule_background_v624",
            "schedule_persist_v624",
            "local_vps_backend_v622",
        ):
            self.assertNotIn(forbidden, source)

    def test_service_layer_has_no_streamlit_dependency(self):
        service_root = SRC / "qlda" / "services"
        for path in service_root.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            self.assertNotIn("import streamlit", source, str(path))
            self.assertNotIn("from streamlit", source, str(path))


if __name__ == "__main__":
    unittest.main()
