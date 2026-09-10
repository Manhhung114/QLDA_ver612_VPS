from __future__ import annotations

import re


PATCH_MARKER = "V7 COMPACT UI SOURCE PATCH V1"


def _insert_runtime_import(source: str) -> str:
    anchor = "import streamlit as st\n"
    count = source.count(anchor)
    if count != 1:
        raise RuntimeError(f"{PATCH_MARKER}: expected one Streamlit import, found {count}")
    addition = (
        anchor
        + "from ui_v7_compact_v622 import install_theme_v7, render_header_v7, render_overview_v7\n"
    )
    return source.replace(anchor, addition, 1)


def _install_theme_after_page_config(source: str) -> str:
    # set_page_config must remain the first Streamlit command.
    match = re.search(r"(?m)^st\.set_page_config\([^\n]*\)\n", source)
    if not match:
        raise RuntimeError(f"{PATCH_MARKER}: st.set_page_config anchor missing")
    return source[: match.end()] + "install_theme_v7(st)\n" + source[match.end() :]


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

    replacement = '''# V7 decision-first navigation: five business groups, lazy-rendered one section at a time.\n_v7_group_labels = [\n    "🏠 Tổng quan",\n    "🏗️ Thi công",\n    "📁 Hồ sơ",\n    "💰 Tài chính",\n    "📚 Công cụ",\n]\nwith st.sidebar:\n    st.markdown("#### Điều hướng")\n    _v7_group = st.radio(\n        "Nhóm chức năng",\n        _v7_group_labels,\n        key=f"qlda_v7_group_{_master_pid}",\n        label_visibility="collapsed",\n    )\n\nif _v7_group == "🏠 Tổng quan":\n    render_overview_v7(\n        st, db, pid,\n        doc_config=DOC_CONFIG,\n        drawing_types=DRAWING_TYPES,\n        detailed_renderer=render_reports,\n    )\nelse:\n    _v7_sections = {\n        "🏗️ Thi công": [\n            ("📅 Tiến độ", lambda: render_schedule(pid)),\n            ("📦 Vật tư", lambda: render_material_management(pid)),\n            ("📷 Nhật ký", lambda: render_site_diary(pid)),\n        ],\n        "📁 Hồ sơ": [\n            ("📁 Hồ sơ", lambda: render_documents(pid)),\n            ("📐 Bản vẽ", lambda: render_drawings(pid)),\n        ],\n        "💰 Tài chính": [\n            ("💰 Chi phí", lambda: render_cost_management(pid)),\n        ],\n        "📚 Công cụ": [\n            ("📚 Văn bản QLXD", lambda: render_legal_documents()),\n            ("🤖 Trợ lý AI", lambda: render_ai_assistant(_master_pid if _v622_can_view_all_contractors else pid)),\n            ("🏗️ Dự án", lambda: render_project_info(_master_pid)),\n            ("⚙️ Cài đặt", lambda: render_settings(_master_pid)),\n        ],\n    }\n    _v7_current_sections = _v7_sections.get(_v7_group, [])\n    if len(_v7_current_sections) == 1:\n        _v7_current_sections[0][1]()\n    elif _v7_current_sections:\n        _v7_labels = [item[0] for item in _v7_current_sections]\n        _v7_choice = st.radio(\n            "Chức năng",\n            _v7_labels,\n            horizontal=True,\n            key=f"qlda_v7_section_{_master_pid}_{_v7_group}",\n            label_visibility="collapsed",\n        )\n        dict(_v7_current_sections)[_v7_choice]()\n'''
    return source[:start] + replacement + source[end:]


def patch_ui_v7_compact(source: str) -> str:
    """Apply visual/navigation-only V7 on top of the validated V6.22 backend."""
    if PATCH_MARKER in source:
        return source

    source = _insert_runtime_import(source)
    source = _install_theme_after_page_config(source)
    source = _replace_main_header(source)
    source = _replace_main_navigation(source)

    # Keep secondary legal tools available but closed by default. The primary
    # page becomes search + metrics + result table rather than a large tool panel.
    source = source.replace(
        'with st.expander("🔎 Google / Tìm kiếm online toàn web", expanded=True):',
        'with st.expander("🔎 Tìm kiếm online nâng cao", expanded=False):',
        1,
    )

    # Simpler user-facing AI label; all full-scan/audit logic remains unchanged.
    source = source.replace(
        'st.subheader("🤖 Trợ lý AI QLDA")',
        'st.subheader("🤖 Trợ lý QLDA")',
        1,
    )

    source += f"\n# {PATCH_MARKER}\n"

    required = (
        "by: Hoàng Mạnh Hùng",  # helper import/source marker validation happens in tests
        "🏠 Tổng quan",
        "🏗️ Thi công",
        "📁 Hồ sơ",
        "💰 Tài chính",
        "📚 Công cụ",
        "render_overview_v7",
    )
    # The credit text lives in the isolated runtime file rather than generated
    # source, so only validate the generated-source markers here.
    for marker in required[1:]:
        if marker not in source:
            raise RuntimeError(f"{PATCH_MARKER}: generated source missing {marker}")
    if "_main_actions[_main_choice]()" in source:
        raise RuntimeError(f"{PATCH_MARKER}: legacy 11-section navigation still present")

    compile(source, "streamlit_app_v7_compact.py", "exec")
    return source
