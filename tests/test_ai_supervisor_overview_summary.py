from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OVERVIEW = ROOT / "src" / "qlda" / "presentation" / "streamlit" / "autonomy_overview.py"
NAVIGATION = ROOT / "src" / "qlda" / "presentation" / "streamlit" / "ai_supervisor_navigation.py"
SUPERVISOR_PAGE = ROOT / "src" / "qlda" / "presentation" / "streamlit" / "ai_supervisor_page.py"


def test_overview_uses_report_only_supervisor_metrics() -> None:
    source = OVERVIEW.read_text(encoding="utf-8")
    installer = source.split("def install_autonomy_overview_patch()", 1)[1]
    assert "render_autonomy_overview_metrics(st, db, int(pid), ui_module=ui)" in installer
    assert "render_autonomy_control_center(st, db, int(pid), ui_module=ui)" not in installer


def test_overview_metrics_have_no_supervisor_controls() -> None:
    source = OVERVIEW.read_text(encoding="utf-8")
    summary = source.split("def render_autonomy_overview_metrics", 1)[1].split(
        "def render_autonomy_control_center", 1
    )[0]
    assert "Project Health" in summary
    assert "Data Integrity" in summary
    assert "Cảnh báo" in summary
    assert "Trạng thái" in summary
    assert "st.dataframe" not in summary
    assert "st.expander" not in summary
    assert "st.selectbox" not in summary
    assert "st.button" not in summary


def test_dedicated_navigation_uses_compact_supervisor_page() -> None:
    source = NAVIGATION.read_text(encoding="utf-8")
    assert "from qlda.presentation.streamlit.ai_supervisor_page import render_ai_supervisor_page" in source
    assert "return render_ai_supervisor_page(" in source
    assert "final_supervisor_renderer = overview.render_autonomy_control_center" not in source


def test_compact_supervisor_page_uses_four_primary_tabs() -> None:
    source = SUPERVISOR_PAGE.read_text(encoding="utf-8")
    for label in ("Tổng quan AI", "Cảnh báo", "Điều phối", "Tự động hóa"):
        assert label in source
    assert "qlda-ai-tenant-badge" in source
    assert "Xem chi tiết cảnh báo" in source
