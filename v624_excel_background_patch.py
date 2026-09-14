from __future__ import annotations

PATCH_MARKER = "V6.24 EXCEL BACKGROUND SOURCE V1"


def patch_excel_background_v624(source: str) -> str:
    """Add direct-to-disk/background BOQ path without removing legacy import."""
    if PATCH_MARKER in source:
        return source

    future = "from __future__ import annotations\n"
    if future not in source:
        raise RuntimeError(f"{PATCH_MARKER}: future import anchor missing")
    source = source.replace(
        future,
        future + "from excel_background_v624 import render_boq_background_panel as _v624_render_boq_background_panel\n",
        1,
    )

    anchor = '''            _boq_result = None
            _boq_source = ""
'''
    if source.count(anchor) != 1:
        raise RuntimeError(f"{PATCH_MARKER}: BOQ result anchor count={source.count(anchor)}")
    panel = '''            _v624_render_boq_background_panel(
                st,
                db,
                pid,
                gateway=_drive_gateway(),
                session_token=_gateway_session_token(),
                can_update=bool(_can_update()),
                replace_existing_excel=bool(_boq_replace),
            )

'''
    source = source.replace(anchor, panel + anchor, 1)
    source += f"\n# {PATCH_MARKER}\n"
    compile(source, "streamlit_app_v624_excel_background.py", "exec")
    return source
