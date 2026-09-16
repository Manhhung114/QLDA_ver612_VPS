from __future__ import annotations

"""Shared finance/project-cost helpers introduced by Cleanup V2.1.

This module is deliberately independent from the retired cashflow forecast V1/V2/V3
UI stack.  Active finance code can therefore resolve contractor scope and calculate
EVM task progress without importing legacy forecast modules.
"""

from datetime import date, datetime
from typing import Any

PATCH_MARKER = "V7.6 CLEANUP V2.1 FINANCE COMMON"


def text(value: Any) -> str:
    return str(value or "").strip()


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or 0)
    except Exception:
        return float(default)


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or 0)
    except Exception:
        return int(default)


def rowdict(row: Any) -> dict[str, Any]:
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


def parse_date(value: Any) -> date | None:
    """Parse the date formats used by finance, IPC and schedule data."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    raw = text(value)
    if not raw:
        return None
    for fmt in (
        "%Y-%m-%d",
        "%Y-%m-%d %H:%M:%S",
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%Y/%m/%d",
    ):
        try:
            candidate = raw[:19] if "%H" in fmt else raw[:10]
            return datetime.strptime(candidate, fmt).date()
        except Exception:
            pass
    return None


def date_text(value: Any) -> str:
    parsed = parse_date(value)
    return parsed.strftime("%d/%m/%Y") if parsed else ""


def resolve_scope(db, workspace_project_id: int) -> dict[str, Any]:
    """Resolve master/workspace/contractor identity without cashflow V1 coupling."""
    pid = int(workspace_project_id)
    info: dict[str, Any] = {
        "master_project_id": pid,
        "workspace_project_id": pid,
        "contractor_code": "",
        "contractor_name": "",
    }
    try:
        from qlda.runtime_core import contractor_workspace as cw

        with db.connect() as connection:
            cw.ensure_schema_connection(connection)
            row = connection.execute(
                f"SELECT master_project_id,workspace_project_id,contractor_code,contractor_name "
                f"FROM {cw.TABLE_NAME} WHERE workspace_project_id=? LIMIT 1",
                (pid,),
            ).fetchone()
        if row:
            info.update(rowdict(row))
    except Exception:
        # Preserve the historical behavior: finance still works at workspace scope
        # even when contractor metadata is unavailable during a partial migration.
        pass

    info["master_project_id"] = as_int(info.get("master_project_id"), pid) or pid
    info["workspace_project_id"] = as_int(info.get("workspace_project_id"), pid) or pid
    return info


def scope_label(db, workspace_project_id: int) -> str:
    scope = resolve_scope(db, int(workspace_project_id))
    label = " - ".join(
        item
        for item in (text(scope.get("contractor_code")), text(scope.get("contractor_name")))
        if item
    )
    return label or "Workspace mặc định"


def task_ref(task: dict[str, Any]) -> str:
    task_id = task.get("source_task_id") or task.get("id") or ""
    return f"[TASK:{task_id}/{text(task.get('wbs'))}]"


def actual_progress(task: dict[str, Any]) -> float:
    override = task.get("actual_override")
    value = override if override not in (None, "") else task.get("actual_progress")
    return max(0.0, min(100.0, as_float(value)))


def planned_progress(task: dict[str, Any], on_date: date) -> float:
    start = parse_date(task.get("start_date"))
    finish = parse_date(task.get("end_date"))
    if not start or not finish:
        return max(0.0, min(100.0, as_float(task.get("planned_progress"))))
    if on_date < start:
        return 0.0
    if on_date >= finish:
        return 100.0
    total = max(1, (finish - start).days + 1)
    elapsed = max(0, (on_date - start).days + 1)
    return max(0.0, min(100.0, elapsed * 100.0 / total))


__all__ = [
    "PATCH_MARKER",
    "text",
    "as_float",
    "as_int",
    "rowdict",
    "parse_date",
    "date_text",
    "resolve_scope",
    "scope_label",
    "task_ref",
    "actual_progress",
    "planned_progress",
]
