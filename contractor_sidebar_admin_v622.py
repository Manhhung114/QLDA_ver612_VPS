from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Any


PATCH_MARKER = "V6.22 CONTRACTOR SIDEBAR ADMIN V1"


def _rowdict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    try:
        return {str(k): row[k] for k in row.keys()}
    except Exception:
        try:
            return dict(row)
        except Exception:
            return {}


def _safe_identifier(value: str) -> str:
    text = str(value or "")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", text):
        raise ValueError(f"Tên bảng không hợp lệ: {text!r}")
    return text


def _project_tables_with_project_id(connection) -> list[str]:
    """Return every application table that owns a project_id column.

    PostgreSQL is queried through information_schema. SQLite falls back to
    sqlite_master + PRAGMA. This intentionally discovers future V6.22 tables too,
    so deleting a contractor does not leave IPC/VO/BOQ extension rows behind.
    """
    try:
        rows = connection.execute(
            """SELECT DISTINCT table_name
               FROM information_schema.columns
               WHERE table_schema=current_schema() AND column_name='project_id'
               ORDER BY table_name"""
        ).fetchall()
        names = [str(row[0]) for row in rows if str(row[0] or "")]
        if names:
            return [_safe_identifier(name) for name in names if name != "projects"]
    except Exception:
        pass

    names: list[str] = []
    try:
        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        for row in tables:
            table = _safe_identifier(str(row[0]))
            if table == "projects":
                continue
            cols = connection.execute(f"PRAGMA table_info({table})").fetchall()
            col_names = set()
            for col in cols:
                try:
                    col_names.add(str(col[1]))
                except Exception:
                    data = _rowdict(col)
                    col_names.add(str(data.get("name") or ""))
            if "project_id" in col_names:
                names.append(table)
    except Exception:
        pass
    return names


def _delete_all_workspace_rows(db, workspace_project_id: int, contractor_id: int) -> dict[str, int]:
    """Delete all DB rows for one non-default contractor in one transaction."""
    pid = int(workspace_project_id)
    deleted: dict[str, int] = {}
    with db.connect() as connection:
        tables = _project_tables_with_project_id(connection)

        # Parent tables are safe to delete directly because their child tables
        # already use ON DELETE CASCADE. Extension tables with no FK are caught
        # independently because they also carry project_id.
        for table in tables:
            try:
                cur = connection.execute(f"DELETE FROM {table} WHERE project_id=?", (pid,))
                deleted[table] = max(0, int(getattr(cur, "rowcount", 0) or 0))
            except Exception as exc:
                raise RuntimeError(f"Không thể xóa dữ liệu bảng {table}: {exc}") from exc

        # Remove mapping explicitly before its workspace project row. This works
        # even on an upgraded DB where the historical FK was created differently.
        try:
            cur = connection.execute(
                "DELETE FROM project_contractors WHERE id=? AND workspace_project_id=?",
                (int(contractor_id), pid),
            )
            deleted["project_contractors"] = max(0, int(getattr(cur, "rowcount", 0) or 0))
        except Exception as exc:
            raise RuntimeError(f"Không thể xóa liên kết nhà thầu: {exc}") from exc

        cur = connection.execute("DELETE FROM projects WHERE id=?", (pid,))
        deleted["projects"] = max(0, int(getattr(cur, "rowcount", 0) or 0))
        if deleted["projects"] != 1:
            raise RuntimeError("Không xóa được workspace dự án của nhà thầu.")
    return deleted


def _purge_local_vps_files(project_code: str) -> dict[str, int]:
    """Permanently remove local-VPS files and local file metadata for a workspace."""
    try:
        import local_vps_backend_v622 as lb
    except Exception:
        return {"files": 0, "bytes": 0, "metadata": 0}

    try:
        if not lb.database_url():
            return {"files": 0, "bytes": 0, "metadata": 0}
    except Exception:
        return {"files": 0, "bytes": 0, "metadata": 0}

    safe_code = lb._safe_segment(str(project_code or ""), "DU_AN")
    rows: list[dict[str, Any]] = []
    try:
        lb.ensure_schema()
        with lb._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id,storage_path,size FROM qlda_local_files WHERE project_code=%s",
                    (safe_code,),
                )
                rows = [dict(x) for x in cur.fetchall()]
    except Exception as exc:
        raise RuntimeError(f"Không đọc được danh sách file VPS của nhà thầu: {exc}") from exc

    removed_files = 0
    removed_bytes = 0
    failures: list[str] = []
    root = lb.storage_root()
    for row in rows:
        try:
            path = lb._absolute_from_relative(str(row.get("storage_path") or ""))
            if path.exists() and path.is_file():
                size = int(path.stat().st_size)
                path.unlink()
                removed_files += 1
                removed_bytes += size
        except Exception as exc:
            failures.append(f"{row.get('id')}: {exc}")

    # Remove any files/directories that belong to the project but pre-date local
    # metadata registration. _relative_target() always stores them here.
    project_dir = (root / "projects" / safe_code).resolve()
    try:
        project_dir.relative_to(root)
        if project_dir.exists():
            shutil.rmtree(project_dir)
    except Exception as exc:
        failures.append(f"workspace folder: {exc}")

    if failures:
        raise RuntimeError(
            "Không xóa hết file vật lý trên VPS; dữ liệu DB chưa bị xóa. " + "; ".join(failures[:5])
        )

    metadata = 0
    try:
        with lb._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM qlda_local_files WHERE project_code=%s", (safe_code,))
                metadata = max(0, int(cur.rowcount or 0))
            conn.commit()
    except Exception as exc:
        raise RuntimeError(f"Đã xóa file vật lý nhưng không xóa được metadata file VPS: {exc}") from exc

    return {"files": removed_files, "bytes": removed_bytes, "metadata": metadata}


def delete_contractor_completely(db, contractor_id: int) -> dict[str, Any]:
    """Permanently delete one selected non-default contractor and all its data."""
    import contractor_workspace_v622 as cw

    cid = int(contractor_id)
    with db.connect() as connection:
        cw.ensure_schema_connection(connection)
        row = connection.execute(
            f"SELECT pc.*,p.code AS workspace_code,p.name AS workspace_name "
            f"FROM {cw.TABLE_NAME} pc JOIN projects p ON p.id=pc.workspace_project_id "
            "WHERE pc.id=? LIMIT 1",
            (cid,),
        ).fetchone()
        if not row:
            raise ValueError("Không tìm thấy nhà thầu được chọn.")
        info = _rowdict(row)

    workspace_id = int(info.get("workspace_project_id") or 0)
    master_id = int(info.get("master_project_id") or 0)
    if int(info.get("is_default") or 0) or workspace_id == master_id:
        raise ValueError(
            "Nhà thầu mặc định đang dùng chính dữ liệu gốc của dự án nên không thể xóa riêng. "
            "Hãy chọn một nhà thầu workspace khác để xóa."
        )
    if workspace_id <= 0:
        raise ValueError("Workspace nhà thầu không hợp lệ.")

    # Fail closed: physical VPS files must be removable before destructive DB purge.
    file_result = _purge_local_vps_files(str(info.get("workspace_code") or ""))
    db_result = _delete_all_workspace_rows(db, workspace_id, cid)

    return {
        "contractor": info,
        "workspace_project_id": workspace_id,
        "db_deleted": db_result,
        "files_deleted": file_result,
    }


def _render_admin_tools(st, db, master_id: int, selected_info: dict[str, Any]) -> None:
    import contractor_workspace_v622 as cw

    selected_id = int(selected_info.get("id") or 0)
    selected_code = str(selected_info.get("contractor_code") or "")
    selected_name = str(selected_info.get("contractor_name") or "")

    with st.sidebar.expander("⚙️ Quản lý nhà thầu · Admin", expanded=False):
        action = st.radio(
            "Thao tác",
            ["➕ Thêm mới", "✏️ Cập nhật", "🗑 Xóa"],
            horizontal=False,
            key=f"contractor_admin_action_{master_id}",
        )

        if action == "➕ Thêm mới":
            with st.form(f"sidebar_add_contractor_{master_id}", clear_on_submit=True):
                code = st.text_input("Mã nhà thầu *", placeholder="VD: REE")
                name = st.text_input("Tên nhà thầu *")
                contract_no = st.text_input("Số hợp đồng")
                package_name = st.text_input("Gói thầu")
                tax_code = st.text_input("Mã số thuế")
                contact_name = st.text_input("Người liên hệ")
                contact_phone = st.text_input("Điện thoại")
                contact_email = st.text_input("Email")
                submitted = st.form_submit_button(
                    "➕ Tạo nhà thầu",
                    type="primary",
                    use_container_width=True,
                )
            if submitted:
                try:
                    added = cw.add_contractor(
                        db,
                        master_id,
                        code,
                        name,
                        contract_no=contract_no,
                        package_name=package_name,
                        tax_code=tax_code,
                        contact_name=contact_name,
                        contact_phone=contact_phone,
                        contact_email=contact_email,
                    )
                    st.session_state[f"contractor_workspace_{master_id}"] = int(added["workspace_project_id"])
                    st.success(f"Đã thêm {added.get('contractor_name') or name}.")
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))

        elif action == "✏️ Cập nhật":
            st.caption(f"Đang cập nhật: {selected_code} - {selected_name}")
            rec = dict(selected_info)
            with st.form(f"sidebar_edit_contractor_{master_id}_{selected_id}"):
                code = st.text_input("Mã nhà thầu", value=selected_code)
                name = st.text_input("Tên nhà thầu", value=selected_name)
                contract_no = st.text_input("Số hợp đồng", value=str(rec.get("contract_no") or ""))
                package_name = st.text_input("Gói thầu", value=str(rec.get("package_name") or ""))
                tax_code = st.text_input("Mã số thuế", value=str(rec.get("tax_code") or ""))
                contact_name = st.text_input("Người liên hệ", value=str(rec.get("contact_name") or ""))
                contact_phone = st.text_input("Điện thoại", value=str(rec.get("contact_phone") or ""))
                contact_email = st.text_input("Email", value=str(rec.get("contact_email") or ""))
                statuses = ["Đang hoạt động", "Tạm dừng", "Đã kết thúc"]
                current_status = str(rec.get("status") or "Đang hoạt động")
                status = st.selectbox(
                    "Trạng thái",
                    statuses,
                    index=statuses.index(current_status) if current_status in statuses else 0,
                )
                save = st.form_submit_button(
                    "💾 Lưu cập nhật",
                    type="primary",
                    use_container_width=True,
                )
            if save:
                try:
                    cw.update_contractor(
                        db,
                        selected_id,
                        {
                            "contractor_code": code,
                            "contractor_name": name,
                            "contract_no": contract_no,
                            "package_name": package_name,
                            "tax_code": tax_code,
                            "contact_name": contact_name,
                            "contact_email": contact_email,
                            "contact_phone": contact_phone,
                            "status": status,
                        },
                    )
                    st.success("Đã cập nhật nhà thầu.")
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))

        else:
            st.caption(f"Nhà thầu được chọn: {selected_code} - {selected_name}")
            is_default = bool(int(selected_info.get("is_default") or 0))
            if is_default:
                st.info(
                    "Đây là nhà thầu mặc định đang chứa dữ liệu gốc của dự án nên không thể xóa riêng."
                )
                return

            stats = cw.contractor_scope_stats(db, int(selected_info.get("workspace_project_id") or 0))
            st.warning(
                "Xóa vĩnh viễn workspace này và toàn bộ dữ liệu thuộc nhà thầu: "
                f"BOQ {int(stats.get('boq_rows', 0) or 0)} dòng · "
                f"Claim {int(stats.get('claim_count', 0) or 0)} · "
                f"Hồ sơ {int(stats.get('documents', 0) or 0)} · "
                f"Bản vẽ {int(stats.get('drawings', 0) or 0)} · "
                f"Tiến độ {int(stats.get('tasks', 0) or 0)}."
            )
            confirm = st.checkbox(
                f"Tôi xác nhận xóa {selected_code} và TẤT CẢ dữ liệu của nhà thầu này.",
                key=f"confirm_sidebar_delete_contractor_{master_id}_{selected_id}",
            )
            typed = st.text_input(
                f"Nhập lại mã {selected_code} để xác nhận",
                key=f"confirm_sidebar_delete_code_{master_id}_{selected_id}",
            )
            allowed = bool(confirm and typed.strip().upper() == selected_code.strip().upper())
            if st.button(
                "🗑 XÓA NHÀ THẦU & TOÀN BỘ DỮ LIỆU",
                type="primary",
                disabled=not allowed,
                use_container_width=True,
                key=f"delete_sidebar_contractor_{master_id}_{selected_id}",
            ):
                try:
                    result = delete_contractor_completely(db, selected_id)
                    st.session_state.pop(f"contractor_workspace_{master_id}", None)
                    st.session_state.pop(f"contractor_workspace_select_{master_id}", None)
                    files = result.get("files_deleted") or {}
                    st.success(
                        "Đã xóa nhà thầu và toàn bộ dữ liệu workspace. "
                        f"File VPS đã xóa: {int(files.get('files', 0) or 0)}."
                    )
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))


def install_contractor_sidebar_admin() -> None:
    import contractor_workspace_v622 as cw

    if getattr(cw, "_qlda_contractor_sidebar_admin_installed", False):
        return

    def render_contractor_selector(
        db,
        master_project_id: int,
        *,
        can_admin: bool = False,
        current_user: str = "",
    ) -> tuple[int, dict[str, Any]]:
        import streamlit as st

        master_id = int(master_project_id)
        rows = cw.list_contractors(db, master_id, active_only=True)
        if not rows:
            return master_id, {}

        by_workspace = {int(row["workspace_project_id"]): dict(row) for row in rows}
        ids = list(by_workspace)
        state_key = f"contractor_workspace_{master_id}"
        current = int(st.session_state.get(state_key) or ids[0])
        if current not in by_workspace:
            current = ids[0]
        index = ids.index(current)

        selected = st.sidebar.selectbox(
            "Nhà thầu đang làm việc",
            ids,
            index=index,
            format_func=lambda wid: (
                f"{by_workspace[wid].get('contractor_code','')} - "
                f"{by_workspace[wid].get('contractor_name','')}"
            ),
            key=f"contractor_workspace_select_{master_id}",
        )
        st.session_state[state_key] = int(selected)
        info = dict(by_workspace[int(selected)])
        st.sidebar.caption("🤖 Trợ lý AI/Ban điều hành mặc định quét TOÀN BỘ nhà thầu của dự án.")

        # Admin controls live immediately below the active contractor selector.
        # Non-admin users do not see Add / Update / Delete controls at all.
        if bool(can_admin):
            _render_admin_tools(st, db, master_id, info)

        return int(selected), info

    cw.render_contractor_selector = render_contractor_selector
    cw.delete_contractor_completely = delete_contractor_completely
    cw._qlda_contractor_sidebar_admin_installed = True
    cw._qlda_contractor_sidebar_admin_marker = PATCH_MARKER
