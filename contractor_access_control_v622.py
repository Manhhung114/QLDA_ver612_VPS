from __future__ import annotations

from contextvars import ContextVar
from datetime import datetime
from typing import Any

from contractor_workspace_v622 import list_contractors, resolve_master_project_id_connection


PATCH_MARKER = "V6.22 CONTRACTOR ACCESS CONTROL V1"
TABLE_NAME = "project_user_contractor_access"
PROJECT_VIEWER = "PROJECT_VIEWER"
CONTRACTOR = "CONTRACTOR"
ALL = "ALL"

_AI_WORKSPACE_SCOPE: ContextVar[int | None] = ContextVar(
    "qlda_ai_contractor_workspace_scope", default=None
)


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


def _email(value: Any) -> str:
    return str(value or "").strip().lower()


def _approval(value: Any) -> str:
    return str(value or "").strip().upper().replace("-", "_").replace(" ", "_")


def ensure_schema_connection(connection) -> None:
    connection.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {TABLE_NAME}(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            master_project_id INTEGER NOT NULL,
            user_email TEXT NOT NULL,
            access_mode TEXT NOT NULL DEFAULT 'ALL',
            contractor_id INTEGER,
            workspace_project_id INTEGER,
            created_at TEXT DEFAULT '',
            updated_at TEXT DEFAULT '',
            FOREIGN KEY(master_project_id) REFERENCES projects(id) ON DELETE CASCADE,
            FOREIGN KEY(contractor_id) REFERENCES project_contractors(id) ON DELETE CASCADE,
            FOREIGN KEY(workspace_project_id) REFERENCES projects(id) ON DELETE CASCADE,
            UNIQUE(master_project_id,user_email)
        )
        """
    )
    connection.execute(
        f"CREATE INDEX IF NOT EXISTS idx_project_user_contractor_access_email "
        f"ON {TABLE_NAME}(user_email,master_project_id)"
    )
    connection.execute(
        f"CREATE INDEX IF NOT EXISTS idx_project_user_contractor_access_workspace "
        f"ON {TABLE_NAME}(workspace_project_id)"
    )


def ensure_schema(db) -> None:
    with db.connect() as connection:
        ensure_schema_connection(connection)


def get_user_project_access(db, master_project_id: int, user_email: str) -> dict[str, Any]:
    email = _email(user_email)
    if not email or not master_project_id:
        return {}
    with db.connect() as connection:
        ensure_schema_connection(connection)
        master_id = resolve_master_project_id_connection(connection, int(master_project_id))
        row = connection.execute(
            f"SELECT * FROM {TABLE_NAME} WHERE master_project_id=? AND user_email=? LIMIT 1",
            (master_id, email),
        ).fetchone()
        return _rowdict(row)


def _contractor_by_id(db, master_project_id: int, contractor_id: int) -> dict[str, Any]:
    cid = int(contractor_id or 0)
    if cid <= 0:
        return {}
    rows = list_contractors(db, int(master_project_id), active_only=False)
    return next((dict(row) for row in rows if int(row.get("id") or 0) == cid), {})


def set_user_project_access(
    db,
    master_project_id: int,
    user_email: str,
    classification: str,
    contractor_id: int | None = None,
) -> dict[str, Any]:
    email = _email(user_email)
    if not email:
        raise ValueError("Email người dùng không hợp lệ.")
    role = _approval(classification)
    mode = PROJECT_VIEWER if role == PROJECT_VIEWER else CONTRACTOR if role == CONTRACTOR else ALL

    contractor: dict[str, Any] = {}
    if mode == CONTRACTOR:
        contractor = _contractor_by_id(db, int(master_project_id), int(contractor_id or 0))
        if not contractor:
            raise ValueError("Phân loại Nhà thầu bắt buộc phải chọn đúng một nhà thầu trong dự án.")
        if str(contractor.get("status") or "") != "Đang hoạt động":
            raise ValueError("Nhà thầu được gán hiện không ở trạng thái Đang hoạt động.")

    with db.connect() as connection:
        ensure_schema_connection(connection)
        master_id = resolve_master_project_id_connection(connection, int(master_project_id))
        current = connection.execute(
            f"SELECT id FROM {TABLE_NAME} WHERE master_project_id=? AND user_email=? LIMIT 1",
            (master_id, email),
        ).fetchone()
        cid = int(contractor.get("id") or 0) if contractor else None
        wid = int(contractor.get("workspace_project_id") or 0) if contractor else None
        stamp = _now()
        if current:
            connection.execute(
                f"""UPDATE {TABLE_NAME}
                    SET access_mode=?,contractor_id=?,workspace_project_id=?,updated_at=?
                    WHERE master_project_id=? AND user_email=?""",
                (mode, cid, wid, stamp, master_id, email),
            )
        else:
            connection.execute(
                f"""INSERT INTO {TABLE_NAME}(
                       master_project_id,user_email,access_mode,contractor_id,workspace_project_id,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?)""",
                (master_id, email, mode, cid, wid, stamp, stamp),
            )
        row = connection.execute(
            f"SELECT * FROM {TABLE_NAME} WHERE master_project_id=? AND user_email=? LIMIT 1",
            (master_id, email),
        ).fetchone()
        return _rowdict(row)


def delete_user_project_access(db, user_email: str, master_project_id: int | None = None) -> None:
    email = _email(user_email)
    if not email:
        return
    with db.connect() as connection:
        ensure_schema_connection(connection)
        if master_project_id:
            master_id = resolve_master_project_id_connection(connection, int(master_project_id))
            connection.execute(
                f"DELETE FROM {TABLE_NAME} WHERE master_project_id=? AND user_email=?",
                (master_id, email),
            )
        else:
            connection.execute(f"DELETE FROM {TABLE_NAME} WHERE user_email=?", (email,))


def user_can_view_all_contractors(approval_role: str) -> bool:
    return _approval(approval_role) != CONTRACTOR


def authorized_contractor_rows(
    db,
    master_project_id: int,
    user_email: str,
    approval_role: str,
    *,
    active_only: bool = True,
) -> list[dict[str, Any]]:
    rows = [dict(row) for row in list_contractors(db, int(master_project_id), active_only=active_only)]
    if _approval(approval_role) != CONTRACTOR:
        return rows

    email = _email(user_email)
    if not email:
        raise PermissionError("Tài khoản Nhà thầu chưa xác định được email đăng nhập.")
    access = get_user_project_access(db, int(master_project_id), email)

    # Backward-compatible safe migration: if the project has exactly one active
    # contractor, an old CONTRACTOR account cannot leak into another workspace,
    # so it may be bound automatically. With 2+ contractors Admin must assign it.
    if not access and len(rows) == 1:
        access = set_user_project_access(
            db, int(master_project_id), email, CONTRACTOR, int(rows[0].get("id") or 0)
        )

    if str(access.get("access_mode") or "").upper() != CONTRACTOR:
        raise PermissionError(
            "Tài khoản được phân loại Nhà thầu nhưng chưa được gán nhà thầu cho dự án này. "
            "Admin vào Cài đặt → Google Drive & quyền để gán đúng nhà thầu."
        )

    contractor_id = int(access.get("contractor_id") or 0)
    allowed = [row for row in rows if int(row.get("id") or 0) == contractor_id]
    if not allowed:
        raise PermissionError(
            "Nhà thầu được gán không còn hoạt động hoặc không thuộc dự án này. "
            "Vui lòng liên hệ Admin để cập nhật phạm vi truy cập."
        )
    return allowed


def effective_user_classification(
    db,
    master_project_id: int,
    user_email: str,
    gateway_approval_role: str,
) -> str:
    access = get_user_project_access(db, int(master_project_id), user_email) if master_project_id else {}
    if str(access.get("access_mode") or "").upper() == PROJECT_VIEWER:
        return PROJECT_VIEWER
    return _approval(gateway_approval_role)


def user_contractor_scope_label(
    db,
    master_project_id: int,
    user_email: str,
    gateway_approval_role: str,
) -> str:
    classification = effective_user_classification(
        db, int(master_project_id), user_email, gateway_approval_role
    ) if master_project_id else _approval(gateway_approval_role)
    if classification == PROJECT_VIEWER:
        return "Tất cả nhà thầu • chỉ xem"
    if classification != CONTRACTOR:
        return "Tất cả nhà thầu"
    access = get_user_project_access(db, int(master_project_id), user_email)
    cid = int(access.get("contractor_id") or 0)
    contractor = _contractor_by_id(db, int(master_project_id), cid) if cid else {}
    if not contractor:
        return "⚠ Chưa gán nhà thầu"
    return f"{contractor.get('contractor_code','')} - {contractor.get('contractor_name','')}"


def render_authorized_contractor_selector(
    db,
    master_project_id: int,
    *,
    can_admin: bool = False,
    current_user: str = "",
    approval_role: str = "",
) -> tuple[int, dict[str, Any]]:
    del can_admin
    import streamlit as st

    try:
        rows = authorized_contractor_rows(
            db, int(master_project_id), current_user, approval_role, active_only=True
        )
    except PermissionError as exc:
        st.sidebar.error(str(exc))
        st.error(str(exc))
        st.stop()
        return int(master_project_id), {}

    if not rows:
        return int(master_project_id), {}

    # CONTRACTOR accounts never receive a selector containing other contractors.
    if _approval(approval_role) == CONTRACTOR:
        info = dict(rows[0])
        st.sidebar.markdown(
            f"**Nhà thầu:** {info.get('contractor_code','')} - {info.get('contractor_name','')}"
        )
        st.sidebar.caption("🔒 Phạm vi tài khoản: chỉ dữ liệu của nhà thầu này.")
        return int(info["workspace_project_id"]), info

    by_workspace = {int(row["workspace_project_id"]): row for row in rows}
    ids = list(by_workspace)
    state_key = f"contractor_workspace_{int(master_project_id)}"
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
        key=f"contractor_workspace_select_{int(master_project_id)}",
    )
    st.session_state[state_key] = int(selected)
    info = dict(by_workspace[int(selected)])
    st.sidebar.caption("👁 Có quyền xem/chuyển giữa toàn bộ nhà thầu của dự án.")
    return int(selected), info


def set_ai_workspace_scope(workspace_project_id: int | None) -> None:
    value = int(workspace_project_id or 0)
    _AI_WORKSPACE_SCOPE.set(value if value > 0 else None)


def capture_single_contractor_ai_context() -> None:
    """Capture the fully-patched single-workspace AI methods before aggregation."""
    import ai_service

    cls = ai_service.ProjectContextBuilder
    if not hasattr(cls, "_qlda_single_contractor_build"):
        cls._qlda_single_contractor_build = cls.build
    if not hasattr(cls, "_qlda_single_contractor_catalog"):
        cls._qlda_single_contractor_catalog = cls.attachment_catalog


def install_ai_access_guard() -> None:
    """Apply a ContextVar guard after the project-wide multi-contractor AI patch.

    Streamlit sessions execute in separate contexts. A CONTRACTOR screen sets its
    authorized workspace in this ContextVar; all AI snapshot/file catalog calls
    then bypass the project aggregate and use only the captured single workspace.
    """
    import ai_service

    cls = ai_service.ProjectContextBuilder
    if getattr(cls, "_qlda_contractor_ai_access_guard", False):
        return
    single_build = getattr(cls, "_qlda_single_contractor_build", None)
    single_catalog = getattr(cls, "_qlda_single_contractor_catalog", None)
    if not callable(single_build) or not callable(single_catalog):
        raise RuntimeError("Chưa capture single-contractor AI context trước khi cài access guard.")

    project_build = cls.build
    project_catalog = cls.attachment_catalog

    def guarded_build(self, project_id: int, *args, **kwargs):
        restricted = _AI_WORKSPACE_SCOPE.get()
        if restricted:
            return single_build(self, int(restricted), *args, **kwargs)
        return project_build(self, int(project_id), *args, **kwargs)

    def guarded_catalog(self, project_id: int):
        restricted = _AI_WORKSPACE_SCOPE.get()
        if restricted:
            return single_catalog(self, int(restricted))
        return project_catalog(self, int(project_id))

    cls.build = guarded_build
    cls.attachment_catalog = guarded_catalog
    cls._qlda_contractor_ai_access_guard = True
    cls._qlda_contractor_ai_access_marker = PATCH_MARKER


def install_contractor_access_control() -> None:
    """Persist project/user scope without changing the Apps Script account schema."""
    import cloud_db

    base = getattr(cloud_db, "SQLiteCloudDatabase", None) or cloud_db.CloudDatabase
    if not getattr(base, "_qlda_contractor_access_control_installed", False):
        original_create_tables = base.create_tables
        original_migrate = base.migrate

        def create_tables_with_user_scope(self):
            original_create_tables(self)
            try:
                ensure_schema(self)
            except Exception:
                pass

        def migrate_with_user_scope(self):
            original_migrate(self)
            ensure_schema(self)

        base.create_tables = create_tables_with_user_scope
        base.migrate = migrate_with_user_scope
        base._qlda_contractor_access_control_installed = True

    try:
        import postgres_backend_v622 as pg
        if TABLE_NAME not in pg.TABLE_ORDER:
            order = list(pg.TABLE_ORDER)
            anchor = "project_contractors"
            insert_at = order.index(anchor) + 1 if anchor in order else (order.index("projects") + 1 if "projects" in order else 0)
            order.insert(insert_at, TABLE_NAME)
            pg.TABLE_ORDER = tuple(order)
            pg._ID_TABLES = set(pg.TABLE_ORDER)
    except Exception:
        pass

    active = cloud_db.CloudDatabase
    active._qlda_contractor_access_control_installed = True
