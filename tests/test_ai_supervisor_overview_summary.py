from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OVERVIEW = ROOT / "src" / "qlda" / "presentation" / "streamlit" / "autonomy_overview.py"
NAVIGATION = ROOT / "src" / "qlda" / "presentation" / "streamlit" / "ai_supervisor_navigation.py"


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


def test_dedicated_navigation_keeps_full_supervisor_renderer() -> None:
    source = NAVIGATION.read_text(encoding="utf-8")
    assert "final_supervisor_renderer = overview.render_autonomy_control_center" in source
