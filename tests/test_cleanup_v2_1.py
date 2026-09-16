from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path

from qlda.runtime_core import finance_common


ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "src/qlda/runtime_core"


class CleanupV21Tests(unittest.TestCase):
    def test_active_finance_modules_do_not_import_legacy_forecast_stack(self):
        for name in (
            "finance_management_ui.py",
            "project_cost_management.py",
            "cashflow_ai_context.py",
        ):
            source = (RUNTIME / name).read_text(encoding="utf-8")
            self.assertNotIn("cashflow_forecast_v1", source, name)
            self.assertNotIn("cashflow_forecast_v2", source, name)
            self.assertNotIn("cashflow_forecast_v3", source, name)

    def test_shared_finance_helpers_are_the_active_dependency(self):
        finance_ui = (RUNTIME / "finance_management_ui.py").read_text(encoding="utf-8")
        project_cost = (RUNTIME / "project_cost_management.py").read_text(encoding="utf-8")
        shared = (RUNTIME / "finance_common.py").read_text(encoding="utf-8")

        self.assertIn("from qlda.runtime_core.finance_common import", finance_ui)
        self.assertIn("from qlda.runtime_core.finance_common import", project_cost)
        self.assertIn("def resolve_scope", shared)
        self.assertIn("def planned_progress", shared)
        self.assertIn("def actual_progress", shared)
        self.assertIn("CLEANUP V2.1", finance_common.PATCH_MARKER)

    def test_date_and_progress_helpers_preserve_legacy_semantics(self):
        self.assertEqual(finance_common.parse_date("16/09/2026"), date(2026, 9, 16))
        self.assertEqual(finance_common.date_text("2026-09-16"), "16/09/2026")

        task = {
            "id": 7,
            "wbs": "1.2",
            "start_date": "2026-09-01",
            "end_date": "2026-09-10",
            "planned_progress": 12,
            "actual_progress": 40,
            "actual_override": 55,
        }
        self.assertEqual(finance_common.task_ref(task), "[TASK:7/1.2]")
        self.assertAlmostEqual(finance_common.planned_progress(task, date(2026, 9, 5)), 50.0)
        self.assertAlmostEqual(finance_common.actual_progress(task), 55.0)

    def test_progress_fallback_and_clamping(self):
        no_dates = {"planned_progress": 135, "actual_progress": -20}
        self.assertEqual(finance_common.planned_progress(no_dates, date(2026, 9, 16)), 100.0)
        self.assertEqual(finance_common.actual_progress(no_dates), 0.0)


if __name__ == "__main__":
    unittest.main()
