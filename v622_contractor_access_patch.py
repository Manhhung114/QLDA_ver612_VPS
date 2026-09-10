from __future__ import annotations


PATCH_MARKER = "V6.22 CONTRACTOR ACCESS UI V1"


def _replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"{PATCH_MARKER}: expected one {label}, found {count}")
    return source.replace(old, new, 1)


def patch_contractor_access(source: str) -> str:
    """Add project viewer and strict contractor workspace authorization.

    This patch runs AFTER v622_contractor_workspace_patch. The Apps Script keeps
    its stable read/update/admin + approval-role schema; PROJECT_VIEWER is stored
    project-locally as read-only scope, while CONTRACTOR gets one explicit
    contractor assignment per master project.
    """
    if PATCH_MARKER in source:
        return source

    imports_old = '''from contractor_workspace_v622 import (\n    render_contractor_selector as _v622_render_contractor_selector,\n    render_contractor_management as _v622_render_contractor_management,\n)\n'''
    imports_new = '''from contractor_workspace_v622 import (\n    render_contractor_management as _v622_render_contractor_management,\n    list_contractors as _v622_list_contractors,\n)\nfrom contractor_access_control_v622 import (\n    PROJECT_VIEWER as _V622_PROJECT_VIEWER,\n    render_authorized_contractor_selector as _v622_render_contractor_selector,\n    get_user_project_access as _v622_get_user_project_access,\n    set_user_project_access as _v622_set_user_project_access,\n    delete_user_project_access as _v622_delete_user_project_access,\n    effective_user_classification as _v622_effective_user_classification,\n    user_contractor_scope_label as _v622_user_contractor_scope_label,\n    user_can_view_all_contractors as _v622_user_can_view_all_contractors,\n    set_ai_workspace_scope as _v622_set_ai_workspace_scope,\n)\n'''
    source = _replace_once(source, imports_old, imports_new, "contractor imports")

    selector_old = '''_master_pid = int(pid) if pid else None\n_contractor_ctx = {}\nif _master_pid:\n    pid, _contractor_ctx = _v622_render_contractor_selector(\n        db, _master_pid, can_admin=bool(_is_admin()), current_user=_streamlit_user_email()\n    )\n'''
    selector_new = '''_master_pid = int(pid) if pid else None\n_contractor_ctx = {}\n_v622_identity = _cloud_identity()\n_v622_approval_role = _user_approval_role(_v622_identity)\n_v622_can_view_all_contractors = _v622_user_can_view_all_contractors(_v622_approval_role)\nif _master_pid:\n    pid, _contractor_ctx = _v622_render_contractor_selector(\n        db, _master_pid, can_admin=bool(_is_admin()), current_user=_streamlit_user_email(),\n        approval_role=_v622_approval_role,\n    )\n'''
    source = _replace_once(source, selector_old, selector_new, "authorized selector")

    note_old = '''if _contractor_ctx:\n    _ui_note(\n        f"Nhà thầu đang làm việc: **{_contractor_ctx.get('contractor_code','')} - {_contractor_ctx.get('contractor_name','')}** "\n        f"• Hợp đồng: **{_contractor_ctx.get('contract_no','') or '—'}** "\n        "• 🤖 AI: **Toàn dự án / tất cả nhà thầu**"\n    )\n'''
    note_new = '''if _contractor_ctx:\n    _v622_scope_note = (\n        "• 🤖 AI: **Toàn dự án / tất cả nhà thầu**"\n        if _v622_can_view_all_contractors\n        else "• 🔒 Phạm vi: **chỉ nhà thầu này, kể cả AI**"\n    )\n    _ui_note(\n        f"Nhà thầu đang làm việc: **{_contractor_ctx.get('contractor_code','')} - {_contractor_ctx.get('contractor_name','')}** "\n        f"• Hợp đồng: **{_contractor_ctx.get('contract_no','') or '—'}** "\n        + _v622_scope_note\n    )\n'''
    source = _replace_once(source, note_old, note_new, "scope note")

    sections_old = '''    ("🏢 Nhà thầu", lambda: _v622_render_contractor_management(db, _master_pid, can_admin=bool(_is_admin()))),\n    ("🤖 AI", lambda: render_ai_assistant(_master_pid)),\n    ("⚙️ Cài đặt", lambda: render_settings()),\n'''
    sections_new = '''    *([("🏢 Nhà thầu", lambda: _v622_render_contractor_management(db, _master_pid, can_admin=bool(_is_admin())))] if _v622_can_view_all_contractors else []),\n    ("🤖 AI", lambda: render_ai_assistant(_master_pid if _v622_can_view_all_contractors else pid)),\n    ("⚙️ Cài đặt", lambda: render_settings(_master_pid)),\n'''
    source = _replace_once(source, sections_old, sections_new, "main access sections")

    ai_old = '''def render_ai_assistant(pid: int):\n    st.subheader("🤖 Trợ lý AI QLDA")\n'''
    ai_new = '''def render_ai_assistant(pid: int):\n    # CONTRACTOR accounts set a ContextVar guard so ProjectContextBuilder,\n    # attachment catalog and every AI helper remain inside the authorized workspace.\n    _v622_set_ai_workspace_scope(\n        int(pid) if _user_approval_role(_cloud_identity()) == "CONTRACTOR" else None\n    )\n    st.subheader("🤖 Trợ lý AI QLDA")\n'''
    source = _replace_once(source, ai_old, ai_new, "AI scope guard")

    settings_old = '''def render_settings():\n    st.subheader("⚙️ Cài đặt ứng dụng")\n'''
    settings_new = '''def render_settings(master_project_id=None):\n    st.subheader("⚙️ Cài đặt ứng dụng")\n'''
    source = _replace_once(source, settings_old, settings_new, "settings project scope")

    choices_old = '''                    approval_choices = ["", "CONTRACTOR", "SITE_MANAGEMENT", "CONSULTANT", "PROJECT_MANAGEMENT"]\n                    role_choices = ["read", "update", "admin"]\n'''
    choices_new = '''                    # V6.22 CONTRACTOR ACCESS UI V1\n                    approval_choices = ["", "CONTRACTOR", "SITE_MANAGEMENT", "CONSULTANT", "PROJECT_MANAGEMENT", _V622_PROJECT_VIEWER]\n                    role_choices = ["read", "update", "admin"]\n                    _v622_approval_labels = dict(APPROVAL_ROLE_LABELS)\n                    _v622_approval_labels[_V622_PROJECT_VIEWER] = "Chỉ xem toàn bộ nhà thầu"\n                    _v622_master_access_id = int(master_project_id or 0)\n                    _v622_project_contractors = (\n                        _v622_list_contractors(db, _v622_master_access_id, active_only=False)\n                        if _v622_master_access_id else []\n                    )\n                    _v622_contractor_by_id = {\n                        int(x.get("id") or 0): dict(x) for x in _v622_project_contractors\n                        if int(x.get("id") or 0) > 0\n                    }\n'''
    source = _replace_once(source, choices_old, choices_new, "user access choices")

    current_old = '''                    current_approval = _user_approval_role(selected_user)\n                    if current_approval not in approval_choices:\n                        current_approval = ""\n                    widget_suffix = (current_email or "new").replace("@", "_").replace(".", "_")\n'''
    current_new = '''                    current_approval = _user_approval_role(selected_user)\n                    _v622_current_access = (\n                        _v622_get_user_project_access(db, _v622_master_access_id, current_email)\n                        if current_email and _v622_master_access_id else {}\n                    )\n                    if str(_v622_current_access.get("access_mode") or "").upper() == _V622_PROJECT_VIEWER:\n                        current_approval = _V622_PROJECT_VIEWER\n                    if current_approval not in approval_choices:\n                        current_approval = ""\n                    _v622_current_contractor_id = int(_v622_current_access.get("contractor_id") or 0)\n                    widget_suffix = (current_email or "new").replace("@", "_").replace(".", "_")\n'''
    source = _replace_once(source, current_old, current_new, "current user scope")

    approval_widget_old = '''                        papproval = c2.selectbox(\n                            "Phân loại phê duyệt",\n                            approval_choices,\n                            index=approval_choices.index(current_approval),\n                            format_func=lambda x: APPROVAL_ROLE_LABELS.get(x, x),\n                        )\n                        ppass = c2.text_input(\n'''
    approval_widget_new = '''                        papproval = c2.selectbox(\n                            "Phân loại phê duyệt",\n                            approval_choices,\n                            index=approval_choices.index(current_approval),\n                            format_func=lambda x: _v622_approval_labels.get(x, x),\n                        )\n                        _v622_contractor_options = [None] + list(_v622_contractor_by_id)\n                        _v622_default_contractor_index = (\n                            _v622_contractor_options.index(_v622_current_contractor_id)\n                            if _v622_current_contractor_id in _v622_contractor_options else 0\n                        )\n                        pcontractor = c1.selectbox(\n                            "Nhà thầu được phép (bắt buộc khi Phân loại = Nhà thầu)",\n                            _v622_contractor_options,\n                            index=_v622_default_contractor_index,\n                            format_func=lambda cid: "— Chọn nhà thầu —" if cid is None else (\n                                f"{_v622_contractor_by_id[cid].get('contractor_code','')} - "\n                                f"{_v622_contractor_by_id[cid].get('contractor_name','')}"\n                            ),\n                        )\n                        st.caption(\n                            "Nhà thầu: hệ thống tự ép quyền Cập nhật và chỉ thấy workspace được gán. "\n                            "Chỉ xem toàn bộ nhà thầu: hệ thống tự ép quyền Chỉ đọc."\n                        )\n                        ppass = c2.text_input(\n'''
    source = _replace_once(source, approval_widget_old, approval_widget_new, "contractor assignment widget")

    save_old = '''                            target_email = current_email if selected_user else pemail\n                            expected_approval = papproval or ("PROJECT_MANAGEMENT" if prole == "admin" else "")\n                            result = gw.set_user(token, target_email, pname, prole, ppass, papproval)\n                            saved = dict(result.get("user") or {})\n'''
    save_new = '''                            target_email = current_email if selected_user else pemail\n                            if papproval == "CONTRACTOR" and not pcontractor:\n                                raise ValueError("Phân loại Nhà thầu bắt buộc phải chọn đúng nhà thầu được phép xử lý.")\n                            _v622_gateway_approval = "" if papproval == _V622_PROJECT_VIEWER else papproval\n                            _v622_effective_role = (\n                                "read" if papproval == _V622_PROJECT_VIEWER\n                                else "update" if papproval == "CONTRACTOR"\n                                else prole\n                            )\n                            expected_approval = _v622_gateway_approval or (\n                                "PROJECT_MANAGEMENT" if _v622_effective_role == "admin" else ""\n                            )\n                            result = gw.set_user(\n                                token, target_email, pname, _v622_effective_role, ppass, _v622_gateway_approval\n                            )\n                            saved = dict(result.get("user") or {})\n'''
    source = _replace_once(source, save_old, save_new, "save enforced role")

    success_old = '''                            else:\n                                if str(target_email or "").lower() == str(ident.get("email") or "").lower():\n                                    st.session_state.pop("qlda_drive_identity", None)\n                                st.success(\n                                    "Đã lưu người dùng: "\n                                    f"{APPROVAL_ROLE_LABELS.get(saved_role, saved_role) if saved_role else 'Không tham gia duyệt'}."\n                                )\n                                st.rerun()\n'''
    success_new = '''                            else:\n                                _v622_saved_classification = (\n                                    _V622_PROJECT_VIEWER if papproval == _V622_PROJECT_VIEWER else saved_role\n                                )\n                                if _v622_master_access_id:\n                                    _v622_set_user_project_access(\n                                        db, _v622_master_access_id, target_email,\n                                        _v622_saved_classification, pcontractor,\n                                    )\n                                if str(target_email or "").lower() == str(ident.get("email") or "").lower():\n                                    st.session_state.pop("qlda_drive_identity", None)\n                                st.success(\n                                    "Đã lưu người dùng: "\n                                    f"{_v622_approval_labels.get(_v622_saved_classification, _v622_saved_classification) if _v622_saved_classification else 'Không tham gia duyệt'} "\n                                    f"• quyền hệ thống {_v622_effective_role}."\n                                )\n                                st.rerun()\n'''
    source = _replace_once(source, success_old, success_new, "persist project scope")

    table_old = '''                            "Phân loại duyệt": APPROVAL_ROLE_LABELS.get(_user_approval_role(u), "Không tham gia duyệt"),\n                            "Quyền": {"read":"Chỉ đọc","update":"Cập nhật","admin":"Admin"}.get(u.get("role", ""), u.get("role", "")),\n'''
    table_new = '''                            "Phân loại duyệt": _v622_approval_labels.get(\n                                _v622_effective_user_classification(\n                                    db, _v622_master_access_id, str(u.get("email") or ""), _user_approval_role(u)\n                                ) if _v622_master_access_id else _user_approval_role(u),\n                                "Không tham gia duyệt",\n                            ),\n                            "Phạm vi nhà thầu": (\n                                _v622_user_contractor_scope_label(\n                                    db, _v622_master_access_id, str(u.get("email") or ""), _user_approval_role(u)\n                                ) if _v622_master_access_id else "Tất cả nhà thầu"\n                            ),\n                            "Quyền": {"read":"Chỉ đọc","update":"Cập nhật","admin":"Admin"}.get(u.get("role", ""), u.get("role", "")),\n'''
    source = _replace_once(source, table_old, table_new, "user scope table")

    delete_old = '''                                    gw.delete_user(token, target)\n                                    st.success("Đã xóa người dùng và thu hồi quyền Drive.")\n'''
    delete_new = '''                                    gw.delete_user(token, target)\n                                    _v622_delete_user_project_access(db, target, None)\n                                    st.success("Đã xóa người dùng, thu hồi quyền Drive và phạm vi nhà thầu.")\n'''
    source = _replace_once(source, delete_old, delete_new, "delete project scope")

    note_user_old = '''                    _ui_note("Chỉ đọc = Viewer Drive • Cập nhật = Viewer Drive + được thêm/sửa/upload qua app, KHÔNG được xóa • Admin = Editor Drive + toàn quyền quản trị/xóa trong app. Vai trò phê duyệt là lớp quyền riêng, dùng cho Nhà thầu/Ban điều hành/TVGS/Ban QLDA.")\n'''
    note_user_new = '''                    _ui_note("Chỉ đọc = Viewer Drive • Cập nhật = Viewer Drive + được thêm/sửa/upload qua app, KHÔNG được xóa • Admin = Editor Drive + toàn quyền quản trị/xóa trong app. Phân loại Nhà thầu bắt buộc gán đúng 01 nhà thầu; Chỉ xem toàn bộ nhà thầu luôn là read-only.")\n'''
    source = _replace_once(source, note_user_old, note_user_new, "user permission help")

    checks = (
        PATCH_MARKER,
        "Phạm vi nhà thầu",
        "Chỉ xem toàn bộ nhà thầu",
        "_v622_set_ai_workspace_scope",
        "approval_role=_v622_approval_role",
        "render_ai_assistant(_master_pid if _v622_can_view_all_contractors else pid)",
        "render_settings(_master_pid)",
    )
    missing = [item for item in checks if item not in source]
    if missing:
        raise RuntimeError(f"{PATCH_MARKER}: generated source validation failed: {missing}")
    compile(source, "streamlit_app_v622_contractor_access.py", "exec")
    return source
