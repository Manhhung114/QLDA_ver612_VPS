from __future__ import annotations


PATCH_MARKER = "V6.22 CONTRACTOR WORKSPACE UI V1"


def _replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"{PATCH_MARKER}: expected one {label}, found {count}")
    return source.replace(old, new, 1)


def patch_contractor_workspace(source: str) -> str:
    """Insert a contractor workspace selector while keeping AI at master-project scope."""
    if PATCH_MARKER in source:
        return source

    startup = '''_require_cloud_login_and_access()\nsidebar_project_tools()\npid, projects = project_selector()\n\nst.title('''
    startup_new = '''# V6.22 CONTRACTOR WORKSPACE UI V1 START\nfrom contractor_workspace_v622 import (\n    render_contractor_selector as _v622_render_contractor_selector,\n    render_contractor_management as _v622_render_contractor_management,\n)\n\n_require_cloud_login_and_access()\nsidebar_project_tools()\npid, projects = project_selector()\n_master_pid = int(pid) if pid else None\n_contractor_ctx = {}\nif _master_pid:\n    pid, _contractor_ctx = _v622_render_contractor_selector(\n        db, _master_pid, can_admin=bool(_is_admin()), current_user=_streamlit_user_email()\n    )\n\n# Operational tabs use the selected contractor workspace `pid`.\n# AI and Project settings deliberately keep `_master_pid` to cover all contractors.\n# V6.22 CONTRACTOR WORKSPACE UI V1 END\n\nst.title('''
    source = _replace_once(source, startup, startup_new, "startup selector anchor")

    project_note = '''p = db.project(pid)\n_ui_note(f"Dự án: **{p['code']} - {p['name']}**")\n'''
    project_note_new = '''p = db.project(_master_pid)\n_ui_note(f"Dự án: **{p['code']} - {p['name']}**")\nif _contractor_ctx:\n    _ui_note(\n        f"Nhà thầu đang làm việc: **{_contractor_ctx.get('contractor_code','')} - {_contractor_ctx.get('contractor_name','')}** "\n        f"• Hợp đồng: **{_contractor_ctx.get('contract_no','') or '—'}** "\n        "• 🤖 AI: **Toàn dự án / tất cả nhà thầu**"\n    )\n'''
    source = _replace_once(source, project_note, project_note_new, "project note anchor")

    ai_line = '''    ("🤖 AI", lambda: render_ai_assistant(pid)),\n'''
    ai_new = '''    ("🏢 Nhà thầu", lambda: _v622_render_contractor_management(db, _master_pid, can_admin=bool(_is_admin()))),\n    ("🤖 AI", lambda: render_ai_assistant(_master_pid)),\n'''
    source = _replace_once(source, ai_line, ai_new, "AI section")

    project_line = '''    ("🏗️ Dự án", lambda: render_project_info(pid)),\n'''
    project_new = '''    ("🏗️ Dự án", lambda: render_project_info(_master_pid)),\n'''
    source = _replace_once(source, project_line, project_new, "project settings section")

    if (
        PATCH_MARKER not in source
        or "_v622_render_contractor_selector" not in source
        or "_v622_render_contractor_management" not in source
        or "render_ai_assistant(_master_pid)" not in source
    ):
        raise RuntimeError(f"{PATCH_MARKER}: generated source validation failed")
    compile(source, "streamlit_app_v622_multi_contractor.py", "exec")
    return source
