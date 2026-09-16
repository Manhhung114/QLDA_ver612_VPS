from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src/qlda"
RUNTIME = SRC / "runtime_core"


class CleanupV23Tests(unittest.TestCase):
    def test_one_finance_consistency_implementation(self):
        source = (RUNTIME / "finance_consistency.py").read_text(encoding="utf-8")
        self.assertIn("CLEANUP V2.3 CONSOLIDATED FINANCE CONSISTENCY", source)
        self.assertIn("def boq_budget_total", source)
        self.assertIn("def effective_proposed_value", source)
        self.assertIn("def install_finance_consistency_core", source)
        self.assertIn("def install_finance_consistency_ui", source)

    def test_retired_finance_policy_facades_are_physically_absent(self):
        for name in (
            "boq_after_tax_budget.py",
            "project_cost_budget_ui_simplify.py",
            "project_cost_signed_adjustments.py",
            "vo_value_consistency.py",
        ):
            self.assertFalse((RUNTIME / name).exists(), name)

    def test_no_packaged_module_imports_retired_finance_facades(self):
        retired = {
            "qlda.runtime_core.boq_after_tax_budget",
            "qlda.runtime_core.project_cost_budget_ui_simplify",
            "qlda.runtime_core.project_cost_signed_adjustments",
            "qlda.runtime_core.vo_value_consistency",
        }
        offenders: list[str] = []
        for path in SRC.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module in retired:
                    offenders.append(f"{path.relative_to(ROOT)}: from {node.module}")
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name in retired:
                            offenders.append(f"{path.relative_to(ROOT)}: import {alias.name}")
        self.assertEqual(offenders, [])

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
