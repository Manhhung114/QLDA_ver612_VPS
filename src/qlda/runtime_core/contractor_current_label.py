from __future__ import annotations

"""Keep contractor labels current after Admin renames a contractor.

Contractor workspaces have two metadata layers:
- ``project_contractors``: the current Admin-managed contractor code/name;
- ``projects``: the stable technical workspace row created when the contractor was added.

The technical workspace code must stay stable because VPS file metadata and other
references may use it.  User-facing labels, especially assignment emails, must
instead read the current contractor metadata.
"""

from typing import Any

PATCH_MARKER = "V7.6 CONTRACTOR CURRENT LABEL V1"


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


def _text(value: Any) -> str:
    return str(value or "").strip()


def current_contractor_label(db, workspace_project_id: int) -> str:
    """Return current ``Mã nhà thầu - Tên nhà thầu`` for non-default workspaces."""
    pid = int(workspace_project_id or 0)
    if pid <= 0:
        return ""
    try:
        from qlda.runtime_core import contractor_workspace as cw
        with db.connect() as connection:
            cw.ensure_schema_connection(connection)
            row = connection.execute(
                f"SELECT contractor_code,contractor_name,master_project_id,workspace_project_id,is_default "
                f"FROM {cw.TABLE_NAME} WHERE workspace_project_id=? LIMIT 1",
                (pid,),
            ).fetchone()
        info = _rowdict(row)
    except Exception:
        return ""

    if not info:
        return ""
    master_id = int(info.get("master_project_id") or 0)
    workspace_id = int(info.get("workspace_project_id") or 0)
    is_default = bool(int(info.get("is_default") or 0))
    if is_default or (master_id > 0 and workspace_id == master_id):
        return ""

    code = _text(info.get("contractor_code"))
    name = _text(info.get("contractor_name"))
    if code and name:
        return f"{code} - {name}"
    return code or name


def _sync_workspace_display_name(db, contractor_id: int) -> None:
    """Refresh projects.name after a contractor rename without changing stable code."""
    try:
        from qlda.runtime_core import contractor_workspace as cw
        with db.connect() as connection:
            cw.ensure_schema_connection(connection)
            row = connection.execute(
                f"SELECT pc.contractor_name,pc.master_project_id,pc.workspace_project_id,pc.is_default,"
                "mp.name AS master_name "
                f"FROM {cw.TABLE_NAME} pc LEFT JOIN projects mp ON mp.id=pc.master_project_id "
                "WHERE pc.id=? LIMIT 1",
                (int(contractor_id),),
            ).fetchone()
            info = _rowdict(row)
            if not info:
                return
            master_id = int(info.get("master_project_id") or 0)
            workspace_id = int(info.get("workspace_project_id") or 0)
            if bool(int(info.get("is_default") or 0)) or workspace_id <= 0 or workspace_id == master_id:
                return
            contractor_name = _text(info.get("contractor_name"))
            master_name = _text(info.get("master_name")) or "Dự án"
            if contractor_name:
                connection.execute(
                    "UPDATE projects SET name=? WHERE id=?",
                    (f"{master_name} · {contractor_name}", workspace_id),
                )
    except Exception:
        # Renaming the contractor itself remains successful even if the display
        # mirror cannot be refreshed. Email labels still read project_contractors.
        return


def install_contractor_current_label() -> None:
    from qlda.runtime_core import contractor_workspace as cw
    from qlda.runtime_core import work_task_email_notification as mail

    if getattr(cw, "_qlda_current_contractor_label_installed", False):
        return

    original_update = cw.update_contractor
    original_project_label = mail._project_label

    def update_contractor_with_display_sync(db, contractor_id: int, data: dict[str, Any]) -> None:
        original_update(db, contractor_id, data)
        _sync_workspace_display_name(db, int(contractor_id))

    def project_or_current_contractor_label(db, project_id: int) -> str:
        current = current_contractor_label(db, int(project_id))
        return current or original_project_label(db, int(project_id))

    cw.update_contractor = update_contractor_with_display_sync
    mail._project_label = project_or_current_contractor_label
    cw._qlda_current_contractor_label_installed = True
    cw._qlda_current_contractor_label_marker = PATCH_MARKER
    mail._qlda_current_contractor_label_installed = True
    mail._qlda_current_contractor_label_marker = PATCH_MARKER


__all__ = ["current_contractor_label", "install_contractor_current_label"]
