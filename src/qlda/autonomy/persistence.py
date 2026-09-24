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
    "CREATE INDEX IF NOT EXISTS idx_qlda_ai_snapshot_project ON qlda_ai_project_snapshots(project_id,snapshot_type,created_at)",
    "CREATE INDEX IF NOT EXISTS idx_qlda_ai_events_project ON qlda_ai_events(project_id,status,occurred_at)",
)


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

    def mark_event_processed(self, event_id: str, *, status: str = "PROCESSED") -> None:
        self.ensure_schema()
        with self._conn() as conn:
            conn.execute(
                "UPDATE qlda_ai_events SET status=?,processed_at=CURRENT_TIMESTAMP WHERE event_id=?",
                (str(status), str(event_id)),
            )
            commit = getattr(conn, "commit", None)
            if callable(commit):
                commit()

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
        out: set[str] = set()
        for row in rows:
            item = _rowdict(row)
            value = item.get("step_id")
            if value is None:
                try:
                    value = row[0]
                except Exception:
                    value = None
            if value is not None:
                out.add(str(value))
        return out

    def save_snapshot(
        self,
        *,
        project_id: int,
        snapshot_type: str,
        payload: Any,
        evidence: Any = None,
    ) -> int:
        self.ensure_schema()
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO qlda_ai_project_snapshots(project_id,snapshot_type,payload_json,evidence_json)
                VALUES(?,?,?,?)""",
                (
                    int(project_id),
                    str(snapshot_type),
                    json.dumps(payload, ensure_ascii=False, default=str),
                    json.dumps(evidence or [], ensure_ascii=False, default=str),
                ),
            )
            commit = getattr(conn, "commit", None)
            if callable(commit):
                commit()
            try:
                return int(cur.lastrowid or 0)
            except Exception:
                return 0

    def latest_snapshot(self, *, project_id: int, snapshot_type: str) -> dict[str, Any]:
        self.ensure_schema()
        with self._conn() as conn:
            row = conn.execute(
                """SELECT * FROM qlda_ai_project_snapshots
                WHERE project_id=? AND snapshot_type=? ORDER BY created_at DESC,id DESC LIMIT 1""",
                (int(project_id), str(snapshot_type)),
            ).fetchone()
        item = _rowdict(row)
        for key in ("payload_json", "evidence_json"):
            if key in item:
                default = "{}" if key == "payload_json" else "[]"
                try:
                    item[key.removesuffix("_json")] = json.loads(str(item.get(key) or default))
                except Exception:
                    item[key.removesuffix("_json")] = {} if key == "payload_json" else []
        return item

    def pending_approvals(self, *, project_id: int, limit: int = 100) -> list[dict[str, Any]]:
        self.ensure_schema()
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT * FROM qlda_ai_approvals WHERE project_id=? AND status='PENDING'
                ORDER BY created_at,id LIMIT ?""",
                (int(project_id), max(1, min(int(limit), 500))),
            ).fetchall()
        return [_rowdict(row) for row in rows]
