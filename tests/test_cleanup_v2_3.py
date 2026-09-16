from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "src/qlda/runtime_core"


class CleanupV23Tests(unittest.TestCase):
    def test_one_finance_consistency_implementation(self):
        source = (RUNTIME / "finance_consistency.py").read_text(encoding="utf-8")
        self.assertIn("CLEANUP V2.3 CONSOLIDATED FINANCE CONSISTENCY", source)
        self.assertIn("def boq_budget_total", source)
        self.assertIn("def effective_proposed_value", source)
        self.assertIn("def install_finance_consistency_core", source)
        self.assertIn("def install_finance_consistency_ui", source)

    def test_old_project_cost_policy_files_are_thin_facades(self):
        names = (
            "boq_after_tax_budget.py",
            "project_cost_budget_ui_simplify.py",
            "project_cost_signed_adjustments.py",
            "vo_value_consistency.py",
        )
        for name in names:
            path = RUNTIME / name
            source = path.read_text(encoding="utf-8")
            self.assertLess(path.stat().st_size, 1600, name)
            self.assertIn("finance_consistency", source, name)
            self.assertNotIn("inspect.currentframe", source, name)
            self.assertNotIn("CREATE TABLE", source, name)

    def test_compatibility_facades_share_function_objects(self):
        from qlda.runtime_core import boq_after_tax_budget, finance_consistency, vo_value_consistency
        self.assertIs(boq_after_tax_budget.boq_budget_total, finance_consistency.boq_budget_total)
        self.assertIs(boq_after_tax_budget.saved_after_tax_total, finance_consistency.saved_after_tax_total)
        self.assertIs(vo_value_consistency.effective_proposed_value, finance_consistency.effective_proposed_value)
        self.assertIs(vo_value_consistency.normalize_project_vo_values, finance_consistency.normalize_project_vo_values)

    def test_bootstrap_has_single_finance_consistency_chain(self):
        bootstrap = (RUNTIME / "bootstrap.py").read_text(encoding="utf-8")
        self.assertEqual(bootstrap.count("install_finance_consistency_core()"), 1)
        self.assertEqual(bootstrap.count("install_finance_consistency_ui()"), 1)
        for old in (
            "install_project_cost_signed_adjustments",
            "install_vo_value_consistency",
            "install_boq_after_tax_budget_policy",
            "install_project_cost_budget_ui_simplify",
        ):
            self.assertNotIn(old, bootstrap)

    def test_signed_and_after_tax_semantics_are_kept(self):
        from qlda.runtime_core.finance_consistency import effective_proposed_value
        self.assertEqual(
            effective_proposed_value({"increase_amount": 100.0, "decrease_amount": -40.0, "proposed_amount": 999.0}),
            60.0,
        )
        self.assertEqual(effective_proposed_value({"proposed_amount": -15.0}), -15.0)


if __name__ == "__main__":
    unittest.main()
