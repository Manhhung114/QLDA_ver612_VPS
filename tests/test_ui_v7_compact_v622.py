from __future__ import annotations

import unittest
from pathlib import Path

from v622_auth_refresh_v4 import patch_auth_refresh_v4
from v622_boq_multisheet_patch import patch_boq_multisheet
from v622_contractor_access_patch import patch_contractor_access
from v622_contractor_sidebar_patch import patch_contractor_sidebar_ui
from v622_contractor_workspace_patch import patch_contractor_workspace
from v622_ipc_claim_patch import patch_ipc_claims
from v622_legal_qlda_patch import patch_legal_qlda
from v622_local_vps_patch import patch_local_vps
from v622_original_import_patch import patch_original_import_storage
from v622_report_cost_patch import patch_report_cost
from v622_schedule_management_patch import patch_schedule_management
from v622_single_session_patch import patch_single_session
from v622_ui_v7_compact_patch import PATCH_MARKER, patch_ui_v7_compact
from v622_vo_claim_patch import patch_vo_claims


class UIV7CompactV622Test(unittest.TestCase):
    def _patched_source(self) -> str:
        """Mirror streamlit_app.py production source-patch order exactly."""
        source = Path("dist/streamlit_app.py").read_text(encoding="utf-8")
        source = patch_boq_multisheet(source)
        source = patch_ipc_claims(source)
        source = patch_vo_claims(source)
        source = patch_report_cost(source)
        source = patch_auth_refresh_v4(source)
        source = patch_local_vps(source)
        source = patch_contractor_workspace(source)
        source = patch_contractor_access(source)
        source = patch_single_session(source)
        source = patch_contractor_sidebar_ui(source)
        source = patch_schedule_management(source)
        source = patch_legal_qlda(source)
        source = patch_original_import_storage(source)
        source = patch_ui_v7_compact(source)
        return source

    def test_compact_layer_compiles_on_exact_production_source(self):
        source = self._patched_source()
        compile(source, "streamlit_app_v7_compact_test.py", "exec")
        self.assertIn(PATCH_MARKER, source)
        self.assertIn("install_theme_v7", source)
        self.assertIn("install_caption_policy_v7(st)", source)
        self.assertIn("render_admin_caption_toggle_v7(st, bool(_is_admin()))", source)
        self.assertIn("render_overview_v7", source)
        self.assertIn("render_work_tasks_v1", source)

    def test_missing_legacy_form_anchor_never_breaks_v7(self):
        source = self._patched_source()
        self.assertIn(PATCH_MARKER, source)
        compile(source, "streamlit_app_v7_optional_forms.py", "exec")

    def test_navigation_is_six_business_groups(self):
        source = self._patched_source()
        for label in (
            "🏠 Tổng quan",
            "📋 Công việc",
            "🏗️ Thi công",
            "📁 Hồ sơ",
            "💰 Tài chính",
            "📚 Công cụ",
        ):
            self.assertIn(label, source)
        self.assertNotIn("_main_actions[_main_choice]()", source)
        self.assertNotIn('("📊 Báo cáo", lambda: render_reports(pid))', source)
        self.assertIn("detailed_renderer=render_reports", source)
        self.assertIn("master_project_id=_master_pid", source)
        self.assertIn("users=_approval_users()", source)
        self.assertIn("gateway=_drive_gateway()", source)

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

    def test_secondary_legal_search_is_collapsed_when_anchor_exists(self):
        source = self._patched_source()
        self.assertNotIn(
            'with st.expander("🔎 Google / Tìm kiếm online toàn web", expanded=True):',
            source,
        )

    def test_caption_policy_is_global_default_off_and_admin_only(self):
        runtime = Path("ui_v7_compact_v622.py").read_text(encoding="utf-8")
        self.assertIn('_CAPTION_STATE_KEY = "qlda_v7_show_captions"', runtime)
        self.assertIn('_CAPTION_ADMIN_KEY = "qlda_v7_caption_admin_authorized"', runtime)
        self.assertIn("def install_caption_policy_v7", runtime)
        self.assertIn("def render_admin_caption_toggle_v7", runtime)
        self.assertIn('"Hiện chú thích / hướng dẫn"', runtime)
        self.assertIn("if allowed and enabled:", runtime)
        self.assertIn("st.session_state[_CAPTION_STATE_KEY] = False", runtime)

    def test_credit_is_black_in_isolated_runtime_layer(self):
        runtime = Path("ui_v7_compact_v622.py").read_text(encoding="utf-8")
        self.assertIn("by: Hoàng Mạnh Hùng &amp; AI", runtime)
        self.assertIn("qlda-v7-credit", runtime)
        self.assertIn("color:#000000;", runtime)
        self.assertIn("qlda-v7-hero", runtime)


if __name__ == "__main__":
    unittest.main()
