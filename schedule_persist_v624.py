from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import date, datetime
from typing import Any, Callable


PATCH_VERSION = "V6.24.5 SCHEDULE EXCEL BATCHED PERSISTENCE"
VERIFY_COMPLETE = "HOÀN TẤT"
VERIFY_INCOMPLETE = "CHƯA ĐỦ"
SOURCE_PREFIX = "schedule_excel:"

ProgressFn = Callable[[int, str, str], None]
CancelFn = Callable[[], bool]


@contextmanager
def _atomic(connection):
    try:
        yield connection
    except BaseException:
        connection.rollback()
        raise


def _batch_size(value: int | None = None) -> int:
    if value is None:
        try:
            value = int(os.environ.get("QLDA_SCHEDULE_DB_BATCH_SIZE", "500") or 500)
        except Exception:
            value = 500
    return max(50, min(int(value), 2000))


def _scalar_int(row: Any) -> int:
    try:
        return max(0, int(row["row_count"] or 0))
    except Exception:
        try:
            return max(0, int(row[0] or 0))
        except Exception:
            return 0


def save_schedule_result_batched(
    db,
    project_id: int,
    result: dict[str, Any],
    *,
    progress: ProgressFn | None = None,
    cancelled: CancelFn | None = None,
    batch_size: int | None = None,
) -> dict[str, Any]:
    """Replace only prior background-Excel tasks and verify the new batch."""
    from cloud_db import calc_progress_status

    pid = int(project_id)
    if pid <= 0:
        raise ValueError("project_id tiến độ không hợp lệ.")
    tasks = list(result.get("tasks") or [])
    expected_rows = int(result.get("task_count") or len(tasks))
    prepared_rows = len(tasks)
    scanned_rows = int(result.get("detail_line_count") or prepared_rows)
    if expected_rows <= 0 or expected_rows != prepared_rows or scanned_rows != prepared_rows:
        raise ValueError(
            f"Tiến độ chưa nhất quán: expected={expected_rows:,}, scanned={scanned_rows:,}, prepared={prepared_rows:,}."
        )
    batch_id = str(result.get("batch_id") or "").strip().lower()
    if not batch_id:
        raise ValueError("Tiến độ thiếu batch_id/SHA file gốc.")
    source_type = SOURCE_PREFIX + batch_id[:24]
    report_date = str(result.get("status_date") or date.today().isoformat())
    try:
        report_day = datetime.strptime(report_date, "%Y-%m-%d").date()
    except Exception:
        report_day = date.today()
        report_date = report_day.isoformat()
    chunk_size = _batch_size(batch_size)
    inserted_rows = 0

    fields = [
        "project_id", "wbs", "name", "responsible", "start_date", "end_date", "duration",
        "planned_progress", "actual_progress", "actual_update_date", "actual_finish_date", "status",
        "predecessor", "note", "source_type", "source_uid", "source_task_id", "outline_level",
        "is_summary", "is_milestone", "critical", "total_slack", "resource_names",
        "baseline_start", "baseline_finish",
    ]
    sql = f"INSERT INTO tasks({','.join(fields)}) VALUES({','.join('?' for _ in fields)})"
    with db.connect() as connection, _atomic(connection):
        if cancelled and cancelled():
            raise InterruptedError("Job tiến độ đã được yêu cầu hủy trước khi ghi database.")
        connection.execute(
            "DELETE FROM tasks WHERE project_id=? AND source_type LIKE ?",
            (pid, SOURCE_PREFIX + "%"),
        )
        for start_index in range(0, expected_rows, chunk_size):
            if cancelled and cancelled():
                raise InterruptedError("Job tiến độ đã được yêu cầu hủy; transaction đã rollback.")
            stop = min(expected_rows, start_index + chunk_size)
            params = []
            for task in tasks[start_index:stop]:
                actual = max(0, min(100, int(task.get("actual_progress") or 0)))
                planned = max(0, min(100, int(task.get("planned_progress") or 0)))
                status = calc_progress_status(
                    str(task.get("start_date") or ""),
                    str(task.get("end_date") or ""),
                    planned,
                    actual,
                    report_day,
                )
                row = {
                    "project_id": pid,
                    **task,
                    "planned_progress": planned,
                    "actual_progress": actual,
                    "actual_update_date": report_date if actual > 0 else "",
                    "actual_finish_date": report_date if actual >= 100 else "",
                    "status": status,
                    "source_type": source_type,
                    "source_uid": int(task.get("source_row_no") or 0),
                    "source_task_id": int(task.get("source_row_no") or 0),
                    "outline_level": 1,
                    "is_summary": 0,
                    "is_milestone": 0,
                    "critical": 0,
                    "total_slack": 0,
                    "resource_names": "",
                    "baseline_start": "",
                    "baseline_finish": "",
                }
                params.append(tuple(row.get(field) for field in fields))
            connection.executemany(sql, params)
            inserted_rows = stop
            if progress:
                progress(
                    min(92, 76 + int(inserted_rows * 16 / max(1, expected_rows))),
                    f"Đang ghi tiến độ vào PostgreSQL · {inserted_rows:,}/{expected_rows:,} công việc",
                    str(result.get("sheet_name") or ""),
                )
        written_rows = _scalar_int(
            connection.execute(
                "SELECT COUNT(*) AS row_count FROM tasks WHERE project_id=? AND source_type=?",
                (pid, source_type),
            ).fetchone()
        )
        failed_rows = abs(expected_rows - written_rows)
        complete = inserted_rows == expected_rows == written_rows and prepared_rows == scanned_rows
        verification_status = VERIFY_COMPLETE if complete else VERIFY_INCOMPLETE
        if progress:
            progress(
                93,
                f"Xác minh tiến độ · expected={expected_rows:,} · scanned={scanned_rows:,} · "
                f"written={written_rows:,} · failed={failed_rows:,} · {verification_status}",
                str(result.get("sheet_name") or ""),
            )
        if not complete:
            raise ValueError(
                f"Xác minh số dòng tiến độ PostgreSQL CHƯA ĐỦ: expected={expected_rows:,}, "
                f"scanned={scanned_rows:,}, prepared={prepared_rows:,}, inserted={inserted_rows:,}, "
                f"written={written_rows:,}, failed={failed_rows:,}. Transaction đã rollback."
            )

    return {
        "pipeline": PATCH_VERSION,
        "batch_id": batch_id,
        "source_type": source_type,
        "expected_rows": expected_rows,
        "scanned_rows": scanned_rows,
        "prepared_rows": prepared_rows,
        "inserted_rows": inserted_rows,
        "written_rows": written_rows,
        "failed_rows": failed_rows,
        "verification_status": verification_status,
        "verified_postgresql": verification_status == VERIFY_COMPLETE,
        "source_row_count": int(result.get("source_row_count") or expected_rows),
        "skipped_rows": int(result.get("skipped_rows") or 0),
    }
