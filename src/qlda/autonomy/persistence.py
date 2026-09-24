from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any, Callable, Iterator

from .models import DomainEvent


_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS qlda_ai_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id TEXT NOT NULL UNIQUE,
        project_id INTEGER NOT NULL,
        workspace_project_id INTEGER,
        event_type TEXT NOT NULL,
        payload_json TEXT NOT NULL DEFAULT '{}',
        actor TEXT NOT NULL DEFAULT 'system',
        status TEXT NOT NULL DEFAULT 'PENDING',
        occurred_at TEXT NOT NULL,
        processed_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS qlda_ai_action_audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL,
        plan_id TEXT,
        step_id TEXT,
        tool_name TEXT NOT NULL,
        actor TEXT NOT NULL,
        role TEXT NOT NULL,
        risk TEXT NOT NULL,
        mode TEXT NOT NULL,
        status TEXT NOT NULL,
        arguments_json TEXT NOT NULL DEFAULT '{}',
        result_json TEXT NOT NULL DEFAULT '{}',
        error_text TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""",
    """CREATE TABLE IF NOT EXISTS qlda_ai_approvals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL,
        plan_id TEXT NOT NULL,
        step_id TEXT NOT NULL,
        tool_name TEXT NOT NULL,
        requested_by TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'PENDING',
        approved_by TEXT NOT NULL DEFAULT '',
        note TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        decided_at TEXT,
        UNIQUE(project_id, plan_id, step_id)
    )""",
    """CREATE TABLE IF NOT EXISTS qlda_ai_project_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id INTEGER NOT NULL,
        snapshot_type TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        evidence_json TEXT NOT NULL DEFAULT '[]',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""",
)


class AutomationRepository:
    """Durable V7.9+ persistence. Works with sqlite and QLDA's DB-API bridge."""

    def __init__(self, connect: Callable[[], Any]) -> None:
        self._connect = connect

    @contextmanager
    def _conn(self) -> Iterator[Any]:
        resource = self._connect()
        if hasattr(resource, "__enter__"):
            with resource as conn:
                yield conn
            return
        try:
            yield resource
        finally:
            close = getattr(resource, "close", None)
            if callable(close):
                close()

    def ensure_schema(self) -> None:
        with self._conn() as conn:
            for ddl in _SCHEMA:
                conn.execute(ddl)
            commit = getattr(conn, "commit", None)
            if callable(commit):
                commit()

    def save_event(self, event: DomainEvent) -> str:
        self.ensure_schema()
        with self._conn() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO qlda_ai_events
                (event_id,project_id,workspace_project_id,event_type,payload_json,actor,status,occurred_at)
                VALUES(?,?,?,?,?,?,?,?)""",
                (
                    event.event_id,
                    int(event.project_id),
                    event.workspace_project_id,
                    event.event_type,
                    json.dumps(event.payload, ensure_ascii=False, default=str),
                    event.actor,
                    "PENDING",
                    event.occurred_at,
                ),
            )
            commit = getattr(conn, "commit", None)
            if callable(commit):
                commit()
        return event.event_id

    def audit(self, row: dict[str, Any]) -> None:
        self.ensure_schema()
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO qlda_ai_action_audit
                (project_id,plan_id,step_id,tool_name,actor,role,risk,mode,status,arguments_json,result_json,error_text)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    int(row.get("project_id") or 0),
                    str(row.get("plan_id") or ""),
                    str(row.get("step_id") or ""),
                    str(row.get("tool") or row.get("tool_name") or ""),
                    str(row.get("actor") or "system"),
                    str(row.get("role") or "read"),
                    str(row.get("risk") or "low"),
                    str(row.get("mode") or "read_only"),
                    str(row.get("status") or "UNKNOWN"),
                    json.dumps(row.get("arguments") or {}, ensure_ascii=False, default=str),
                    json.dumps(row.get("result") or {}, ensure_ascii=False, default=str),
                    str(row.get("error") or ""),
                ),
            )
            commit = getattr(conn, "commit", None)
            if callable(commit):
                commit()

    def request_approval(self, *, project_id: int, plan_id: str, step_id: str, tool_name: str, requested_by: str) -> None:
        self.ensure_schema()
        with self._conn() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO qlda_ai_approvals
                (project_id,plan_id,step_id,tool_name,requested_by,status)
                VALUES(?,?,?,?,?,'PENDING')""",
                (int(project_id), plan_id, step_id, tool_name, requested_by),
            )
            commit = getattr(conn, "commit", None)
            if callable(commit):
                commit()

    def decide_approval(self, *, project_id: int, plan_id: str, step_id: str, approved: bool, approved_by: str, note: str = "") -> None:
        self.ensure_schema()
        with self._conn() as conn:
            conn.execute(
                """UPDATE qlda_ai_approvals SET status=?,approved_by=?,note=?,decided_at=CURRENT_TIMESTAMP
                WHERE project_id=? AND plan_id=? AND step_id=?""",
                ("APPROVED" if approved else "REJECTED", approved_by, note, int(project_id), plan_id, step_id),
            )
            commit = getattr(conn, "commit", None)
            if callable(commit):
                commit()

    def approved_steps(self, *, project_id: int, plan_id: str) -> set[str]:
        self.ensure_schema()
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT step_id FROM qlda_ai_approvals WHERE project_id=? AND plan_id=? AND status='APPROVED'",
                (int(project_id), plan_id),
            ).fetchall()
        return {str(row[0]) for row in rows}
