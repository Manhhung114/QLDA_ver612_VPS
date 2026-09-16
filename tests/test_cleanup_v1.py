from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class CleanupV1Tests(unittest.TestCase):
    def test_boq_snapshot_has_one_implementation(self):
        from qlda.import_engines import boq_snapshot
        from qlda.runtime_core import boq_persistence

        self.assertIs(boq_persistence.save_saved_boq_workbook, boq_snapshot.save_saved_boq_workbook)
        self.assertIs(boq_persistence.load_saved_boq_workbook, boq_snapshot.load_saved_boq_workbook)
        self.assertIs(boq_persistence.delete_saved_boq_workbook, boq_snapshot.delete_saved_boq_workbook)
        self.assertIs(boq_persistence.format_table_number, boq_snapshot.format_table_number)

        facade = (ROOT / "src/qlda/runtime_core/boq_persistence.py").read_text(encoding="utf-8")
        self.assertIn("from qlda.import_engines.boq_snapshot import", facade)
        self.assertNotIn("CREATE TABLE IF NOT EXISTS", facade)

    def test_requested_consistency_policies_are_composed(self):
        bootstrap = (ROOT / "src/qlda/runtime_core/bootstrap.py").read_text(encoding="utf-8")
        self.assertIn("install_vo_value_consistency", bootstrap)
        self.assertIn("install_project_cost_budget_ui_simplify", bootstrap)
        self.assertLess(
            bootstrap.index("install_project_cost_signed_adjustments()"),
            bootstrap.index("install_vo_value_consistency()"),
        )

    def test_document_uniform_interaction_is_not_a_second_bootstrap_chain(self):
        bridge = (ROOT / "src/qlda/runtime_core/document_selection_autopen.py").read_text(encoding="utf-8")
        bootstrap = (ROOT / "src/qlda/runtime_core/bootstrap.py").read_text(encoding="utf-8")
        self.assertIn("install_document_management_uniform_interaction", bridge)
        # One composition entrypoint is enough: selection-autopen owns the uniform
        # interaction install so bootstrap does not need a second direct call.
        self.assertNotIn(
            "from qlda.runtime_core.document_management_uniform_interaction import",
            bootstrap,
        )


if __name__ == "__main__":
    unittest.main()
