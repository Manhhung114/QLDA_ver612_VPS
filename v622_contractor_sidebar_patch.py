from __future__ import annotations


PATCH_MARKER = "V6.22 CONTRACTOR SIDEBAR FINAL UI V2 ADMIN CONTROLS"
ADMIN_UI_MARKER = "V6.22 CONTRACTOR SIDEBAR ADMIN INJECT V1"


def _replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"{PATCH_MARKER}: expected one {label}, found {count}")
    return source.replace(old, new, 1)


def patch_contractor_sidebar_ui(source: str) -> str:
    """Finalize the contractor sidebar after access rules are patched.

    v622_contractor_access_patch replaces the basic contractor selector with the
    authorization-aware selector. That selector intentionally restricts rows for
    CONTRACTOR accounts, but it does not render the Admin Add/Update/Delete block.
    Inject the Admin tools immediately after the authorized selector, then remove
    the legacy Contractor main-tab.
    """
    if PATCH_MARKER in source:
        return source

    selector = '''if _master_pid:\n    pid, _contractor_ctx = _v622_render_contractor_selector(\n        db, _master_pid, can_admin=bool(_is_admin()), current_user=_streamlit_user_email(),\n        approval_role=_v622_approval_role,\n    )\n'''
    selector_with_admin = selector + '''# V6.22 CONTRACTOR SIDEBAR ADMIN INJECT V1\n# The authorization-aware selector filters which contractor can be seen. Admin\n# management controls are rendered separately because that selector deliberately\n# does not own destructive management actions.\nif (\n    _master_pid\n    and _contractor_ctx\n    and bool(_is_admin())\n    and str(_v622_approval_role or "").strip().upper() != "CONTRACTOR"\n):\n    from contractor_sidebar_admin_v622 import _render_admin_tools as _v622_render_contractor_admin_tools\n    _v622_render_contractor_admin_tools(st, db, int(_master_pid), dict(_contractor_ctx))\n'''
    source = _replace_once(source, selector, selector_with_admin, "authorized contractor selector")

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
