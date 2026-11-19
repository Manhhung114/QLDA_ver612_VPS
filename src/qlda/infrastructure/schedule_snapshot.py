from __future__ import annotations

"""Atomic schedule snapshot import for Microsoft Project.

A new complete schedule can replace the current workspace's entire timeline
without first deleting it in a separate, non-transactional user action.
"""

from typing import Any
from datetime import date

from qlda.import_engines.schedule_persistence import _atomic, _scalar_int
from qlda.runtime_core.project_store import calc_progress_status


def save_project_schedule(
    db: Any,
    project_id: int,
    tasks: list[dict],
    source_name: str,
    project_info: dict | None = None,
    *,
    replace_all: bool = False,
    expected_existing_rows: int | None = None,
) -> dict[str, int | bool]:
    pid = int(project_id)
    if pid <= 0:
        raise ValueError("Dự án không hợp lệ.")
    info = dict(project_info or {})
    prepared = [dict(row) for row in tasks if int(row.get("source_uid") or 0) > 0]
    uids = [int(row["source_uid"]) for row in prepared]
    if not prepared or len(uids) != len(set(uids)) or len(prepared) != len(tasks):
        raise ValueError("Microsoft Project không có UniqueID hợp lệ/duy nhất cho mọi công việc.")
    if not replace_all:
        db.sync_mpp_tasks(pid, prepared, source_name, info)
        with db.connect() as conn:
            total = _scalar_int(conn.execute(
                "SELECT COUNT(*) AS row_count FROM tasks WHERE project_id=?", (pid,),
            ).fetchone())
            mpp = _scalar_int(conn.execute(
                "SELECT COUNT(*) AS row_count FROM tasks WHERE project_id=? AND source_type='mpp'", (pid,),
            ).fetchone())
        if mpp != len(prepared):
            raise RuntimeError(f"Đồng bộ Project chưa đủ {mpp}/{len(prepared)} công việc.")
        return {"written_rows": mpp, "total_rows": total, "replace_all": False}

    if expected_existing_rows is None:
        raise ValueError("Chưa xác nhận số công việc cũ trước khi thay thế toàn bộ.")

    fields = [
        "project_id", "wbs", "name", "responsible", "start_date", "end_date", "duration",
        "planned_progress", "actual_progress", "status", "predecessor", "note",
        "source_type", "source_uid", "source_task_id", "outline_level", "is_summary",
        "is_milestone", "critical", "total_slack", "resource_names",
        "baseline_start", "baseline_finish",
    ]
    insert_sql = "INSERT INTO tasks(" + ",".join(fields) + ") VALUES(" + ",".join("?" for _ in fields) + ")"
    expected = len(prepared)
    with db.connect() as conn, _atomic(conn):
        existing = _scalar_int(conn.execute(
            "SELECT COUNT(*) AS row_count FROM tasks WHERE project_id=?", (pid,),
        ).fetchone())
        if existing != int(expected_existing_rows):
            raise ValueError(
                f"Bảng tiến độ đã thay đổi ({expected_existing_rows} → {existing}). "
                "Hãy xem trước và xác nhận lại."
            )
        conn.execute("DELETE FROM tasks WHERE project_id=?", (pid,))
        params = []
        for task in prepared:
            start = str(task.get("start_date") or "")
            finish = str(task.get("end_date") or "")
            actual = max(0, min(100, int(task.get("actual_progress") or 0)))
            planned = max(0, min(100, int(task.get("planned_progress") or 0)))
            row = {
                "project_id": pid,
                "wbs": str(task.get("wbs") or ""),
                "name": str(task.get("name") or ""),
                "responsible": str(task.get("responsible") or ""),
                "start_date": start, "end_date": finish,
                "duration": float(task.get("duration") or 0),
                "planned_progress": planned, "actual_progress": actual,
                "status": calc_progress_status(start, finish, planned, actual),
                "predecessor": str(task.get("predecessor") or ""),
                "note": str(task.get("note") or ""),
                "source_type": "mpp", "source_uid": int(task["source_uid"]),
                "source_task_id": int(task.get("task_id") or 0),
                "outline_level": int(task.get("outline_level") or 1),
                "is_summary": int(bool(task.get("is_summary"))),
                "is_milestone": int(bool(task.get("is_milestone"))),
                "critical": int(bool(task.get("critical"))),
                "total_slack": float(task.get("total_slack") or 0),
                "resource_names": str(task.get("resource_names") or ""),
                "baseline_start": str(task.get("baseline_start") or ""),
                "baseline_finish": str(task.get("baseline_finish") or ""),
            }
            params.append(tuple(row[field] for field in fields))
        conn.executemany(insert_sql, params)
        start_date = str(info.get("start_date") or "")
        end_date = str(info.get("end_date") or "")
        manager = str(info.get("manager") or "")
        conn.execute(
            """UPDATE projects SET source_mpp_path=?,last_sync=CURRENT_TIMESTAMP,
            start_date=CASE WHEN ?<>'' THEN ? ELSE start_date END,
            end_date=CASE WHEN ?<>'' THEN ? ELSE end_date END,
            manager=CASE WHEN ?<>'' THEN ? ELSE manager END WHERE id=?""",
            (source_name, start_date, start_date, end_date, end_date, manager, manager, pid),
        )
        total = _scalar_int(conn.execute(
            "SELECT COUNT(*) AS row_count FROM tasks WHERE project_id=?", (pid,),
        ).fetchone())
        mpp = _scalar_int(conn.execute(
            "SELECT COUNT(*) AS row_count FROM tasks WHERE project_id=? AND source_type='mpp'", (pid,),
        ).fetchone())
        if total != expected or mpp != expected:
            raise RuntimeError(
                f"Project chưa đủ {mpp}/{expected}, tổng {total}; transaction đã rollback."
            )
    return {"written_rows": mpp, "total_rows": total, "replace_all": True}


__all__ = ["save_project_schedule"]
