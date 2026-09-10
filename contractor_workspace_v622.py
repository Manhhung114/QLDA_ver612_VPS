from __future__ import annotations

import re
from datetime import datetime
from typing import Any


PATCH_MARKER = "V6.22 CONTRACTOR WORKSPACE V1"
TABLE_NAME = "project_contractors"
DEFAULT_CODE = "NT-01"
DEFAULT_NAME = "Nhà thầu hiện tại"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


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


def _slug(value: Any) -> str:
    text = str(value or "").upper().strip()
    text = re.sub(r"[^A-Z0-9]+", "-", text).strip("-")
    return text[:30] or "NT"


def _table_exists(connection, table: str) -> bool:
    try:
        connection.execute(f"SELECT 1 FROM {table} LIMIT 1")
        return True
    except Exception:
        return False


def ensure_schema_connection(connection) -> None:
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE_NAME}(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            master_project_id INTEGER NOT NULL,
            workspace_project_id INTEGER NOT NULL UNIQUE,
            contractor_code TEXT NOT NULL,
            contractor_name TEXT NOT NULL,
            contract_no TEXT DEFAULT '',
            package_name TEXT DEFAULT '',
            tax_code TEXT DEFAULT '',
            contact_name TEXT DEFAULT '',
            contact_email TEXT DEFAULT '',
            contact_phone TEXT DEFAULT '',
            status TEXT DEFAULT 'Đang hoạt động',
            is_default INTEGER DEFAULT 0,
            created_at TEXT DEFAULT '',
            updated_at TEXT DEFAULT '',
            FOREIGN KEY(master_project_id) REFERENCES projects(id) ON DELETE CASCADE,
            FOREIGN KEY(workspace_project_id) REFERENCES projects(id) ON DELETE CASCADE,
            UNIQUE(master_project_id, contractor_code)
        )
        """
    )
    connection.execute(
        f"CREATE INDEX IF NOT EXISTS idx_project_contractors_master ON {TABLE_NAME}(master_project_id,status,id)"
    )
    connection.execute(
        f"CREATE INDEX IF NOT EXISTS idx_project_contractors_workspace ON {TABLE_NAME}(workspace_project_id)"
    )


def ensure_schema(db) -> None:
    with db.connect() as connection:
        ensure_schema_connection(connection)


def resolve_master_project_id_connection(connection, project_id: int) -> int:
    pid = int(project_id)
    try:
        ensure_schema_connection(connection)
        row = connection.execute(
            f"SELECT master_project_id FROM {TABLE_NAME} WHERE workspace_project_id=? LIMIT 1",
            (pid,),
        ).fetchone()
        if row:
            data = _rowdict(row)
            return int(data.get("master_project_id") if data else row[0])
    except Exception:
        pass
    return pid


def contractor_rows_connection(connection, project_id: int, *, active_only: bool = False) -> list[dict[str, Any]]:
    ensure_schema_connection(connection)
    master_id = resolve_master_project_id_connection(connection, int(project_id))
    sql = (
        f"SELECT pc.*,p.code AS workspace_code,p.name AS workspace_name "
        f"FROM {TABLE_NAME} pc JOIN projects p ON p.id=pc.workspace_project_id "
        "WHERE pc.master_project_id=?"
    )
    params: list[Any] = [master_id]
    if active_only:
        sql += " AND pc.status='Đang hoạt động'"
    sql += " ORDER BY pc.is_default DESC,pc.id"
    return [_rowdict(row) for row in connection.execute(sql, params).fetchall()]


def workspace_ids_connection(connection, project_id: int, *, active_only: bool = False) -> list[int]:
    rows = contractor_rows_connection(connection, int(project_id), active_only=active_only)
    return [int(row.get("workspace_project_id") or 0) for row in rows if int(row.get("workspace_project_id") or 0) > 0]


def _first_nonempty(connection, queries: list[tuple[str, tuple[Any, ...]]]) -> str:
    for sql, params in queries:
        try:
            row = connection.execute(sql, params).fetchone()
            if not row:
                continue
            try:
                value = row[0]
            except Exception:
                data = _rowdict(row)
                value = next(iter(data.values())) if data else ""
            text = str(value or "").strip()
            if text:
                return text
        except Exception:
            continue
    return ""


def _infer_default_metadata(connection, master_project_id: int) -> dict[str, str]:
    pid = int(master_project_id)
    contractor = _first_nonempty(connection, [
        ("SELECT contractor FROM payment_claims WHERE project_id=? AND TRIM(COALESCE(contractor,''))<>'' ORDER BY updated_at DESC LIMIT 1", (pid,)),
        ("SELECT contractor FROM cost_budgets WHERE project_id=? AND TRIM(COALESCE(contractor,''))<>'' ORDER BY id DESC LIMIT 1", (pid,)),
        ("SELECT contractor FROM documents WHERE project_id=? AND TRIM(COALESCE(contractor,''))<>'' ORDER BY id DESC LIMIT 1", (pid,)),
    ]) or DEFAULT_NAME
    contract_no = _first_nonempty(connection, [
        ("SELECT contract_no FROM payment_claims WHERE project_id=? AND TRIM(COALESCE(contract_no,''))<>'' ORDER BY updated_at DESC LIMIT 1", (pid,)),
    ])
    package_name = _first_nonempty(connection, [
        ("SELECT package_name FROM payment_claims WHERE project_id=? AND TRIM(COALESCE(package_name,''))<>'' ORDER BY updated_at DESC LIMIT 1", (pid,)),
    ])
    return {"contractor_name": contractor, "contract_no": contract_no, "package_name": package_name}


def ensure_default_contractor(db, project_id: int) -> dict[str, Any]:
    pid = int(project_id)
    with db.connect() as connection:
        ensure_schema_connection(connection)
        master_id = resolve_master_project_id_connection(connection, pid)
        row = connection.execute(
            f"SELECT * FROM {TABLE_NAME} WHERE master_project_id=? AND workspace_project_id=? LIMIT 1",
            (master_id, master_id),
        ).fetchone()
        if row:
            return _rowdict(row)
        any_row = connection.execute(
            f"SELECT * FROM {TABLE_NAME} WHERE master_project_id=? ORDER BY is_default DESC,id LIMIT 1",
            (master_id,),
        ).fetchone()
        if any_row:
            return _rowdict(any_row)
        project = connection.execute("SELECT id FROM projects WHERE id=?", (master_id,)).fetchone()
        if not project:
            raise ValueError("Không tìm thấy dự án để tạo workspace nhà thầu.")
        meta = _infer_default_metadata(connection, master_id)
        stamp = _now()
        connection.execute(
            f"""INSERT INTO {TABLE_NAME}(
                   master_project_id,workspace_project_id,contractor_code,contractor_name,
                   contract_no,package_name,status,is_default,created_at,updated_at
               ) VALUES(?,?,?,?,?,?,'Đang hoạt động',1,?,?)""",
            (
                master_id, master_id, DEFAULT_CODE, meta["contractor_name"],
                meta["contract_no"], meta["package_name"], stamp, stamp,
            ),
        )
        row = connection.execute(
            f"SELECT * FROM {TABLE_NAME} WHERE master_project_id=? AND workspace_project_id=?",
            (master_id, master_id),
        ).fetchone()
        return _rowdict(row)


def list_contractors(db, project_id: int, *, active_only: bool = False) -> list[dict[str, Any]]:
    default = ensure_default_contractor(db, int(project_id))
    master_id = int(default.get("master_project_id") or project_id)
    with db.connect() as connection:
        return contractor_rows_connection(connection, master_id, active_only=active_only)


def _unique_workspace_code(connection, master_code: str, contractor_code: str) -> str:
    base = f"{_slug(master_code)}__NT__{_slug(contractor_code)}"[:72]
    candidate = base
    index = 2
    while connection.execute("SELECT 1 FROM projects WHERE code=?", (candidate,)).fetchone():
        suffix = f"-{index}"
        candidate = (base[: max(1, 72 - len(suffix))] + suffix)
        index += 1
    return candidate


def add_contractor(
    db,
    master_project_id: int,
    contractor_code: str,
    contractor_name: str,
    *,
    contract_no: str = "",
    package_name: str = "",
    tax_code: str = "",
    contact_name: str = "",
    contact_email: str = "",
    contact_phone: str = "",
) -> dict[str, Any]:
    default = ensure_default_contractor(db, int(master_project_id))
    master_id = int(default.get("master_project_id") or master_project_id)
    code = str(contractor_code or "").strip().upper()
    name = str(contractor_name or "").strip()
    if not code or not name:
        raise ValueError("Cần nhập Mã nhà thầu và Tên nhà thầu.")
    with db.connect() as connection:
        ensure_schema_connection(connection)
        duplicate = connection.execute(
            f"SELECT 1 FROM {TABLE_NAME} WHERE master_project_id=? AND UPPER(contractor_code)=UPPER(?)",
            (master_id, code),
        ).fetchone()
        if duplicate:
            raise ValueError(f"Mã nhà thầu {code} đã tồn tại trong dự án.")
        master = connection.execute("SELECT * FROM projects WHERE id=?", (master_id,)).fetchone()
        if not master:
            raise ValueError("Không tìm thấy dự án gốc.")
        m = _rowdict(master)
        workspace_code = _unique_workspace_code(connection, str(m.get("code") or "DA"), code)
        workspace_name = f"{m.get('name') or 'Dự án'} · {name}"
        note = f"[QLDA_CONTRACTOR_WORKSPACE] master={master_id}; contractor={code}"
        cur = connection.execute(
            """INSERT INTO projects(code,name,start_date,end_date,manager,note,source_mpp_path,last_sync)
               VALUES(?,?,?,?,?,?,?,?)""",
            (
                workspace_code, workspace_name, str(m.get("start_date") or ""), str(m.get("end_date") or ""),
                str(m.get("manager") or ""), note, "", "",
            ),
        )
        workspace_id = int(cur.lastrowid)
        stamp = _now()
        connection.execute(
            f"""INSERT INTO {TABLE_NAME}(
                   master_project_id,workspace_project_id,contractor_code,contractor_name,contract_no,package_name,
                   tax_code,contact_name,contact_email,contact_phone,status,is_default,created_at,updated_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,'Đang hoạt động',0,?,?)""",
            (
                master_id, workspace_id, code, name, str(contract_no or "").strip(), str(package_name or "").strip(),
                str(tax_code or "").strip(), str(contact_name or "").strip(), str(contact_email or "").strip(),
                str(contact_phone or "").strip(), stamp, stamp,
            ),
        )
        row = connection.execute(
            f"SELECT * FROM {TABLE_NAME} WHERE workspace_project_id=?", (workspace_id,)
        ).fetchone()
        return _rowdict(row)


def update_contractor(db, contractor_id: int, data: dict[str, Any]) -> None:
    fields = (
        "contractor_code", "contractor_name", "contract_no", "package_name", "tax_code",
        "contact_name", "contact_email", "contact_phone", "status",
    )
    values = [str(data.get(name) or "").strip() for name in fields]
    if not values[0] or not values[1]:
        raise ValueError("Mã nhà thầu và Tên nhà thầu không được để trống.")
    with db.connect() as connection:
        ensure_schema_connection(connection)
        row = connection.execute(f"SELECT * FROM {TABLE_NAME} WHERE id=?", (int(contractor_id),)).fetchone()
        if not row:
            raise ValueError("Không tìm thấy nhà thầu.")
        info = _rowdict(row)
        dup = connection.execute(
            f"SELECT 1 FROM {TABLE_NAME} WHERE master_project_id=? AND UPPER(contractor_code)=UPPER(?) AND id<>?",
            (int(info.get("master_project_id") or 0), values[0], int(contractor_id)),
        ).fetchone()
        if dup:
            raise ValueError("Mã nhà thầu đã tồn tại trong dự án.")
        connection.execute(
            f"UPDATE {TABLE_NAME} SET " + ",".join(f"{field}=?" for field in fields) + ",updated_at=? WHERE id=?",
            values + [_now(), int(contractor_id)],
        )


def delete_contractor(db, contractor_id: int) -> None:
    cid = int(contractor_id)
    with db.connect() as connection:
        ensure_schema_connection(connection)
        row = connection.execute(f"SELECT * FROM {TABLE_NAME} WHERE id=?", (cid,)).fetchone()
        if not row:
            return
        info = _rowdict(row)
        if int(info.get("is_default") or 0) or int(info.get("workspace_project_id") or 0) == int(info.get("master_project_id") or 0):
            raise ValueError("Không thể xóa nhà thầu mặc định đang chứa dữ liệu gốc của dự án.")
        workspace_id = int(info.get("workspace_project_id") or 0)
    # Use the normal project delete path so all child business tables cascade.
    db.delete_project(workspace_id)


def contractor_scope_stats(db, workspace_project_id: int) -> dict[str, Any]:
    pid = int(workspace_project_id)
    out: dict[str, Any] = {"workspace_project_id": pid}
    with db.connect() as connection:
        for key, table in (
            ("tasks", "tasks"), ("documents", "documents"), ("drawings", "drawings"),
            ("boq_rows", "cost_budgets"), ("vo_rows", "cost_variations"),
        ):
            try:
                row = connection.execute(f"SELECT COUNT(*) FROM {table} WHERE project_id=?", (pid,)).fetchone()
                out[key] = int(row[0] if row else 0)
            except Exception:
                out[key] = 0
        try:
            row = connection.execute("SELECT COALESCE(SUM(budget_total),0) FROM cost_budgets WHERE project_id=?", (pid,)).fetchone()
            out["boq_total"] = float(row[0] if row else 0)
        except Exception:
            out["boq_total"] = 0.0
        try:
            rows = connection.execute("SELECT claim_no,claim_code FROM payment_claims WHERE project_id=?", (pid,)).fetchall()
            out["claim_count"] = len(rows)
            best = (-1, "")
            for raw in rows:
                data = _rowdict(raw)
                text = str(data.get("claim_no") or data.get("claim_code") or "")
                match = re.search(r"0*([0-9]+)", text)
                if match and int(match.group(1)) > best[0]:
                    best = (int(match.group(1)), str(data.get("claim_code") or f"IPC-{int(match.group(1)):02d}"))
            out["latest_ipc"] = best[1]
        except Exception:
            out["claim_count"] = 0
            out["latest_ipc"] = ""
    return out


def render_contractor_selector(db, master_project_id: int, *, can_admin: bool = False, current_user: str = "") -> tuple[int, dict[str, Any]]:
    import streamlit as st

    rows = list_contractors(db, int(master_project_id), active_only=True)
    if not rows:
        return int(master_project_id), {}
    by_workspace = {int(row["workspace_project_id"]): row for row in rows}
    ids = list(by_workspace)
    state_key = f"contractor_workspace_{int(master_project_id)}"
    current = int(st.session_state.get(state_key) or ids[0])
    if current not in by_workspace:
        current = ids[0]
    index = ids.index(current)
    selected = st.sidebar.selectbox(
        "Nhà thầu đang làm việc",
        ids,
        index=index,
        format_func=lambda wid: f"{by_workspace[wid].get('contractor_code','')} - {by_workspace[wid].get('contractor_name','')}",
        key=f"contractor_workspace_select_{int(master_project_id)}",
    )
    st.session_state[state_key] = int(selected)
    info = dict(by_workspace[int(selected)])
    st.sidebar.caption("🤖 Trợ lý AI/Ban điều hành mặc định quét TOÀN BỘ nhà thầu của dự án.")
    return int(selected), info


def render_contractor_management(db, master_project_id: int, *, can_admin: bool = False) -> None:
    import pandas as pd
    import streamlit as st

    master_id = int(master_project_id)
    rows = list_contractors(db, master_id, active_only=False)
    st.subheader("🏢 Quản lý nhà thầu trong dự án")
    st.caption("Mỗi nhà thầu có workspace riêng với đầy đủ Tiến độ, Hồ sơ, Bản vẽ, BOQ, Claim/IPC, VO, Vật tư và Nhật ký. AI quản lý dự án quét tất cả workspace.")

    data = []
    for row in rows:
        stats = contractor_scope_stats(db, int(row["workspace_project_id"]))
        data.append({
            "Mã": row.get("contractor_code", ""),
            "Nhà thầu": row.get("contractor_name", ""),
            "Số HĐ": row.get("contract_no", ""),
            "Gói thầu": row.get("package_name", ""),
            "Trạng thái": row.get("status", ""),
            "BOQ (dòng)": stats.get("boq_rows", 0),
            "BOQ (VND)": stats.get("boq_total", 0),
            "Claim": stats.get("claim_count", 0),
            "IPC lớn nhất": stats.get("latest_ipc", ""),
            "Hồ sơ": stats.get("documents", 0),
            "Bản vẽ": stats.get("drawings", 0),
            "Tiến độ": stats.get("tasks", 0),
            "Mặc định": "✓" if int(row.get("is_default") or 0) else "",
        })
    if data:
        frame = pd.DataFrame(data)
        if "BOQ (VND)" in frame.columns:
            frame["BOQ (VND)"] = frame["BOQ (VND)"].map(lambda v: f"{float(v or 0):,.0f}")
        st.dataframe(frame, hide_index=True, width="stretch")

    with st.expander("➕ Thêm nhà thầu", expanded=False):
        with st.form(f"add_contractor_{master_id}", clear_on_submit=True):
            a, b = st.columns([1, 2])
            code = a.text_input("Mã nhà thầu *", placeholder="VD: REE")
            name = b.text_input("Tên nhà thầu *")
            a, b = st.columns(2)
            contract_no = a.text_input("Số hợp đồng")
            package_name = b.text_input("Gói thầu")
            a, b, c = st.columns(3)
            tax_code = a.text_input("Mã số thuế")
            contact_name = b.text_input("Người liên hệ")
            contact_phone = c.text_input("Điện thoại")
            contact_email = st.text_input("Email")
            submitted = st.form_submit_button("Tạo workspace nhà thầu", type="primary", disabled=not can_admin, width="stretch")
        if submitted:
            try:
                added = add_contractor(
                    db, master_id, code, name, contract_no=contract_no, package_name=package_name,
                    tax_code=tax_code, contact_name=contact_name, contact_phone=contact_phone, contact_email=contact_email,
                )
                st.success(f"Đã tạo workspace cho {added.get('contractor_name') or name}.")
                st.rerun()
            except Exception as exc:
                st.error(str(exc))

    if not rows:
        return
    ids = [int(row["id"]) for row in rows]
    by_id = {int(row["id"]): row for row in rows}
    selected_id = st.selectbox(
        "Chọn nhà thầu để cập nhật",
        ids,
        format_func=lambda cid: f"{by_id[cid].get('contractor_code','')} - {by_id[cid].get('contractor_name','')}",
        key=f"edit_contractor_{master_id}",
    )
    rec = by_id[int(selected_id)]
    with st.form(f"edit_contractor_form_{master_id}_{selected_id}"):
        a, b = st.columns([1, 2])
        code = a.text_input("Mã nhà thầu", value=str(rec.get("contractor_code") or ""))
        name = b.text_input("Tên nhà thầu", value=str(rec.get("contractor_name") or ""))
        a, b = st.columns(2)
        contract_no = a.text_input("Số hợp đồng", value=str(rec.get("contract_no") or ""))
        package_name = b.text_input("Gói thầu", value=str(rec.get("package_name") or ""))
        a, b, c = st.columns(3)
        tax_code = a.text_input("Mã số thuế", value=str(rec.get("tax_code") or ""))
        contact_name = b.text_input("Người liên hệ", value=str(rec.get("contact_name") or ""))
        contact_phone = c.text_input("Điện thoại", value=str(rec.get("contact_phone") or ""))
        contact_email = st.text_input("Email", value=str(rec.get("contact_email") or ""))
        statuses = ["Đang hoạt động", "Tạm dừng", "Đã kết thúc"]
        current_status = str(rec.get("status") or "Đang hoạt động")
        status = st.selectbox("Trạng thái", statuses, index=statuses.index(current_status) if current_status in statuses else 0)
        save = st.form_submit_button("💾 Lưu thông tin nhà thầu", type="primary", disabled=not can_admin, width="stretch")
    if save:
        try:
            update_contractor(db, int(selected_id), {
                "contractor_code": code, "contractor_name": name, "contract_no": contract_no,
                "package_name": package_name, "tax_code": tax_code, "contact_name": contact_name,
                "contact_email": contact_email, "contact_phone": contact_phone, "status": status,
            })
            st.success("Đã cập nhật nhà thầu.")
            st.rerun()
        except Exception as exc:
            st.error(str(exc))

    if not int(rec.get("is_default") or 0):
        confirm = st.checkbox(
            f"Xác nhận xóa workspace {rec.get('contractor_code','')}; toàn bộ dữ liệu của riêng nhà thầu này sẽ bị xóa.",
            key=f"confirm_delete_contractor_{master_id}_{selected_id}",
        )
        if st.button("🗑 Xóa nhà thầu", disabled=(not can_admin or not confirm), key=f"delete_contractor_{master_id}_{selected_id}"):
            try:
                delete_contractor(db, int(selected_id))
                st.success("Đã xóa nhà thầu và workspace tương ứng.")
                st.rerun()
            except Exception as exc:
                st.error(str(exc))
    else:
        st.info("Nhà thầu mặc định đang chứa dữ liệu lịch sử của dự án nên không thể xóa; có thể đổi tên/mã để phản ánh đúng nhà thầu hiện hữu.")


def install_contractor_workspace() -> None:
    """Install a backward-compatible contractor layer without rewriting business tables.

    Each contractor workspace is an ordinary hidden `projects` row, so every existing
    project-scoped feature works unchanged. The original project remains the default
    contractor workspace; therefore legacy data is never moved, copied, or deleted.
    """
    import cloud_db

    base = getattr(cloud_db, "SQLiteCloudDatabase", None) or cloud_db.CloudDatabase
    if getattr(base, "_qlda_contractor_workspace_installed", False):
        return

    original_create_tables = base.create_tables
    original_migrate = base.migrate
    original_projects = base.projects
    original_delete_project = base.delete_project

    def create_tables_with_contractors(self):
        original_create_tables(self)
        try:
            ensure_schema(self)
        except Exception:
            # First PostgreSQL bootstrap may still be translating the parent schema;
            # migrate()/first selector will retry deterministically.
            pass

    def migrate_with_contractors(self):
        original_migrate(self)
        ensure_schema(self)

    def root_projects_only(self):
        try:
            with self.connect() as connection:
                ensure_schema_connection(connection)
                return connection.execute(
                    f"""SELECT p.* FROM projects p
                        WHERE NOT EXISTS(
                            SELECT 1 FROM {TABLE_NAME} pc
                            WHERE pc.workspace_project_id=p.id
                              AND pc.master_project_id<>pc.workspace_project_id
                        )
                        ORDER BY p.id DESC"""
                ).fetchall()
        except Exception:
            return original_projects(self)

    def delete_project_tree(self, project_id: int):
        pid = int(project_id)
        children: list[int] = []
        try:
            with self.connect() as connection:
                ensure_schema_connection(connection)
                master_id = resolve_master_project_id_connection(connection, pid)
                if master_id == pid:
                    rows = connection.execute(
                        f"SELECT workspace_project_id FROM {TABLE_NAME} WHERE master_project_id=? AND workspace_project_id<>?",
                        (pid, pid),
                    ).fetchall()
                    children = [int(row[0]) for row in rows]
        except Exception:
            children = []
        for child_id in children:
            original_delete_project(self, child_id)
        original_delete_project(self, pid)

    base.create_tables = create_tables_with_contractors
    base.migrate = migrate_with_contractors
    base.projects = root_projects_only
    base.delete_project = delete_project_tree
    base._qlda_contractor_workspace_installed = True

    # Keep portable PostgreSQL backup/restore aware of the new mapping table.
    try:
        import postgres_backend_v622 as pg
        if TABLE_NAME not in pg.TABLE_ORDER:
            order = list(pg.TABLE_ORDER)
            insert_at = order.index("projects") + 1 if "projects" in order else 0
            order.insert(insert_at, TABLE_NAME)
            pg.TABLE_ORDER = tuple(order)
            pg._ID_TABLES = set(pg.TABLE_ORDER)
    except Exception:
        pass

    active = cloud_db.CloudDatabase
    if active is not base:
        active._qlda_contractor_workspace_installed = True
