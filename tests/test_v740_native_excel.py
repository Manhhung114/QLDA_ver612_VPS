from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

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


class V740NativeExcelTests(unittest.TestCase):
    def test_version_and_adapter_inventory(self):
        import qlda

        self.assertEqual(qlda.__version__, "7.4")
        self.assertIn("excel", qlda.NATIVE_ADAPTERS)
        self.assertEqual(qlda.LEGACY_ADAPTERS, ())

    def test_native_excel_boundary_has_no_service_or_module_facade_dependency(self):
        path = SRC / "qlda" / "infrastructure" / "native_excel.py"
        modules = imports_of(path)
        self.assertIn("qlda.infrastructure.database", modules)
        self.assertIn("qlda.runtime", modules)
        for forbidden in ("qlda.services", "qlda.modules", "qlda.shared.legacy"):
            self.assertFalse(
                any(m == forbidden or m.startswith(forbidden + ".") for m in modules),
                forbidden,
            )

    def test_composition_wires_native_excel_without_eager_legacy_engines(self):
        from qlda.bootstrap import get_application, reset_application

        before = set(sys.modules)
        reset_application()
        app = get_application()
        loaded = set(sys.modules) - before
        self.assertEqual(app.excel._port.__class__.__name__, "NativeExcelImportAdapter")
        for engine in (
            "boq_background_v624",
            "boq_persist_v624",
            "ipc_background_v624",
            "ipc_persist_v624",
            "vo_background_v624",
            "vo_persist_v624",
            "schedule_background_v624",
            "schedule_persist_v624",
        ):
            self.assertNotIn(engine, loaded)

    def test_legacy_adapter_file_is_removed(self):
        self.assertFalse((SRC / "qlda" / "infrastructure" / "legacy_adapters.py").exists())

    def test_boq_dispatch_preserves_verification_and_snapshot_contract(self):
        from qlda.infrastructure.native_excel import NativeExcelImportAdapter

        calls: list[tuple] = []

        def parse(path, filename, **kwargs):
            calls.append(("parse", str(path), filename))
            return {"detail_line_count": 3, "batch_id": "B-1"}

        def save(db, project_id, result, **kwargs):
            calls.append(("save", db, project_id, kwargs.get("replace_existing_excel")))
            return {
                "inserted": 3,
                "expected_rows": 3,
                "scanned_rows": 3,
                "prepared_rows": 3,
                "written_rows": 3,
                "failed_rows": 0,
                "verification_status": "ĐỦ",
                "verified_postgresql": True,
                "batch_id": "B-1",
            }

        def snapshot(db, project_id, result):
            calls.append(("snapshot", db, project_id, result["batch_id"]))

        modules = {
            "boq_background_v624": SimpleNamespace(parse_boq_path=parse),
            "boq_persist_v624": SimpleNamespace(save_boq_result_batched=save),
            "boq_persistence_v622": SimpleNamespace(save_saved_boq_workbook=snapshot),
        }
        adapter = NativeExcelImportAdapter(
            db_factory=lambda: "DB",
            compat_loader=lambda name: modules[name],
        )
        result = adapter.process_job(
            {
                "job_type": "BOQ_IMPORT",
                "project_id": 7,
                "workspace_project_id": 9,
                "options": {"replace_existing_excel": False},
            },
            "/tmp/input.xlsx",
            {"name": "BOQ.xlsx", "size": 1234},
        )
        self.assertEqual(result["job_type"], "BOQ")
        self.assertEqual(result["workspace_project_id"], 9)
        self.assertEqual(result["written_rows"], 3)
        self.assertTrue(result["verified_postgresql"])
        self.assertEqual(result["verification_status"], "ĐỦ")
        self.assertIn(("save", "DB", 9, False), calls)
        self.assertIn(("snapshot", "DB", 9, "B-1"), calls)

    def test_schedule_dispatch_preserves_status_date_and_persistence_boundary(self):
        from qlda.infrastructure.native_excel import NativeExcelImportAdapter

        seen = {}

        def parse(path, filename, **kwargs):
            seen["status_date"] = kwargs.get("status_date")
            return {
                "sheet_name": "Schedule",
                "status_date": "2026-09-14",
                "source_sha256": "abc",
            }

        def save(db, project_id, result, **kwargs):
            return {
                "expected_rows": 4,
                "scanned_rows": 4,
                "prepared_rows": 4,
                "inserted_rows": 4,
                "written_rows": 4,
                "failed_rows": 0,
                "verification_status": "ĐỦ",
                "verified_postgresql": True,
                "source_row_count": 4,
                "skipped_rows": 0,
                "batch_id": "S-1",
            }

        modules = {
            "schedule_background_v624": SimpleNamespace(parse_schedule_excel_path=parse),
            "schedule_persist_v624": SimpleNamespace(save_schedule_result_batched=save),
        }
        adapter = NativeExcelImportAdapter(
            db_factory=lambda: "DB",
            compat_loader=lambda name: modules[name],
        )
        result = adapter.process_job(
            {
                "job_type": "SCHEDULE_EXCEL",
                "workspace_project_id": 11,
                "options": {"status_date": "2026-09-14"},
            },
            "/tmp/schedule.xlsx",
            {"name": "schedule.xlsx", "size": 88},
        )
        self.assertEqual(seen["status_date"], "2026-09-14")
        self.assertEqual(result["written_rows"], 4)
        self.assertTrue(result["verified_postgresql"])

    def test_worker_keeps_application_port_boundary(self):
        source = (SRC / "qlda" / "modules" / "excel" / "worker.py").read_text(encoding="utf-8")
        self.assertIn("get_application", source)
        self.assertIn("services.excel.process_job", source)
        self.assertNotIn("qlda.services", source)
        self.assertNotIn("legacy_adapters", source)


if __name__ == "__main__":
    unittest.main()
