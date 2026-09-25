from __future__ import annotations

"""Admin-only management surfaces for the Contractor Data Hub.

Project users may keep the production overview/history needed for day-to-day
work, but only system Admin accounts can see or operate the contractor data-space
and Google-source management tabs. Data-quality alerts and sync error details
are also restricted to Admin.
"""

from typing import Any

PATCH_MARKER = "V7 CONTRACTOR DATA ADMIN VISIBILITY V1"

_ADMIN_TABS = [
    "📈 Tổng quan",
    "🏢 Kho nhà thầu",
    "🔗 Nguồn Google",
    "🕘 Lịch sử",
]
_USER_TABS = [
    "📈 Tổng quan",
    "🕘 Lịch sử",
]


def visible_tab_labels(can_admin: bool) -> list[str]:
    """Return the Data Hub tabs that the current role is allowed to see."""
    return list(_ADMIN_TABS if can_admin else _USER_TABS)


class _RestrictedRepo:
    """Read-only presentation proxy that hides sync-error details."""

    def __init__(self, repo: Any) -> None:
        self._repo = repo

    def project_metrics(self, *args, **kwargs):
        rows = self._repo.project_metrics(*args, **kwargs)
        clean: list[dict[str, Any]] = []
        for raw in rows or []:
            row = dict(raw or {})
            row["last_error"] = ""
            clean.append(row)
        return clean

    def __getattr__(self, name: str):
        return getattr(self._repo, name)


class _RestrictedService:
    """Presentation proxy that removes the Admin-only alert feed."""

    def __init__(self, service: Any) -> None:
        self._service = service
        self.repo = _RestrictedRepo(service.repo)

    def project_alerts(self, *args, **kwargs):
        del args, kwargs
        return []

    def __getattr__(self, name: str):
        return getattr(self._service, name)


class _RestrictedStreamlit:
    """Delegate Streamlit calls while suppressing data-quality warning banners."""

    def __init__(self, st: Any) -> None:
        self._st = st

    def warning(self, *args, **kwargs):
        del args, kwargs
        return None

    def __getattr__(self, name: str):
        return getattr(self._st, name)


def _render_overview_for_role(
    ui,
    st,
    service,
    project_id: int,
    contractors: list[dict[str, Any]],
    workspace_ids: list[int],
    *,
    can_admin: bool,
) -> None:
    if can_admin:
        ui._render_overview(st, service, project_id, contractors, workspace_ids)
        return

    # Non-admin users still see production data, but not the "Cảnh báo dữ liệu"
    # section, sync error text or yellow data-quality warning banners.
    ui._render_overview(
        _RestrictedStreamlit(st),
        _RestrictedService(service),
        project_id,
        contractors,
        workspace_ids,
    )


def render_production_progress_admin_visibility(
    st,
    db,
    project_id: int,
    *,
    identity: dict | None = None,
) -> None:
    """Render shared Data Hub UI with Admin-only management and warnings."""
    from qlda.presentation.streamlit import production_progress_ui as ui

    project_id = int(project_id)
    identity = dict(identity or {})
    role = str(identity.get("role") or "read").strip().lower()
    can_admin = role == "admin"

    try:
        ui.apply_to_environment()
    except Exception:
        pass

    # OAuth callbacks can create/update the persisted project Google connection,
    # therefore they are part of Admin management and must not run for other roles.
    if can_admin:
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

    # Reading overview/history does not require a Google token and must not create
    # or adjust data spaces. Only Admin initializes/manages those objects.
    if can_admin:
        client, stored = ui._google_client(st, project_id)
        service = ui.ContractorDataHubService(db, client=client)
        try:
            spaces = service.ensure_spaces(project_id, contractors)
        except Exception as exc:
            st.error(f"Không khởi tạo được kho dữ liệu nhà thầu: {exc}")
            return
    else:
        client, stored = None, {}
        service = ui.ContractorDataHubService(db, client=None)
        spaces = []

    workspace_ids = [
        int(x.get("workspace_project_id") or 0)
        for x in contractors
        if int(x.get("workspace_project_id") or 0) > 0
    ]

    st.subheader("📊 Sản lượng & Kho dữ liệu nhà thầu")
    if can_admin:
        st.caption(
            "Admin quản lý kho nhà thầu, nguồn Google và cảnh báo dữ liệu. "
            "Google được đồng bộ read-only; dữ liệu được dùng chung bởi Trợ lý AI theo đúng quyền người dùng."
        )
    else:
        st.caption(
            "Dữ liệu sản lượng được đồng bộ từ kho nhà thầu. Các thiết lập kho, nguồn Google "
            "và cảnh báo dữ liệu được quản lý riêng bởi Admin."
        )

    if can_admin:
        flash = st.session_state.pop(ui._connection_flash_key(project_id), None)
        if flash:
            level, message = flash
            if level == "success":
                st.success(message)
            else:
                st.error(message)

        tab_overview, tab_spaces, tab_sources, tab_history = st.tabs(visible_tab_labels(True))
        with tab_overview:
            _render_overview_for_role(
                ui,
                st,
                service,
                project_id,
                contractors,
                workspace_ids,
                can_admin=True,
            )
        with tab_spaces:
            ui._render_data_spaces(
                st,
                service,
                project_id,
                contractors,
                spaces,
                can_update=True,
            )
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
                can_update=True,
                can_admin=True,
            )
        with tab_history:
            ui._render_history(st, service, project_id, workspace_ids)
        return

    tab_overview, tab_history = st.tabs(visible_tab_labels(False))
    with tab_overview:
        _render_overview_for_role(
            ui,
            st,
            service,
            project_id,
            contractors,
            workspace_ids,
            can_admin=False,
        )
    with tab_history:
        ui._render_history(st, service, project_id, workspace_ids)


def install_contractor_data_admin_visibility() -> None:
    """Install after the shared-AI Data Hub UI patch so this policy wins last."""
    from qlda.presentation.streamlit import production_progress_ui

    if getattr(production_progress_ui, "_qlda_admin_visibility_v1", False):
        return
    production_progress_ui.render_production_progress = render_production_progress_admin_visibility
    production_progress_ui._qlda_admin_visibility_v1 = True
    production_progress_ui._qlda_admin_visibility_marker = PATCH_MARKER


__all__ = [
    "PATCH_MARKER",
    "visible_tab_labels",
    "render_production_progress_admin_visibility",
    "install_contractor_data_admin_visibility",
]
