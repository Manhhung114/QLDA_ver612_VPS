from __future__ import annotations

from importlib import import_module
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCUMENT_UI = ROOT / "src" / "qlda" / "presentation" / "streamlit" / "document_management_vps_ui.py"
RUNTIME_MEETING = ROOT / "src" / "qlda" / "runtime_core" / "meeting_minutes_simple.py"


def test_document_management_uses_presentation_meeting_minutes_owner() -> None:
    source = DOCUMENT_UI.read_text(encoding="utf-8")
    assert "from qlda.presentation.streamlit import meeting_minutes_simple as meeting" in source
    assert "from qlda.runtime_core import meeting_minutes_simple" not in source


def test_retired_runtime_meeting_minutes_module_stays_absent() -> None:
    assert not RUNTIME_MEETING.exists()


def test_presentation_meeting_minutes_module_is_importable() -> None:
    module = import_module("qlda.presentation.streamlit.meeting_minutes_simple")
    assert callable(module.install_meeting_minutes_simple_ui)
    assert callable(module.render_meeting_minutes_simple)
