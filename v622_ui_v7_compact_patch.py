from __future__ import annotations

import re


PATCH_MARKER = "V7 COMPACT UI SOURCE PATCH V6 CONTRACT MANAGEMENT"


def _insert_runtime_import(source: str) -> str:
    anchor = "import streamlit as st\n"
    count = source.count(anchor)
    if count != 1:
        raise RuntimeError(f"{PATCH_MARKER}: expected one Streamlit import, found {count}")
    addition = (
        anchor
        + "from ui_v7_compact_v622 import install_theme_v7, install_caption_policy_v7, install_vn_datetime_policy_v7, render_admin_caption_toggle_v7, render_header_v7, render_overview_v7\n"
        + "from work_tasks_v1_v622 import render_work_tasks_v1\n"
        + "from vps_status_v622 import render_vps_status_v622\n"
        + "from contract_management_v622 import can_access_contract_management, render_contract_management_v622\n"
    )
    return source.replace(anchor, addition, 1)


def _install_theme_after_page_config(source: str) -> str:
    # set_page_config must remain the first Streamlit command.
    match = re.search(r"(?m)^st\.set_page_config\([^\n]*\)\n", source)
    if not match:
        raise RuntimeError(f"{PATCH_MARKER}: st.set_page_config anchor missing")
    addition = "install_theme_v7(st)\ninstall_vn_datetime_policy_v7(st)\ninstall_caption_policy_v7(st)\n"
    return source[: match.end()] + addition + source[match.end() :]


def _replace_main_header(source: str) -> str:
    # There are several login/error titles. The last title is the operational app
    # header rendered after project + contractor selection.
    start = source.rfind('st.title("🏗️ QLDA Xây dựng')
    if start < 0:
        start = source.rfind("st.title('🏗️ QLDA Xây dựng")
    end = source.find("_main_sections = [", start)
    if start < 0 or end < 0:
        raise RuntimeError(f"{PATCH_MARKER}: main header/navigation anchor missing")

    replacement = '''# V7 compact operational header. Technical backend/version text stays in Settings.\nif not pid:\n    st.info("Hãy tạo dự án đầu tiên ở thanh bên trái.")\n    st.stop()\n\np = db.project(_master_pid)\n_v7_identity = _cloud_identity()\nrender_header_v7(st, p, _contractor_ctx, _v7_identity)\n\n'''
    return source[:start] + replacement + source[end:]


def _replace_main_navigation(source: str) -> str:
    start = source.find("_main_sections = [")
    if start < 0:
        raise RuntimeError(f"{PATCH_MARKER}: legacy main section list missing")
    end_marker = "_main_actions[_main_choice]()"
    end = source.find(end_marker, start)
    if end < 0:
        raise RuntimeError(f"{PATCH_MARKER}: legacy main dispatch missing")
    end += len(end_marker)

    replacement = '''# V7 decision-first navigation: business groups, lazy-rendered one section at a time.\n_v7_group_labels = [\n    "🏠 Tổng quan",\n    "📋 Công việc",\n    "🏗️ Thi công",\n    "📁 Hồ sơ",\n    "💰 Tài chính",\n    "📚 Công cụ",\n]\nwith st.sidebar:\n    render_admin_caption_toggle_v7(st, bool(_is_admin()))\n    st.markdown("#### Điều hướng")\n    _v7_group = st.radio(\n        "Nhóm chức năng",\n        _v7_group_labels,\n        key=f"qlda_v7_group_{_master_pid}",\n        label_visibility="collapsed",\n    )\n\nif _v7_group == "🏠 Tổng quan":\n    render_overview_v7(\n        st, db, pid,\n        doc_config=DOC_CONFIG,\n        drawing_types=DRAWING_TYPES,\n        detailed_renderer=render_reports,\n    )\nelif _v7_group == "📋 Công việc":\n    render_work_tasks_v1(\n        st, db, pid,\n        master_project_id=_master_pid,\n        identity=_v7_identity,\n        can_update=bool(_can_update()),\n        is_admin=bool(_is_admin()),\n        users=_approval_users(),\n        gateway=_drive_gateway(),\n        session_token=_gateway_session_token(),\n    )\nelse:\n    _v7_sections = {\n        "🏗️ Thi công": [\n            ("📅 Tiến độ", lambda: render_schedule(pid)),\n            ("📦 Vật tư", lambda: render_material_management(pid)),\n            ("📷 Nhật ký", lambda: render_site_diary(pid)),\n        ],\n        "📁 Hồ sơ": [\n            ("📁 Hồ sơ", lambda: render_documents(pid)),\n            ("📐 Bản vẽ", lambda: render_drawings(pid)),\n        ],\n        "💰 Tài chính": [\n            ("💰 Chi phí", lambda: render_cost_management(pid)),\n        ],\n        "📚 Công cụ": [\n            ("📚 Văn bản QLXD", lambda: render_legal_documents()),\n            ("🤖 Trợ lý AI", lambda: render_ai_assistant(_master_pid if _v622_can_view_all_contractors else pid)),\n            ("🏗️ Dự án", lambda: render_project_info(_master_pid)),\n            ("⚙️ Cài đặt", lambda: render_settings(_master_pid)),\n        ],\n    }\n    # Contract data is private by business role and isolated to the selected\n    # contractor workspace. Other roles never receive the navigation item.\n    if _v7_group == "📁 Hồ sơ" and can_access_contract_management(_v7_identity):\n        _v7_sections["📁 Hồ sơ"].append(\n            (\n                "📑 Quản lý hợp đồng",\n                lambda: render_contract_management_v622(\n                    st, db, pid,\n                    identity=_v7_identity,\n                    is_admin=bool(_is_admin()),\n                    gateway=_drive_gateway(),\n                    session_token=_gateway_session_token(),\n                ),\n            )\n        )\n    # VPS/server information is sensitive infrastructure metadata. It is added\n    # to Công cụ only for Admin and is absent from every non-Admin navigation.\n    if _v7_group == "📚 Công cụ" and bool(_is_admin()):\n        _v7_sections["📚 Công cụ"].append(\n            ("🖥️ Trạng thái VPS", lambda: render_vps_status_v622(st, db, is_admin=True))\n        )\n    _v7_current_sections = _v7_sections.get(_v7_group, [])\n    if len(_v7_current_sections) == 1:\n        _v7_current_sections[0][1]()\n    elif _v7_current_sections:\n        _v7_labels = [item[0] for item in _v7_current_sections]\n        _v7_choice = st.radio(\n            "Chức năng",\n            _v7_labels,\n            horizontal=True,\n            key=f"qlda_v7_section_{_master_pid}_{_v7_group}",\n            label_visibility="collapsed",\n        )\n        dict(_v7_current_sections)[_v7_choice]()\n'''
    return source[:start] + replacement + source[end:]


def _indent_block(source: str, start_token: str, end_token: str, wrapper_line: str, label: str) -> str:
    """Wrap one source range in a Streamlit container without changing its logic.

    This is a visual-only optimization. If a prior business patch has changed the
    source shape, leave that block untouched instead of preventing app startup.
    """
    start = source.find(start_token)
    if start < 0:
        return source
    line_start = source.rfind("\n", 0, start) + 1
    end = source.find(end_token, start)
    if end < 0:
        return source
    end_line_start = source.rfind("\n", 0, end) + 1
    block = source[line_start:end_line_start]
    first_newline = source.find("\n", line_start)
    first_line = source[line_start:first_newline if first_newline >= 0 else len(source)]
    indent = first_line[: len(first_line) - len(first_line.lstrip())]
    wrapped = indent + wrapper_line + "\n"
    for line in block.splitlines(keepends=True):
        wrapped += (indent + "    " + line[len(indent):]) if line.strip() else line
    return source[:line_start] + wrapped + source[end_line_start:]


def _wrap_form(source: str, anchor: str, title: str) -> str:
    """Put matching legacy CRUD forms inside collapsed expanders.

    The V7 layer is presentation-only. Earlier V6.22 patches are allowed to
    rename, replace or remove a form, so zero matches is valid and must never
    make the production app fail. Multiple matches are wrapped independently.
    """
    lines = source.splitlines(keepends=True)
    hits = [i for i, line in enumerate(lines) if anchor in line]
    if not hits:
        return source

    # Process from bottom to top so line indexes above each edit remain stable.
    for i in reversed(hits):
        raw = lines[i]
        stripped = raw.lstrip(" ")
        indent_len = len(raw) - len(stripped)
        indent = " " * indent_len

        end = i + 1
        while end < len(lines):
            line = lines[end]
            if not line.strip():
                end += 1
                continue
            current_indent = len(line) - len(line.lstrip(" "))
            if current_indent <= indent_len:
                break
            end += 1

        block = lines[i:end]
        replacement = [f'{indent}with st.expander("{title}", expanded=False):\n']
        replacement.extend(("    " + line) if line.strip() else line for line in block)
        lines = lines[:i] + replacement + lines[end:]

    return "".join(lines)


def _compact_crud_forms(source: str) -> str:
    # Approval RFA/RFI and Shopdrawing/As-built intentionally stay untouched:
    # their upload/approval workflow must remain immediately visible when active.
    # Every entry is best-effort because earlier business patches may already
    # have replaced a legacy form with another UI structure.
    forms = (
        ('with st.form(f"cost_boq_form_', "✏️ Thêm / sửa BOQ"),
        ('with st.form(f"pay_form_', "✏️ Thêm / sửa thanh toán"),
        ('with st.form(f"vo_cost_form_', "✏️ Thêm / sửa VO"),
        ('with st.form(f"mat_form_', "✏️ Thêm / sửa vật tư"),
        ('with st.form(f"proc_form_', "✏️ Thêm / sửa kế hoạch mua sắm"),
        ('with st.form(f"inv_form_', "✏️ Thêm / sửa phiếu nhập xuất"),
        ('with st.form(f"doc_form_', "✏️ Thêm / sửa hồ sơ"),
        ('with st.form(f"drawing_form_', "✏️ Thêm / sửa bản vẽ"),
        ('with st.form(f"site_diary_form_', "✏️ Thêm / sửa nhật ký"),
    )
    for anchor, title in forms:
        source = _wrap_form(source, anchor, title)
    return source


def _compact_legal_tools(source: str) -> str:
    # Source sync remains fully available, but no longer occupies the main legal
    # screen. Search/filter/table becomes the default visual hierarchy. This is
    # deliberately best-effort so legal business patches can evolve independently.
    source = _indent_block(
        source,
        "    c1, c2, c3, c4 = st.columns(4)\n    actions = [",
        '    with st.expander("🔎 Google / Tìm kiếm online toàn web", expanded=True):',
        'with st.expander("⟳ Cập nhật kho văn bản", expanded=False):',
        "legal sync tools",
    )
    source = source.replace(
        'with st.expander("🔎 Google / Tìm kiếm online toàn web", expanded=True):',
        'with st.expander("🔎 Tìm kiếm online nâng cao", expanded=False):',
        1,
    )
    return source


def _compact_ai(source: str) -> str:
    source = source.replace(
        'st.subheader("🤖 Trợ lý AI QLDA")',
        'st.subheader("🤖 Trợ lý QLDA")',
        1,
    )
    tabs_old = '    tab_chat, tab_risk, tab_file, tab_legal = st.tabs(["💬 Chat với dự án", "📈 Rủi ro & báo cáo", "📎 Đọc hồ sơ", "⚖️ Văn bản AI"])\n'
    tabs_new = '''    # V7: chat là tác vụ chính; công cụ phân tích chuyên sâu đóng mặc định.\n    tab_chat = st.container()\n    tab_risk = st.expander("📈 Phân tích rủi ro & báo cáo", expanded=False)\n    tab_file = st.expander("📎 Đọc / phân tích hồ sơ", expanded=False)\n    tab_legal = st.expander("⚖️ Tra cứu văn bản bằng AI", expanded=False)\n'''
    # A prior AI patch may alter its controls. In that case keep the proven
    # business UI instead of failing the entire application for a cosmetic step.
    if source.count(tabs_old) == 1:
        source = source.replace(tabs_old, tabs_new, 1)
    return source


def _clean_technical_labels(source: str) -> str:
    # Browser/sidebar labels are product-facing. Backend/version details remain
    # visible in Settings/System and in logs, not in daily operational screens.
    source = re.sub(
        r'st\.set_page_config\(page_title="QLDA Xây dựng V6\.22[^\n]*?page_icon="🏗️", layout="wide"\)',
        'st.set_page_config(page_title="QLDA Xây dựng", page_icon="🏗️", layout="wide")',
        source,
        count=1,
    )
    for old in (
        'st.sidebar.markdown("### 🏗️ QLDA Xây dựng V6.22 PostgreSQL VPS AI")',
        'st.sidebar.markdown("### 🏗️ QLDA Xây dựng V6.22 PostgreSQL Cloud AI")',
    ):
        if old in source:
            source = source.replace(old, 'st.sidebar.markdown("### 🏗️ QLDA Xây dựng")', 1)
            break
    return source


def patch_ui_v7_compact(source: str) -> str:
    """Apply visual/navigation-only V7 on top of the validated V6.22 backend."""
    if PATCH_MARKER in source:
        return source

    source = _insert_runtime_import(source)
    source = _install_theme_after_page_config(source)
    source = _clean_technical_labels(source)
    source = _compact_crud_forms(source)
    source = _compact_legal_tools(source)
    source = _compact_ai(source)
    source = _replace_main_header(source)
    source = _replace_main_navigation(source)

    source += f"\n# {PATCH_MARKER}\n"

    # Only structural V7 requirements are fatal. Optional cosmetic compaction
    # markers are intentionally excluded because earlier business patches may
    # legitimately replace those source blocks.
    required = (
        "🏠 Tổng quan",
        "📋 Công việc",
        "🏗️ Thi công",
        "📁 Hồ sơ",
        "💰 Tài chính",
        "📚 Công cụ",
        "render_overview_v7",
        "render_work_tasks_v1",
        "install_vn_datetime_policy_v7",
        "install_caption_policy_v7",
        "render_admin_caption_toggle_v7",
        "render_vps_status_v622",
        "🖥️ Trạng thái VPS",
        "can_access_contract_management",
        "render_contract_management_v622",
        "📑 Quản lý hợp đồng",
    )
    for marker in required:
        if marker not in source:
            raise RuntimeError(f"{PATCH_MARKER}: generated source missing {marker}")
    if "_main_actions[_main_choice]()" in source:
        raise RuntimeError(f"{PATCH_MARKER}: legacy 11-section navigation still present")

    compile(source, "streamlit_app_v7_compact.py", "exec")
    return source
