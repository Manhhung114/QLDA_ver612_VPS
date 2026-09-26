from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMPOSITION = ROOT / "src" / "qlda" / "composition" / "runtime_features.py"
DENSITY = ROOT / "src" / "qlda" / "presentation" / "streamlit" / "compact_density.py"
AUTOMATION = ROOT / "src" / "qlda" / "presentation" / "streamlit" / "advanced_automation.py"


def test_compact_density_is_wired_through_composition() -> None:
    source = COMPOSITION.read_text(encoding="utf-8")
    assert 'RuntimeFeature("compact-density"' in source
    assert '"qlda.presentation.streamlit.compact_density"' in source


def test_compact_density_runs_after_normal_theme_renderer() -> None:
    source = DENSITY.read_text(encoding="utf-8")
    assert "original = ui.install_theme_v7" in source
    assert "original(st)" in source
    assert "st.markdown(_COMPACT_CSS" in source
    assert "[data-testid=\"stMetric\"]" in source
    assert ".qlda-v7-credit" in source


def test_persistent_credit_final_override_is_large_black_and_bold() -> None:
    source = DENSITY.read_text(encoding="utf-8")
    assert "font-size:14px!important" in source
    assert "font-weight:900!important" in source
    assert "color:#000000!important" in source
    assert "opacity:1!important" in source
    assert "-webkit-text-stroke:.15px" in source
    assert "text-shadow:0 1px 0 rgba(255,255,255,.95),0 1px 3px rgba(0,0,0,.30)!important" in source
    assert "font-size:8px!important" not in source
    assert "opacity:.42!important" not in source


def test_automation_hides_internal_version_names_from_user_ui() -> None:
    source = AUTOMATION.read_text(encoding="utf-8")
    assert "AI Automation V9.1" not in source
    assert 'st.markdown("#### V9.' not in source
    for label in ("Kiểm tra", "Draft VO", "Site Vision", "Task Routing"):
        assert label in source
