from __future__ import annotations


PATCH_MARKER = "V6.22 CONTRACTOR SIDEBAR FINAL UI V1"


def patch_contractor_sidebar_ui(source: str) -> str:
    """Remove the legacy Contractor main-tab after access rules are patched.

    Contractor Add/Update/Delete is rendered directly below the active contractor
    selector by contractor_sidebar_admin_v622. The temporary main-tab exists only
    so v622_contractor_access_patch can keep its historical replacement anchor.
    """
    if PATCH_MARKER in source:
        return source

    candidates = (
        '    *([("🏢 Nhà thầu", lambda: _v622_render_contractor_management(db, _master_pid, can_admin=bool(_is_admin())))] if _v622_can_view_all_contractors else []),\n',
        '    ("🏢 Nhà thầu", lambda: _v622_render_contractor_management(db, _master_pid, can_admin=bool(_is_admin()))),\n',
    )
    removed = 0
    for line in candidates:
        count = source.count(line)
        if count:
            source = source.replace(line, "", count)
            removed += count

    if removed != 1:
        raise RuntimeError(
            f"{PATCH_MARKER}: expected exactly one legacy Contractor main-tab, removed {removed}"
        )

    source += f"\n# {PATCH_MARKER}\n"
    if '("🏢 Nhà thầu", lambda: _v622_render_contractor_management' in source:
        raise RuntimeError(f"{PATCH_MARKER}: Contractor main-tab still present")
    compile(source, "streamlit_app_v622_contractor_sidebar.py", "exec")
    return source
