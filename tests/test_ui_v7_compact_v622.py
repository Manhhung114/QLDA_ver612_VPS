from __future__ import annotations

import unittest
from pathlib import Path

from v622_contractor_access_patch import patch_contractor_access
from v622_contractor_sidebar_patch import patch_contractor_sidebar_ui
from v622_contractor_workspace_patch import patch_contractor_workspace
from v622_legal_qlda_patch import patch_legal_qlda
from v622_ui_v7_compact_patch import PATCH_MARKER, patch_ui_v7_compact


class UIV7CompactV622Test(unittest.TestCase):
    def _patched_source(self) -> str:
        source = Path("dist/streamlit_app.py").read_text(encoding="utf-8")
        source = patch_contractor_workspace(source)
        source = patch_contractor_access(source)
        source = patch_contractor_sidebar_ui(source)
        source = patch_legal_qlda(source)
        source = patch_ui_v7_compact(source)
        return source

    def test_compact_layer_compiles_on_multi_contractor_source(self):
        source = self._patched_source()
        compile(source, "streamlit_app_v7_compact_test.py", "exec")
        self.assertIn(PATCH_MARKER, source)
        self.assertIn("from ui_v7_compact_v622 import install_theme_v7", source)
        self.assertIn("install_theme_v7(st)", source)
        self.assertIn("render_overview_v7", source)

    def test_navigation_is_five_business_groups(self):
        source = self._patched_source()
        for label in (
            "🏠 Tổng quan",
            "🏗️ Thi công",
            "📁 Hồ sơ",
            "💰 Tài chính",
            "📚 Công cụ",
        ):
            self.assertIn(label, source)
        self.assertNotIn("_main_actions[_main_choice]()", source)
        self.assertNotIn('("📊 Báo cáo", lambda: render_reports(pid))', source)
        # Detailed report is preserved but only reached from Overview on demand.
        self.assertIn("detailed_renderer=render_reports", source)

    def test_business_renderers_are_preserved(self):
        source = self._patched_source()
        for fn in (
            "def render_schedule(",
            "def render_documents(",
            "def render_drawings(",
            "def render_cost_management(",
            "def render_material_management(",
            "def render_site_diary(",
            "def render_ai_assistant(",
        ):
            self.assertIn(fn, source)

    def test_secondary_legal_search_is_collapsed(self):
        source = self._patched_source()
        self.assertIn(
            'with st.expander("🔎 Tìm kiếm online nâng cao", expanded=False):',
            source,
        )
        self.assertNotIn(
            'with st.expander("🔎 Google / Tìm kiếm online toàn web", expanded=True):',
            source,
        )

    def test_credit_lives_in_isolated_runtime_layer(self):
        runtime = Path("ui_v7_compact_v622.py").read_text(encoding="utf-8")
        self.assertIn("by: Hoàng Mạnh Hùng &amp; AI", runtime)
        self.assertIn("qlda-v7-credit", runtime)
        self.assertIn("qlda-v7-hero", runtime)


if __name__ == "__main__":
    unittest.main()
