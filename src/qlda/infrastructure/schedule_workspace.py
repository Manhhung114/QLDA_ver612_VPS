from __future__ import annotations

"""Source-aware schedule operations; never touch another project's records.

Imported Microsoft Project rows use source_type='mpp'.
Background / previewed Excel rows use source_type='schedule_excel:<sha>'.
Legacy Excel entered through the old inline importer was saved as 'manual' and
cannot be distinguished from actual manual tasks; it is intentionally NOT
deleted by an Excel-only action.
"""

from typing import Any

VALID_DELETE_SCOPES = frozenset({"excel", "mpp", "manual", "all"})


def schedule_source_kind(source_type: Any) -> str:
    source = str(source_type or "").strip().lower()
    if source.startswith("schedule_excel:"):
        return "excel"
    if source == "mpp":
        return "mpp"
    if source in {"manual", ""}:
        return "manual"
    return "other"


def schedule_source_label(source_type: Any) -> str:
    return {
        "excel": "Excel",
        "mpp": "Microsoft Project",
        "manual": "Thủ công / Excel cũ",
        "other": "Nguồn khác",
    }[schedule_source_kind(source_type)]


def source_counts(rows: list[Any]) -> dict[str, int]:
    totals = {"excel": 0, "mpp": 0, "manual": 0, "other": 0}
    for row in rows:
        try:
            source = row["source_type"]
        except (KeyError, TypeError, IndexError):
            source = dict(row).get("source_type", "")
        totals[schedule_source_kind(source)] += 1
    totals["all"] = sum(totals.values())
    return totals


def filtered_schedule_rows(rows: list[Any], source: str) -> list[Any]:
    if source == "all":
        return list(rows)
    if source not in {"excel", "mpp", "manual", "other"}:
        raise ValueError("Nguồn tiến độ không hợp lệ.")
    return [row for row in rows if schedule_source_kind(row["source_type"]) == source]


def _delete_where(scope: str) -> tuple[str, tuple[Any, ...]]:
    if scope == "excel":
        return " AND LOWER(TRIM(COALESCE(source_type,''))) LIKE ?", ("schedule_excel:%",)
    if scope == "mpp":
        return " AND LOWER(TRIM(COALESCE(source_type,'')))=?", ("mpp",)
    if scope == "manual":
        return " AND LOWER(TRIM(COALESCE(source_type,''))) IN ('manual','')", ()
    if scope == "all":
        return "", ()
    raise ValueError("Phạm vi xóa tiến độ không hợp lệ.")


def delete_schedule_scope(
    db: Any,
    project_id: int,
    scope: str,
    *,
    expected_rows: int,
    authorized: bool = False,
) -> int:
    """Delete only the intended source in one transaction, fail closed on races.

    Authorization is an explicit server-side precondition, never inferred from
    untrusted button state. The UI must have also demanded a typed confirmation.
    Files/snapshots, BOQ/VO/IPC, drawing and document tables are not changed.
    """
    if not authorized:
        raise PermissionError("Không có quyền xóa tiến độ.")
    pid = int(project_id)
    if pid <= 0:
        raise ValueError("Workspace dự án không hợp lệ.")
    where, params = _delete_where(scope)
    expected = int(expected_rows)
    if expected <= 0:
        raise ValueError("Không có hạng mục thuộc phạm vi xóa.")
    conditions = " WHERE project_id=?" + where
    binds = (pid,) + params

    with db.connect() as conn:
        try:
            count_row = conn.execute("SELECT COUNT(*) FROM tasks" + conditions, binds).fetchone()
            existing = int(count_row[0] if count_row is not None else 0)
            if existing != expected:
                raise ValueError(
                    f"Số công việc đã thay đổi ({expected} → {existing}). "
                    "Hãy làm mới và xác nhận lại để tránh xóa nhầm."
                )
            cursor = conn.execute("DELETE FROM tasks" + conditions, binds)
            deleted = int(cursor.rowcount)
            if deleted != expected:
                raise RuntimeError(f"Xóa chưa đủ {deleted}/{expected} hạng mục; đã rollback.")
            if scope in {"mpp", "all"}:
                conn.execute(
                    "UPDATE projects SET source_mpp_path='',last_sync='' WHERE id=?",
                    (pid,),
                )
            remaining = conn.execute("SELECT COUNT(*) FROM tasks" + conditions, binds).fetchone()
            if int(remaining[0]) != 0:
                raise RuntimeError("Phạm vi tiến độ chưa được xóa hết; đã rollback.")
        except BaseException:
            conn.rollback()
            raise
    return deleted


__all__ = [
    "schedule_source_kind", "schedule_source_label", "source_counts",
    "filtered_schedule_rows", "delete_schedule_scope",
]
