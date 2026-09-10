from __future__ import annotations


PATCH_MARKER = "V6.22 CONTRACTOR SIDEBAR FINAL UI V3 ADMIN CONTROLS"
ADMIN_UI_MARKER = "V6.22 CONTRACTOR SIDEBAR ADMIN INJECT V2"


def patch_contractor_sidebar_ui(source: str) -> str:
    """Finalize the contractor sidebar and keep Admin controls visible.

    The finalizer is intentionally compatible with both supported call orders:
    directly after the basic contractor-workspace patch, and after the stricter
    contractor-access patch used in production. In either case the destructive
    Add/Update/Delete tools are rendered only when the authenticated app role is
    Admin (`_is_admin()`). Contractor accounts are forced to update/read roles by
    the access layer, so they can never satisfy this condition.
    """
    if PATCH_MARKER in source:
        return source

    authorized_selector = '''if _master_pid:\n    pid, _contractor_ctx = _v622_render_contractor_selector(\n        db, _master_pid, can_admin=bool(_is_admin()), current_user=_streamlit_user_email(),\n        approval_role=_v622_approval_role,\n    )\n'''
    basic_selector = '''if _master_pid:\n    pid, _contractor_ctx = _v622_render_contractor_selector(\n        db, _master_pid, can_admin=bool(_is_admin()), current_user=_streamlit_user_email()\n    )\n'''

    matches = []
    for selector in (authorized_selector, basic_selector):
        count = source.count(selector)
        if count:
            matches.append((selector, count))
    if len(matches) != 1 or matches[0][1] != 1:
        found = sum(count for _, count in matches)
        raise RuntimeError(
            f"{PATCH_MARKER}: expected exactly one contractor selector, found {found}"
        )

    selector = matches[0][0]
    admin_block = '''# V6.22 CONTRACTOR SIDEBAR ADMIN INJECT V2\n# Access-aware selection controls visibility of contractor data; management is a\n# separate Admin-only capability rendered immediately below that selector.\nif _master_pid and _contractor_ctx and bool(_is_admin()):\n    from contractor_sidebar_admin_v622 import _render_admin_tools as _v622_render_contractor_admin_tools\n    _v622_render_contractor_admin_tools(st, db, int(_master_pid), dict(_contractor_ctx))\n'''
    source = source.replace(selector, selector + admin_block, 1)

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
    if ADMIN_UI_MARKER not in source or "_v622_render_contractor_admin_tools" not in source:
        raise RuntimeError(f"{PATCH_MARKER}: Admin contractor controls were not injected")
    compile(source, "streamlit_app_v622_contractor_sidebar.py", "exec")
    return source
