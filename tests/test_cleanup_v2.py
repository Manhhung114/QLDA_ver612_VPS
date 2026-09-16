from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "src/qlda/runtime_core"


class CleanupV2Tests(unittest.TestCase):
    def test_obsolete_cashflow_forecast_panels_are_not_installed(self):
        bootstrap = (RUNTIME / "bootstrap.py").read_text(encoding="utf-8")
        self.assertNotIn("install_cashflow_forecast_v1", bootstrap)
        self.assertNotIn("install_cashflow_forecast_v2", bootstrap)
        self.assertNotIn("install_cashflow_forecast_v3", bootstrap)
        self.assertNotIn("install_finance_management_ui", bootstrap)

    def test_finance_navigation_has_one_owner(self):
        bootstrap = (RUNTIME / "bootstrap.py").read_text(encoding="utf-8")
        project_cost = (RUNTIME / "project_cost_management.py").read_text(encoding="utf-8")
        title_policy = (RUNTIME / "finance_title_policy.py").read_text(encoding="utf-8")

        self.assertIn("install_finance_title_policy", bootstrap)
        self.assertIn("install_project_cost_management", bootstrap)
        # Title policy may mention tabs in documentation, but must never assign or
        # call Streamlit tabs. Project Cost remains the single navigation owner.
        self.assertNotIn("st.tabs =", title_policy)
        self.assertNotIn("st.tabs(", title_policy)
        self.assertIn("render_cashflow_finance_sheet", project_cost)
        self.assertIn('"Dự trù dòng tiền"', project_cost)
        self.assertIn('"Kiểm soát chi phí"', project_cost)

    def test_legacy_forecast_files_are_removed_after_dependency_audit(self):
        # Cleanup V2.2 deletes the retired forecast implementation while leaving
        # any historical database tables untouched for data safety.
        for name in ("cashflow_forecast_v1.py", "cashflow_forecast_v2.py", "cashflow_forecast_v3.py"):
            self.assertFalse((RUNTIME / name).exists(), name)


if __name__ == "__main__":
    unittest.main()
