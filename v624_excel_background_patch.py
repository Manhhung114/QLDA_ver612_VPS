from __future__ import annotations

PATCH_MARKER = "V6.24.5 EXCEL BACKGROUND SOURCE V4"


def patch_excel_background_v624(source: str) -> str:
    """Add background BOQ + IPC + VO + Schedule paths without removing legacy imports."""
    if PATCH_MARKER in source:
        return source

    future = "from __future__ import annotations\n"
    if future not in source:
        raise RuntimeError(f"{PATCH_MARKER}: future import anchor missing")
    source = source.replace(
        future,
        future
        + "from excel_background_v624 import render_boq_background_panel as _v624_render_boq_background_panel\n"
        + "from excel_background_v624 import render_ipc_background_panel as _v624_render_ipc_background_panel\n"
        + "from excel_background_v624 import render_vo_background_panel as _v624_render_vo_background_panel\n"
        + "from excel_background_v624 import render_schedule_background_panel as _v624_render_schedule_background_panel\n",
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

    ipc_anchor = (
        "        _v622_render_ipc_claim_ui(db, pid, can_update=bool(_can_update()), "
        "session_token=_gateway_session_token())\n"
    )
    ipc_count = source.count(ipc_anchor)
    if ipc_count > 1:
        raise RuntimeError(f"{PATCH_MARKER}: IPC renderer anchor count={ipc_count}")
    if ipc_count == 1:
        ipc_panel = '''        _v624_render_ipc_background_panel(
            st,
            db,
            pid,
            gateway=_drive_gateway(),
            session_token=_gateway_session_token(),
            can_update=bool(_can_update()),
        )

'''
        source = source.replace(ipc_anchor, ipc_panel + ipc_anchor, 1)

    vo_anchor = (
        "        _v622_render_vo_ui(db, pid, can_update=bool(_can_update()), "
        "session_token=_gateway_session_token())\n"
    )
    vo_count = source.count(vo_anchor)
    if vo_count > 1:
        raise RuntimeError(f"{PATCH_MARKER}: VO renderer anchor count={vo_count}")
    if vo_count == 1:
        vo_panel = '''        _v624_render_vo_background_panel(
            st,
            db,
            pid,
            gateway=_drive_gateway(),
            session_token=_gateway_session_token(),
            can_update=bool(_can_update()),
        )

'''
        source = source.replace(vo_anchor, vo_panel + vo_anchor, 1)

    schedule_anchor = '''    rows = db.tasks(pid)
    n = len(rows)
'''
    schedule_count = source.count(schedule_anchor)
    if schedule_count > 1:
        raise RuntimeError(f"{PATCH_MARKER}: Schedule anchor count={schedule_count}")
    if schedule_count == 1:
        schedule_panel = '''    _v624_render_schedule_background_panel(
        st,
        db,
        pid,
        gateway=_drive_gateway(),
        session_token=_gateway_session_token(),
        can_update=bool(_can_update()),
        status_date=status_date,
    )

'''
        source = source.replace(schedule_anchor, schedule_panel + schedule_anchor, 1)
    source += f"\n# {PATCH_MARKER}\n"
    compile(source, "streamlit_app_v624_excel_background.py", "exec")
    return source
