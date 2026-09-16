from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "src/qlda/runtime_core"


class CleanupV22Tests(unittest.TestCase):
    def test_no_runtime_module_imports_retired_cashflow_forecast_stack(self):
        offenders: list[str] = []
        retired = {
            "qlda.runtime_core.cashflow_forecast_v1",
            "qlda.runtime_core.cashflow_forecast_v2",
            "qlda.runtime_core.cashflow_forecast_v3",
        }
        for path in RUNTIME.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module in retired:
                    offenders.append(f"{path.name}: from {node.module}")
                elif isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name in retired:
                            offenders.append(f"{path.name}: import {alias.name}")
        self.assertEqual(offenders, [])

    def test_finance_renderer_has_no_legacy_streamlit_patch_installer(self):
        source = (RUNTIME / "finance_management_ui.py").read_text(encoding="utf-8")
        self.assertNotIn("def install_finance_management_ui", source)
        self.assertNotIn("st.tabs =", source)
        self.assertNotIn("st.subheader =", source)
        self.assertNotIn("inspect.currentframe", source)
        self.assertIn("render_cashflow_finance_sheet", source)
        self.assertIn("load_unpaid_ipcs", source)

    def test_retired_files_are_physically_absent(self):
        for name in (
            "cashflow_forecast_v1.py",
            "cashflow_forecast_v2.py",
            "cashflow_forecast_v3.py",
        ):
            self.assertFalse((RUNTIME / name).exists(), name)


if __name__ == "__main__":
    unittest.main()
