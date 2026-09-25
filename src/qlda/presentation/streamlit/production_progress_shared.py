from __future__ import annotations

"""Presentation owner for the Contractor Data Hub production view."""


def render_production_progress_shared(st, db, project_id: int, *, identity: dict | None = None) -> None:
    from qlda.presentation.streamlit import production_progress_ui as ui

    project_id = int(project_id)
    identity = dict(identity or {})
    role = str(identity.get("role") or "read").strip().lower()
    can_update = role in {"update", "admin"}
    can_admin = role == "admin"

    try:
        ui.apply_to_environment()
    except Exception:
        pass

    ui._handle_oauth_callback(st, project_id, identity)

    try:
        contractors = ui._authorized_contractors(db, project_id, identity)
    except PermissionError as exc:
        st.error(str(exc))
        return
    except Exception as exc:
        st.error(f"Không xác định được phạm vi nhà thầu: {exc}")
        return
    if not contractors:
        st.info("Dự án chưa có nhà thầu đang hoạt động trong phạm vi tài khoản này.")
        return

    client, stored = ui._google_client(st, project_id)
    service = ui.ContractorDataHubService(db, client=client)
    try:
        spaces = service.ensure_spaces(project_id, contractors)
    except Exception as exc:
        st.error(f"Không khởi tạo được kho dữ liệu nhà thầu: {exc}")
        return

    workspace_ids = [
        int(x.get("workspace_project_id") or 0)
        for x in contractors
        if int(x.get("workspace_project_id") or 0) > 0
    ]

    st.subheader("📊 Sản lượng & Kho dữ liệu nhà thầu")
    st.caption(
        "Mỗi nhà thầu là một kho dữ liệu riêng. QLDA đồng bộ Google ở chế độ read-only, lưu snapshot lịch sử. "
        "Dữ liệu trong kho này được dùng chung trực tiếp bởi Trợ lý AI của app theo đúng quyền người dùng."
    )

    flash = st.session_state.pop(ui._connection_flash_key(project_id), None)
    if flash:
        level, message = flash
        if level == "success":
            st.success(message)
        else:
            st.error(message)

    tab_overview, tab_spaces, tab_sources, tab_history = st.tabs([
        "📈 Tổng quan",
        "🏢 Kho nhà thầu",
        "🔗 Nguồn Google",
        "🕘 Lịch sử",
    ])

    with tab_overview:
        ui._render_overview(st, service, project_id, contractors, workspace_ids)
    with tab_spaces:
        ui._render_data_spaces(st, service, project_id, contractors, spaces, can_update=can_update)
    with tab_sources:
        ui._render_sources(
            st,
            service,
            project_id,
            identity,
            contractors,
            spaces,
            client,
            stored,
            can_update=can_update,
            can_admin=can_admin,
        )
    with tab_history:
        ui._render_history(st, service, project_id, workspace_ids)


def install_production_progress_shared_ai_ui() -> None:
    from qlda.presentation.streamlit import production_progress_ui

    if getattr(production_progress_ui, "_qlda_shared_ai_ui_v1", False):
        return
    production_progress_ui.render_production_progress = render_production_progress_shared
    production_progress_ui._qlda_shared_ai_ui_v1 = True


__all__ = ["render_production_progress_shared", "install_production_progress_shared_ai_ui"]
