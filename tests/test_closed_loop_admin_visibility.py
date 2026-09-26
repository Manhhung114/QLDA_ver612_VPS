from pathlib import Path


def test_closed_loop_panel_is_admin_only() -> None:
    source = Path("src/qlda/presentation/streamlit/ai_supervisor_navigation.py").read_text(encoding="utf-8")

    assert "render_ai_supervisor_page(" in source
    assert "overview._app_identity(ui)" in source
    assert "if is_admin:" in source
    assert "render_closed_loop_panel(" in source
    assert "return None" in source
    assert "ADMIN CLOSED LOOP" in source
