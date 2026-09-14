from __future__ import annotations

"""Native V7.2 project/workspace authorization over PostgreSQL.

This adapter preserves the existing contractor workspace tables and access
semantics without importing the deprecated V6 service layer or monkey patches.
"""

from datetime import datetime, timezone
from typing import Any

from qlda.domain.models import ProjectScope
from qlda.infrastructure.postgres import connect

CONTRACTOR = "CONTRACTOR"
PROJECT_VIEWER = "PROJECT_VIEWER"
ACTIVE_STATUS = "Đang hoạt động"
DEFAULT_CODE = "NT-01"
DEFAULT_NAME = "Nhà thầu hiện tại"

_ACCESS_SCHEMA = r"""
CREATE TABLE IF NOT EXISTS project_contractors (
    id SERIAL PRIMARY KEY,
    master_project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    workspace_project_id INTEGER NOT NULL UNIQUE REFERENCES projects(id) ON DELETE CASCADE,
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
    UNIQUE(master_project_id, contractor_code)
);
CREATE INDEX IF NOT EXISTS idx_project_contractors_master
    ON project_contractors(master_project_id, status, id);
CREATE INDEX IF NOT EXISTS idx_project_contractors_workspace
    ON project_contractors(workspace_project_id);

CREATE TABLE IF NOT EXISTS project_user_contractor_access (
    id SERIAL PRIMARY KEY,
    master_project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_email TEXT NOT NULL,
    access_mode TEXT NOT NULL DEFAULT 'ALL',
    contractor_id INTEGER REFERENCES project_contractors(id) ON DELETE CASCADE,
    workspace_project_id INTEGER REFERENCES projects(id) ON DELETE CASCADE,
    created_at TEXT DEFAULT '',
    updated_at TEXT DEFAULT '',
    UNIQUE(master_project_id, user_email)
);
CREATE INDEX IF NOT EXISTS idx_project_user_contractor_access_email
    ON project_user_contractor_access(user_email, master_project_id);
CREATE INDEX IF NOT EXISTS idx_project_user_contractor_access_workspace
    ON project_user_contractor_access(workspace_project_id);
"""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _approval(value: Any) -> str:
    return str(value or "").strip().upper().replace("-", "_").replace(" ", "_")


def _email(value: Any) -> str:
    return str(value or "").strip().lower()


def _positive_id(value: Any) -> int:
    try:
        number = int(value)
    except Exception as exc:
        raise ValueError("project_id không hợp lệ.") from exc
    if number <= 0:
        raise ValueError("project_id không hợp lệ.")
    return number


def ensure_access_schema() -> None:
    """Create only the two access tables when a fresh PostgreSQL DB needs them."""
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(_ACCESS_SCHEMA)
        conn.commit()


def _project(cur, project_id: int) -> dict[str, Any]:
    cur.execute(
        "SELECT id, code, name FROM projects WHERE id=%s LIMIT 1",
        (int(project_id),),
    )
    row = cur.fetchone()
    return dict(row) if row else {}


def _resolve_master(cur, project_id: int) -> int:
    cur.execute(
        """SELECT master_project_id
           FROM project_contractors
           WHERE workspace_project_id=%s
           LIMIT 1""",
        (int(project_id),),
    )
    row = cur.fetchone()
    return int(row.get("master_project_id") or project_id) if row else int(project_id)


def _contractor_rows(cur, master_project_id: int, *, active_only: bool) -> list[dict[str, Any]]:
    sql = "SELECT * FROM project_contractors WHERE master_project_id=%s"
    params: list[Any] = [int(master_project_id)]
    if active_only:
        sql += " AND status=%s"
        params.append(ACTIVE_STATUS)
    sql += " ORDER BY is_default DESC, id"
    cur.execute(sql, tuple(params))
    return [dict(row) for row in cur.fetchall()]


def _ensure_default_contractor(cur, master_project_id: int) -> None:
    master_id = int(master_project_id)
    existing = _contractor_rows(cur, master_id, active_only=False)
    if existing:
        return
    project = _project(cur, master_id)
    if not project:
        return
    stamp = _now()
    cur.execute(
        """INSERT INTO project_contractors(
               master_project_id, workspace_project_id, contractor_code, contractor_name,
               status, is_default, created_at, updated_at
           ) VALUES (%s,%s,%s,%s,%s,1,%s,%s)
           ON CONFLICT DO NOTHING""",
        (master_id, master_id, DEFAULT_CODE, DEFAULT_NAME, ACTIVE_STATUS, stamp, stamp),
    )


def _active_contractors(cur, master_project_id: int) -> list[dict[str, Any]]:
    rows = _contractor_rows(cur, int(master_project_id), active_only=True)
    if rows:
        return rows
    # Preserve the legacy safe migration: create a default contractor only when
    # the project has never had any contractor rows. Inactive rows stay inactive.
    if not _contractor_rows(cur, int(master_project_id), active_only=False):
        _ensure_default_contractor(cur, int(master_project_id))
        rows = _contractor_rows(cur, int(master_project_id), active_only=True)
    return rows


def _access_row(cur, master_project_id: int, user_email: str) -> dict[str, Any]:
    cur.execute(
        """SELECT * FROM project_user_contractor_access
           WHERE master_project_id=%s AND user_email=%s
           LIMIT 1""",
        (int(master_project_id), _email(user_email)),
    )
    row = cur.fetchone()
    return dict(row) if row else {}


def _bind_single_contractor(
    cur,
    master_project_id: int,
    user_email: str,
    contractor: dict[str, Any],
) -> dict[str, Any]:
    stamp = _now()
    cur.execute(
        """INSERT INTO project_user_contractor_access(
               master_project_id, user_email, access_mode, contractor_id,
               workspace_project_id, created_at, updated_at
           ) VALUES (%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT (master_project_id,user_email)
           DO UPDATE SET access_mode=EXCLUDED.access_mode,
                         contractor_id=EXCLUDED.contractor_id,
                         workspace_project_id=EXCLUDED.workspace_project_id,
                         updated_at=EXCLUDED.updated_at
           RETURNING *""",
        (
            int(master_project_id),
            _email(user_email),
            CONTRACTOR,
            int(contractor.get("id") or 0),
            int(contractor.get("workspace_project_id") or 0),
            stamp,
            stamp,
        ),
    )
    row = cur.fetchone()
    return dict(row) if row else {}


def _authorized_rows(
    cur,
    master_project_id: int,
    user: dict[str, Any],
) -> list[dict[str, Any]]:
    rows = _active_contractors(cur, int(master_project_id))
    approval_role = _approval(user.get("approval_role"))
    if approval_role != CONTRACTOR:
        return rows

    email = _email(user.get("email"))
    if not email:
        raise PermissionError("Tài khoản Nhà thầu chưa xác định được email đăng nhập.")

    access = _access_row(cur, int(master_project_id), email)
    if not access and len(rows) == 1:
        access = _bind_single_contractor(cur, int(master_project_id), email, rows[0])

    if str(access.get("access_mode") or "").upper() != CONTRACTOR:
        raise PermissionError(
            "Tài khoản Nhà thầu chưa được gán nhà thầu cho dự án này. "
            "Admin cần gán đúng nhà thầu trước khi truy cập."
        )

    contractor_id = int(access.get("contractor_id") or 0)
    allowed = [row for row in rows if int(row.get("id") or 0) == contractor_id]
    if not allowed:
        raise PermissionError(
            "Nhà thầu được gán không còn hoạt động hoặc không thuộc dự án này."
        )
    return allowed


class NativeProjectAccessAdapter:
    """ProjectAccessPort implementation with no qlda.services/V6 dependency."""

    def require_project(self, user: dict[str, Any], project_id: int) -> ProjectScope:
        pid = _positive_id(project_id)
        ensure_access_schema()
        with connect() as conn:
            with conn.cursor() as cur:
                project = _project(cur, pid)
                if not project:
                    raise FileNotFoundError("Không tìm thấy dự án.")
                master_id = _resolve_master(cur, pid)
                role = str(user.get("role") or "read").strip().lower()
                is_admin = role == "admin"

                if is_admin and pid == master_id:
                    allowed = True
                else:
                    rows = _authorized_rows(cur, master_id, user)
                    allowed_ids = {
                        int(row.get("workspace_project_id") or 0)
                        for row in rows
                        if int(row.get("workspace_project_id") or 0) > 0
                    }
                    allowed = pid in allowed_ids

                if not allowed:
                    if is_admin:
                        raise PermissionError("Workspace không thuộc phạm vi dự án được phép.")
                    raise PermissionError("Tài khoản không có quyền truy cập workspace dự án này.")
            conn.commit()

        return ProjectScope(
            requested_project_id=pid,
            master_project_id=int(master_id),
            workspace_project_id=pid,
            project_code=str(project.get("code") or ""),
            is_master_scope=bool(is_admin and pid == master_id),
        )

    def require_project_code(self, user: dict[str, Any], project_code: str) -> ProjectScope:
        code = str(project_code or "").strip()
        if not code:
            raise ValueError("project_code không hợp lệ.")
        ensure_access_schema()
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM projects WHERE code=%s LIMIT 1", (code,))
                row = cur.fetchone()
        if not row:
            raise FileNotFoundError("Không tìm thấy dự án.")
        return self.require_project(user, int(row.get("id") or 0))
