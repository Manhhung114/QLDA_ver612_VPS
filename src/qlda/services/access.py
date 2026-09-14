from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Any

from qlda.infrastructure.database import make_database
from qlda.services.base import require_project_id
from qlda.shared.legacy import load_module


@dataclass(frozen=True)
class ProjectScope:
    requested_project_id: int
    master_project_id: int
    workspace_project_id: int
    project_code: str
    is_master_scope: bool = False


_BOOTSTRAP_LOCK = Lock()
_BOOTSTRAPPED = False


def _bootstrap_access_policy() -> tuple[Any, Any]:
    """Install the proven contractor/default-workspace access rules once."""
    global _BOOTSTRAPPED
    if not _BOOTSTRAPPED:
        with _BOOTSTRAP_LOCK:
            if not _BOOTSTRAPPED:
                make_database()
                workspace = load_module("contractor_workspace_v622")
                access = load_module("contractor_access_control_v622")
                guard = load_module("default_workspace_admin_guard_v622")
                workspace.install_contractor_workspace()
                access.install_contractor_access_control()
                guard.install_default_workspace_admin_guard()
                _BOOTSTRAPPED = True
    return load_module("contractor_workspace_v622"), load_module("contractor_access_control_v622")


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


class ProjectAccessService:
    """Resolve API requests to the same project/contractor scope as Streamlit."""

    @classmethod
    def require_project(cls, user: dict[str, Any], project_id: int) -> ProjectScope:
        pid = require_project_id(project_id)
        workspace, access = _bootstrap_access_policy()
        db = make_database()
        project = _rowdict(db.project(pid))
        if not project:
            raise FileNotFoundError("Không tìm thấy dự án.")

        with db.connect() as connection:
            master_id = int(workspace.resolve_master_project_id_connection(connection, pid))

        role = str(user.get("role") or "read").strip().lower()
        email = str(user.get("email") or "").strip().lower()
        approval_role = str(user.get("approval_role") or "").strip()
        is_admin = role == "admin"

        rows = access.authorized_contractor_rows(
            db,
            master_id,
            email,
            approval_role,
            active_only=True,
            can_admin=is_admin,
        )
        allowed_ids = {
            int(row.get("workspace_project_id") or 0)
            for row in rows
            if int(row.get("workspace_project_id") or 0) > 0
        }

        if not is_admin and pid not in allowed_ids:
            raise PermissionError("Tài khoản không có quyền truy cập workspace dự án này.")
        if is_admin and pid != master_id and pid not in allowed_ids:
            raise PermissionError("Workspace không thuộc phạm vi dự án được phép.")

        return ProjectScope(
            requested_project_id=pid,
            master_project_id=master_id,
            workspace_project_id=pid,
            project_code=str(project.get("code") or ""),
            is_master_scope=bool(is_admin and pid == master_id),
        )

    @classmethod
    def require_project_code(cls, user: dict[str, Any], project_code: str) -> ProjectScope:
        code = str(project_code or "").strip()
        if not code:
            raise ValueError("project_code không hợp lệ.")
        _bootstrap_access_policy()
        db = make_database()
        with db.connect() as connection:
            row = connection.execute(
                "SELECT id FROM projects WHERE code=? LIMIT 1",
                (code,),
            ).fetchone()
        data = _rowdict(row)
        if not data:
            raise FileNotFoundError("Không tìm thấy dự án.")
        return cls.require_project(user, int(data.get("id") or 0))
