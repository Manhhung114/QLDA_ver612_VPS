from __future__ import annotations


PATCH_MARKER = "V6.23 UI UX SOURCE PATCH V1"


def patch_uiux_v623(source: str) -> str:
    """Upgrade the validated V7 presentation layer to V6.23 UI/UX.

    Business formulas, DB schema, permissions, upload and approval workflows are
    intentionally untouched. This patch only replaces presentation/runtime hooks.
    """
    if PATCH_MARKER in source:
        return source

    import_anchor = (
        "from ui_v7_compact_v622 import install_theme_v7, install_caption_policy_v7, "
        "install_vn_datetime_policy_v7, render_admin_caption_toggle_v7, render_header_v7, render_overview_v7\n"
    )
    if import_anchor not in source:
        raise RuntimeError(f"{PATCH_MARKER}: V7 runtime import anchor missing")
    source = source.replace(
        import_anchor,
        import_anchor
        + "from ui_v623_ux import install_theme_v623, apply_pending_navigation_v623, render_header_v623, render_overview_v623\n",
        1,
    )

    theme_anchor = "install_theme_v7(st)\n"
    if theme_anchor not in source:
        raise RuntimeError(f"{PATCH_MARKER}: V7 theme anchor missing")
    source = source.replace(theme_anchor, theme_anchor + "install_theme_v623(st)\n", 1)

    header_old = "render_header_v7(st, p, _contractor_ctx, _v7_identity)"
    if header_old not in source:
        raise RuntimeError(f"{PATCH_MARKER}: V7 header anchor missing")
    source = source.replace(header_old, "render_header_v623(st, p, _contractor_ctx, _v7_identity)", 1)

    nav_anchor = "with st.sidebar:\n    render_admin_caption_toggle_v7(st, bool(_is_admin()))"
    if nav_anchor not in source:
        raise RuntimeError(f"{PATCH_MARKER}: V7 navigation anchor missing")
    source = source.replace(
        nav_anchor,
        "apply_pending_navigation_v623(st, _master_pid)\n" + nav_anchor,
        1,
    )

    overview_old = '''render_overview_v7(
        st, db, pid,
        doc_config=DOC_CONFIG,
        drawing_types=DRAWING_TYPES,
        detailed_renderer=render_reports,
    )'''
    overview_new = '''render_overview_v623(
        st, db, pid,
        master_project_id=_master_pid,
        identity=_v7_identity,
        doc_config=DOC_CONFIG,
        drawing_types=DRAWING_TYPES,
        detailed_renderer=render_reports,
    )'''
    if overview_old not in source:
        raise RuntimeError(f"{PATCH_MARKER}: V7 overview anchor missing")
    source = source.replace(overview_old, overview_new, 1)

    source = source.replace('st.markdown("#### Điều hướng")', 'st.markdown("#### Không gian làm việc")', 1)
    source += f"\n# {PATCH_MARKER}\n"

    for marker in (
        "install_theme_v623",
        "apply_pending_navigation_v623",
        "render_header_v623",
        "render_overview_v623",
        "master_project_id=_master_pid",
        "identity=_v7_identity",
        "V6.23 UI UX SOURCE PATCH V1",
    ):
        if marker not in source:
            raise RuntimeError(f"{PATCH_MARKER}: generated source missing {marker}")

    compile(source, "streamlit_app_v623_uiux.py", "exec")
    return source
