from __future__ import annotations

from typing import Any


PATCH_MARKER = "V6.22 DEFAULT WORKSPACE ADMIN ONLY V2 NATIVE"


def _approval(value: Any) -> str:
    return str(value or "").strip().upper().replace("-", "_").replace(" ", "_")


def _is_default_workspace(row: dict[str, Any], master_project_id: int) -> bool:
    master_id = int(master_project_id or 0)
    workspace_id = int(row.get("workspace_project_id") or 0)
    return bool(int(row.get("is_default") or 0)) or (master_id > 0 and workspace_id == master_id)


def install_default_workspace_admin_guard() -> None:
    """Install fail-closed default-workspace access rules on native access APIs.

    Security rules after installation:
    - Admin sees and can manage the default workspace.
    - Every non-Admin role has the default workspace removed from authorized rows.
    - CONTRACTOR accounts cannot be assigned to the default workspace.
    - If a legacy CONTRACTOR assignment points at the default workspace, access is denied.
    - The visible contractor selector is always derived from the guarded native rows.

    Historical generated-source rewriting is intentionally not installed here.
    Production now consumes ``contractor_access_control`` through the composition
    root, so access enforcement belongs on these native functions rather than on a
    second text-rewrite layer.
    """
    import qlda.runtime_core.contractor_access_control as ac
    import qlda.runtime_core.contractor_workspace as cw

    if getattr(ac, "_qlda_default_workspace_admin_guard_installed", False):
        return

    original_set_user_project_access = ac.set_user_project_access
    original_authorized_contractor_rows = ac.authorized_contractor_rows

    def guarded_set_user_project_access(
        db,
        master_project_id: int,
        user_email: str,
        classification: str,
        contractor_id: int | None = None,
    ):
        if _approval(classification) == ac.CONTRACTOR:
            contractor = ac._contractor_by_id(
                db, int(master_project_id), int(contractor_id or 0)
            )
            if contractor and _is_default_workspace(contractor, int(master_project_id)):
                raise ValueError(
                    "Workspace mặc định chỉ Admin được truy cập; không thể gán workspace này "
                    "cho tài khoản Nhà thầu hoặc người dùng khác."
                )
        return original_set_user_project_access(
            db, master_project_id, user_email, classification, contractor_id
        )

    def guarded_authorized_contractor_rows(
        db,
        master_project_id: int,
        user_email: str,
        approval_role: str,
        *,
        active_only: bool = True,
        can_admin: bool = False,
    ) -> list[dict[str, Any]]:
        master_id = int(master_project_id)
        if can_admin:
            return original_authorized_contractor_rows(
                db, master_id, user_email, approval_role, active_only=active_only
            )

        rows = [
            dict(row)
            for row in cw.list_contractors(db, master_id, active_only=active_only)
            if not _is_default_workspace(dict(row), master_id)
        ]
        role = _approval(approval_role)
        if role != ac.CONTRACTOR:
            return rows

        email = ac._email(user_email)
        if not email:
            raise PermissionError("Tài khoản Nhà thầu chưa xác định được email đăng nhập.")

        access = ac.get_user_project_access(db, master_id, email)
        # Safe compatibility: auto-bind only when there is exactly one VISIBLE,
        # non-default contractor. The default workspace is never auto-assigned.
        if not access and len(rows) == 1:
            access = guarded_set_user_project_access(
                db, master_id, email, ac.CONTRACTOR, int(rows[0].get("id") or 0)
            )

        if str(access.get("access_mode") or "").upper() != ac.CONTRACTOR:
            raise PermissionError(
                "Tài khoản Nhà thầu chưa được gán workspace nhà thầu được phép. "
                "Workspace mặc định chỉ Admin truy cập."
            )

        contractor_id = int(access.get("contractor_id") or 0)
        allowed = [row for row in rows if int(row.get("id") or 0) == contractor_id]
        if not allowed:
            raise PermissionError(
                "Workspace được gán không còn được phép truy cập. Workspace mặc định chỉ Admin; "
                "vui lòng liên hệ Admin để gán một nhà thầu khác."
            )
        return allowed

    def guarded_render_authorized_contractor_selector(
        db,
        master_project_id: int,
        *,
        can_admin: bool = False,
        current_user: str = "",
        approval_role: str = "",
    ) -> tuple[int, dict[str, Any]]:
        import streamlit as st

        master_id = int(master_project_id)
        try:
            rows = guarded_authorized_contractor_rows(
                db,
                master_id,
                current_user,
                approval_role,
                active_only=True,
                can_admin=bool(can_admin),
            )
        except PermissionError as exc:
            st.sidebar.error(str(exc))
            st.error(str(exc))
            st.stop()
            return master_id, {}

        if not rows:
            if can_admin:
                return master_id, {}
            message = (
                "Dự án chưa có workspace nhà thầu nào mà tài khoản này được phép truy cập. "
                "Workspace mặc định được ẩn hoàn toàn và chỉ Admin quản lý."
            )
            st.sidebar.warning(message)
            st.warning(message)
            st.stop()
            return master_id, {}

        if _approval(approval_role) == ac.CONTRACTOR:
            info = dict(rows[0])
            st.sidebar.markdown(
                f"**Nhà thầu:** {info.get('contractor_code','')} - {info.get('contractor_name','')}"
            )
            st.sidebar.caption(
                "🔒 Phạm vi tài khoản: chỉ workspace nhà thầu được gán. Workspace mặc định bị ẩn."
            )
            return int(info["workspace_project_id"]), info

        by_workspace = {int(row["workspace_project_id"]): dict(row) for row in rows}
        ids = list(by_workspace)
        state_key = f"contractor_workspace_{master_id}"
        current = int(st.session_state.get(state_key) or ids[0])
        if current not in by_workspace:
            current = ids[0]
        selected = st.sidebar.selectbox(
            "Nhà thầu đang làm việc",
            ids,
            index=ids.index(current),
            format_func=lambda wid: (
                f"{by_workspace[wid].get('contractor_code','')} - "
                f"{by_workspace[wid].get('contractor_name','')}"
            ),
            key=f"contractor_workspace_select_{master_id}",
        )
        st.session_state[state_key] = int(selected)
        info = dict(by_workspace[int(selected)])
        if can_admin:
            st.sidebar.caption("🔓 Admin: có quyền truy cập cả workspace mặc định.")
        else:
            st.sidebar.caption(
                "👁 Có quyền xem/chuyển các nhà thầu được phép. 🔒 Workspace mặc định chỉ Admin."
            )
        return int(selected), info

    ac.set_user_project_access = guarded_set_user_project_access
    ac.authorized_contractor_rows = guarded_authorized_contractor_rows
    ac.render_authorized_contractor_selector = guarded_render_authorized_contractor_selector
    ac._qlda_default_workspace_admin_guard_installed = True
    ac._qlda_default_workspace_admin_guard_marker = PATCH_MARKER
