from __future__ import annotations

"""PostgreSQL compatibility for Closed Loop text timestamp persistence.

The Closed Loop schema intentionally stores timestamps as TEXT so it stays
portable across the SQLite test/runtime path and the PostgreSQL VPS adapter.
PostgreSQL does not allow COALESCE(text_column, CURRENT_TIMESTAMP) because the
arguments have different SQL types.  Keep all Closed Loop DML timestamp values
as bound text parameters instead of mixing TEXT and timestamptz expressions.
"""

from datetime import datetime, timezone
from typing import Any

from .loop_engine import ClosedLoopRepository, _json, _rowdict


def _timestamp_text() -> str:
    """Return one sortable UTC timestamp representation for TEXT columns."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _postgres_safe_save_loop(self: ClosedLoopRepository, row: dict[str, Any]) -> str:
    self.ensure_schema()
    loop_id = str(row.get("loop_id") or "").strip()
    if not loop_id:
        raise ValueError("loop_id là bắt buộc")

    project_id = int(row.get("project_id") or 0)
    if project_id <= 0:
        raise ValueError("project_id không hợp lệ")

    workspace_id = int(row.get("workspace_project_id") or project_id)
    status = str(row.get("status") or "OPEN")
    current_stage = str(row.get("current_stage") or "SENSE")
    actor = str(row.get("actor") or "system")
    now_text = _timestamp_text()

    payload = (
        workspace_id,
        status,
        current_stage,
        actor,
        float(row.get("baseline_health") or 0),
        float(row.get("latest_health") or 0),
        max(1, int(row.get("cycle_count") or 1)),
        _json(row.get("sensed") or {}),
        _json(row.get("analysis") or {}),
        _json(row.get("recommendations") or []),
        _json(row.get("actions") or []),
        _json(row.get("verification") or {}),
        _json(row.get("learning") or {}),
    )

    with self._conn() as conn:
        existing = conn.execute(
            "SELECT id,closed_at FROM qlda_engineering_loops WHERE loop_id=? AND project_id=? LIMIT 1",
            (loop_id, project_id),
        ).fetchone()
        existing_closed = str(_rowdict(existing).get("closed_at") or "").strip() or None
        closed_at = (existing_closed or now_text) if status == "CLOSED" else None

        if existing:
            conn.execute(
                """UPDATE qlda_engineering_loops SET
                workspace_project_id=?,status=?,current_stage=?,actor=?,baseline_health=?,latest_health=?,cycle_count=?,
                sensed_json=?,analysis_json=?,recommendations_json=?,actions_json=?,verification_json=?,learning_json=?,
                updated_at=?,closed_at=?
                WHERE loop_id=? AND project_id=?""",
                (*payload, now_text, closed_at, loop_id, project_id),
            )
        else:
            conn.execute(
                """INSERT INTO qlda_engineering_loops
                (loop_id,project_id,workspace_project_id,status,current_stage,actor,baseline_health,latest_health,cycle_count,
                sensed_json,analysis_json,recommendations_json,actions_json,verification_json,learning_json,
                created_at,updated_at,closed_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (loop_id, project_id, *payload, now_text, now_text, closed_at),
            )

        commit = getattr(conn, "commit", None)
        if callable(commit):
            commit()
    return loop_id


def install_closed_loop_postgres_compat() -> None:
    """Install the text-timestamp-safe repository write once per interpreter."""
    current = ClosedLoopRepository.save_loop
    if getattr(current, "_qlda_postgres_text_timestamp_safe", False):
        return
    _postgres_safe_save_loop._qlda_postgres_text_timestamp_safe = True  # type: ignore[attr-defined]
    ClosedLoopRepository.save_loop = _postgres_safe_save_loop  # type: ignore[method-assign]


__all__ = ["install_closed_loop_postgres_compat"]
