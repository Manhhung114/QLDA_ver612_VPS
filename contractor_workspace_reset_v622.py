from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any


PATCH_MARKER = "V6.22 DEFAULT CONTRACTOR WORKSPACE RESET V2"
RESET_PHRASE = "RESET TOAN BO DU LIEU"

# Project-scoped system/access metadata must survive a business-data reset.
_PROTECTED_TABLE_NAMES = {
    "projects",
    "project_contractors",
    "project_users",
    "project_user_access",
    "user_project_access",
    "project_permissions",
    "project_roles",
    "project_settings",
    "project_config",
    "project_configs",
    "admin_workspace_reset_log",
}
_PROTECTED_NAME_PARTS = (
    "permission",
    "access",
    "session",
    "auth",
    "login",
    "setting",
    "config",
)


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


def _is_protected_project_table(table: str) -> bool:
    name = str(table or "").strip().lower()
    if not name:
        return True
    if name in _PROTECTED_TABLE_NAMES:
        return True
    return any(part in name for part in _PROTECTED_NAME_PARTS)


def _workspace_scope_tables(connection) -> list[tuple[str, str]]:
    """Discover business tables scoped by project_id or workspace_project_id.

    New modules can be reset automatically without maintaining a hard-coded list.
    workspace_project_id is preferred where both keys exist, which is important
    for Work Assignment V1. Access/config tables are filtered separately.
    """
    found: dict[str, set[str]] = {}
    try:
        rows = connection.execute(
            """SELECT table_name,column_name
               FROM information_schema.columns
               WHERE table_schema=current_schema()
                 AND column_name IN ('project_id','workspace_project_id')
               ORDER BY table_name,column_name"""
        ).fetchall()
        for raw in rows:
            try:
                table, column = str(raw[0]), str(raw[1])
            except Exception:
                data = _rowdict(raw)
                table = str(data.get("table_name") or "")
                column = str(data.get("column_name") or "")
            if table and column:
                found.setdefault(table, set()).add(column)
    except Exception:
        pass

    if not found:
        try:
            tables = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            ).fetchall()
            for raw in tables:
                table = str(raw[0])
                cols = connection.execute(f"PRAGMA table_info({table})").fetchall()
                names = set()
                for col in cols:
                    try:
                        names.add(str(col[1]))
                    except Exception:
                        names.add(str(_rowdict(col).get("name") or ""))
                scope_cols = names.intersection({"project_id", "workspace_project_id"})
                if scope_cols:
                    found[table] = scope_cols
        except Exception:
            pass

    out: list[tuple[str, str]] = []
    for table in sorted(found):
        if _is_protected_project_table(table):
            continue
        cols = found[table]
        key = "workspace_project_id" if "workspace_project_id" in cols else "project_id"
        out.append((table, key))
    return out


def _ensure_reset_audit_table(connection) -> None:
    # TEXT primary key is portable across both the local SQLite test backend and
    # the PostgreSQL compatibility backend used on VPS.
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS admin_workspace_reset_log(
            reset_id TEXT PRIMARY KEY,
            master_project_id INTEGER NOT NULL,
            workspace_project_id INTEGER NOT NULL,
            contractor_id INTEGER NOT NULL,
            contractor_code TEXT DEFAULT '',
            contractor_name TEXT DEFAULT '',
            actor TEXT DEFAULT '',
            deleted_json TEXT DEFAULT '{}',
            file_json TEXT DEFAULT '{}',
            created_at TEXT NOT NULL
        )
        """
    )


def _actor_from_streamlit(st) -> str:
    for key in ("current_user", "user_email", "email", "username", "user"):
        value = st.session_state.get(key)
        if isinstance(value, dict):
            for subkey in ("email", "username", "name"):
                text = str(value.get(subkey) or "").strip()
                if text:
                    return text
        text = str(value or "").strip()
        if text:
            return text
    return "Admin"


def reset_default_workspace(db, contractor_id: int, *, actor: str = "Admin") -> dict[str, Any]:
    """Reset all business data of the default contractor workspace.

    The master project row and project_contractors mapping are intentionally kept.
    User/auth/access/config tables are protected even if they are project-scoped.
    Physical VPS files belonging to this workspace are removed as well.
    """
    import contractor_workspace_v622 as cw
    import contractor_sidebar_admin_v622 as sidebar_admin

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
    is_default = bool(int(info.get("is_default") or 0))
    if not is_default or workspace_id <= 0 or workspace_id != master_id:
        raise ValueError("Reset này chỉ áp dụng cho workspace mặc định của dự án.")

    # Files are part of the business workspace and are reset together with DB data.
    file_result = sidebar_admin._purge_local_vps_files(str(info.get("workspace_code") or ""))

    deleted: dict[str, int] = {}
    with db.connect() as connection:
        scope_tables = _workspace_scope_tables(connection)
        for table, key in scope_tables:
            try:
                cur = connection.execute(f"DELETE FROM {table} WHERE {key}=?", (workspace_id,))
                deleted[table] = max(0, int(getattr(cur, "rowcount", 0) or 0))
            except Exception as exc:
                raise RuntimeError(f"Không thể reset dữ liệu bảng {table}: {exc}") from exc

        # Forget imported schedule source while preserving project code/name/date,
        # manager, note and the project/contractor identity itself.
        try:
            connection.execute(
                "UPDATE projects SET source_mpp_path='',last_sync='' WHERE id=?",
                (workspace_id,),
            )
        except Exception:
            pass

        _ensure_reset_audit_table(connection)
        connection.execute(
            """INSERT INTO admin_workspace_reset_log(
                   reset_id,master_project_id,workspace_project_id,contractor_id,
                   contractor_code,contractor_name,actor,deleted_json,file_json,created_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                uuid.uuid4().hex,
                master_id,
                workspace_id,
                cid,
                str(info.get("contractor_code") or ""),
                str(info.get("contractor_name") or ""),
                str(actor or "Admin"),
                json.dumps(deleted, ensure_ascii=False, sort_keys=True),
                json.dumps(file_result, ensure_ascii=False, sort_keys=True),
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )

    return {
        "contractor": info,
        "workspace_project_id": workspace_id,
        "db_deleted": deleted,
        "files_deleted": file_result,
    }


def _render_admin_tools_with_reset(st, db, master_id: int, selected_info: dict[str, Any]) -> None:
    import contractor_workspace_v622 as cw
    import contractor_sidebar_admin_v622 as sidebar_admin

    selected_id = int(selected_info.get("id") or 0)
    selected_code = str(selected_info.get("contractor_code") or "")
    selected_name = str(selected_info.get("contractor_name") or "")
    workspace_id = int(selected_info.get("workspace_project_id") or 0)
    is_default = bool(int(selected_info.get("is_default") or 0)) and workspace_id == int(master_id)

    with st.sidebar.expander("⚙️ Quản lý nhà thầu · Admin", expanded=False):
        if is_default:
            st.markdown("**🔒 Workspace mặc định · Admin quản lý**")
            actions = ["➕ Thêm mới", "✏️ Cập nhật", "♻️ Reset dữ liệu"]
        else:
            actions = ["➕ Thêm mới", "✏️ Cập nhật", "🗑 Xóa"]

        action = st.radio(
            "Thao tác",
            actions,
            horizontal=False,
            key=f"contractor_admin_action_{master_id}_{selected_id}",
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
                submitted = st.form_submit_button("➕ Tạo nhà thầu", type="primary", use_container_width=True)
            if submitted:
                try:
                    added = cw.add_contractor(
                        db, master_id, code, name,
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
            return

        if action == "✏️ Cập nhật":
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
                save = st.form_submit_button("💾 Lưu cập nhật", type="primary", use_container_width=True)
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
            return

        if action == "♻️ Reset dữ liệu":
            stats = cw.contractor_scope_stats(db, workspace_id)
            st.warning(
                "Reset sẽ xóa toàn bộ dữ liệu nghiệp vụ và file của workspace này nhưng GIỮ dự án, "
                "workspace mặc định, nhà thầu, tài khoản/phân quyền và cấu hình hệ thống."
            )
            st.markdown(
                f"BOQ **{int(stats.get('boq_rows', 0) or 0):,}** dòng · "
                f"Claim **{int(stats.get('claim_count', 0) or 0):,}** · "
                f"Hồ sơ **{int(stats.get('documents', 0) or 0):,}** · "
                f"Bản vẽ **{int(stats.get('drawings', 0) or 0):,}** · "
                f"Tiến độ **{int(stats.get('tasks', 0) or 0):,}**"
            )
            confirm = st.checkbox(
                "Tôi hiểu toàn bộ dữ liệu nghiệp vụ của workspace mặc định sẽ bị xóa.",
                key=f"confirm_reset_default_{master_id}_{selected_id}",
            )
            typed_code = st.text_input(
                f"Nhập lại mã nhà thầu: {selected_code}",
                key=f"confirm_reset_default_code_{master_id}_{selected_id}",
            )
            typed_phrase = st.text_input(
                f"Nhập chính xác: {RESET_PHRASE}",
                key=f"confirm_reset_default_phrase_{master_id}_{selected_id}",
            )
            allowed = bool(
                confirm
                and typed_code.strip().upper() == selected_code.strip().upper()
                and typed_phrase.strip().upper() == RESET_PHRASE
            )
            if st.button(
                "♻️ RESET TOÀN BỘ DỮ LIỆU",
                type="primary",
                disabled=not allowed,
                use_container_width=True,
                key=f"reset_default_workspace_{master_id}_{selected_id}",
            ):
                try:
                    result = reset_default_workspace(db, selected_id, actor=_actor_from_streamlit(st))
                    files = result.get("files_deleted") or {}
                    deleted = result.get("db_deleted") or {}
                    total_rows = sum(int(v or 0) for v in deleted.values())
                    st.success(
                        f"Đã reset workspace mặc định. Đã xóa {total_rows:,} dòng nghiệp vụ và "
                        f"{int(files.get('files', 0) or 0):,} file VPS. Dự án và nhà thầu vẫn được giữ nguyên."
                    )
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))
            return

        # Non-default contractor only: preserve the existing destructive delete.
        stats = cw.contractor_scope_stats(db, workspace_id)
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
                result = sidebar_admin.delete_contractor_completely(db, selected_id)
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


def install_contractor_workspace_reset() -> None:
    """Install Admin-only reset UI for the default contractor workspace."""
    import contractor_sidebar_admin_v622 as sidebar_admin
    import contractor_workspace_v622 as cw

    if getattr(cw, "_qlda_default_workspace_reset_installed", False):
        return

    sidebar_admin._render_admin_tools = _render_admin_tools_with_reset
    cw.reset_default_workspace = reset_default_workspace
    cw._qlda_default_workspace_reset_installed = True
    cw._qlda_default_workspace_reset_marker = PATCH_MARKER
