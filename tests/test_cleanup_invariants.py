from __future__ import annotations

import ast
import unittest
from datetime import date
from pathlib import Path

from qlda.runtime_core import finance_common
from qlda.runtime_core.finance_consistency import effective_proposed_value

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src/qlda"
RUNTIME = SRC / "runtime_core"
COMPOSITION = SRC / "composition/runtime_features.py"


class CleanupInvariantTests(unittest.TestCase):
    def test_boq_snapshot_has_one_implementation(self):
        from qlda.import_engines import boq_snapshot
        from qlda.runtime_core import boq_persistence

        self.assertIs(boq_persistence.save_saved_boq_workbook, boq_snapshot.save_saved_boq_workbook)
        self.assertIs(boq_persistence.load_saved_boq_workbook, boq_snapshot.load_saved_boq_workbook)
        self.assertIs(boq_persistence.delete_saved_boq_workbook, boq_snapshot.delete_saved_boq_workbook)
        facade = (RUNTIME / "boq_persistence.py").read_text(encoding="utf-8")
        self.assertIn("from qlda.import_engines.boq_snapshot import", facade)
        self.assertNotIn("CREATE TABLE IF NOT EXISTS", facade)

    def test_retired_finance_modules_stay_removed_and_unreferenced(self):
        retired_files = {
            "cashflow_forecast_v1.py",
            "cashflow_forecast_v2.py",
            "cashflow_forecast_v3.py",
            "boq_after_tax_budget.py",
            "project_cost_budget_ui_simplify.py",
            "project_cost_signed_adjustments.py",
            "vo_value_consistency.py",
        }
        retired_modules = {f"qlda.runtime_core.{name[:-3]}" for name in retired_files}

        for name in retired_files:
            self.assertFalse((RUNTIME / name).exists(), name)

        offenders: list[str] = []
        for path in SRC.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module in retired_modules:
                    offenders.append(f"{path.relative_to(ROOT)}: from {node.module}")
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name in retired_modules:
                            offenders.append(f"{path.relative_to(ROOT)}: import {alias.name}")
        self.assertEqual(offenders, [])

    def test_finance_consistency_has_one_active_chain(self):
        composition = COMPOSITION.read_text(encoding="utf-8")
        consistency = (RUNTIME / "finance_consistency.py").read_text(encoding="utf-8")

        self.assertEqual(composition.count('"install_finance_consistency_core"'), 1)
        self.assertEqual(composition.count('"install_finance_consistency_ui"'), 1)
        self.assertIn("def boq_budget_total", consistency)
        self.assertIn("def effective_proposed_value", consistency)
        for old in (
            "install_project_cost_signed_adjustments",
            "install_vo_value_consistency",
            "install_boq_after_tax_budget_policy",
            "install_project_cost_budget_ui_simplify",
            "install_cashflow_forecast_v1",
            "install_cashflow_forecast_v2",
            "install_cashflow_forecast_v3",
        ):
            self.assertNotIn(old, composition)

    def test_shared_finance_helpers_preserve_semantics(self):
        finance_ui = (RUNTIME / "finance_management_ui.py").read_text(encoding="utf-8")
        project_cost = (RUNTIME / "project_cost_management.py").read_text(encoding="utf-8")
        self.assertIn("from qlda.runtime_core.finance_common import", finance_ui)
        self.assertIn("from qlda.runtime_core.finance_common import", project_cost)

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
        self.assertEqual(finance_common.planned_progress({"planned_progress": 135}, date(2026, 9, 16)), 100.0)
        self.assertEqual(finance_common.actual_progress({"actual_progress": -20}), 0.0)

        self.assertEqual(
            effective_proposed_value({"increase_amount": 100.0, "decrease_amount": -40.0, "proposed_amount": 999.0}),
            60.0,
        )
        self.assertEqual(effective_proposed_value({"proposed_amount": -15.0}), -15.0)

    def test_document_uniform_interaction_is_not_a_second_bootstrap_chain(self):
        bridge = (RUNTIME / "document_selection_autopen.py").read_text(encoding="utf-8")
        bootstrap = (RUNTIME / "bootstrap.py").read_text(encoding="utf-8")
        composition = COMPOSITION.read_text(encoding="utf-8")
        self.assertIn("install_document_management_uniform_interaction", bridge)
        self.assertNotIn("qlda.runtime_core.document_management_uniform_interaction", composition)
        self.assertNotIn("from qlda.runtime_core.document_management_uniform_interaction import", bootstrap)

    def test_obsolete_contractor_source_patch_is_removed(self):
        self.assertFalse((RUNTIME / "contractor_access_patch.py").exists())


if __name__ == "__main__":
    unittest.main()
