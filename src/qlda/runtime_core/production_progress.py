from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from qlda.application.google_sheets.service import ProductionRow


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


class ProductionProgressStore:
    """Project-scoped Google Sheets source registry + current/history snapshots."""

    def __init__(self, db):
        self.db = db
        self.ensure_tables()

    def ensure_tables(self) -> None:
        statements = [
            """CREATE TABLE IF NOT EXISTS production_sheet_sources (
                source_id TEXT PRIMARY KEY,
                project_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                spreadsheet_id TEXT NOT NULL,
                spreadsheet_title TEXT DEFAULT '',
                worksheet_names TEXT DEFAULT '[]',
                data_type TEXT DEFAULT 'PRODUCTION_PROGRESS',
                enabled INTEGER DEFAULT 1,
                last_sync TEXT DEFAULT '',
                last_error TEXT DEFAULT '',
                created_at TEXT DEFAULT '',
                updated_at TEXT DEFAULT ''
            )""",
            """CREATE TABLE IF NOT EXISTS production_progress_current (
                row_key TEXT PRIMARY KEY,
                project_id INTEGER NOT NULL,
                source_id TEXT NOT NULL,
                worksheet TEXT NOT NULL,
                work_item TEXT NOT NULL,
                zone TEXT NOT NULL,
                progress_percent REAL DEFAULT 0,
                source_row INTEGER DEFAULT 0,
                synced_at TEXT DEFAULT ''
            )""",
            """CREATE TABLE IF NOT EXISTS production_progress_history (
                history_key TEXT PRIMARY KEY,
                project_id INTEGER NOT NULL,
                source_id TEXT NOT NULL,
                worksheet TEXT NOT NULL,
                work_item TEXT NOT NULL,
                zone TEXT NOT NULL,
                progress_percent REAL DEFAULT 0,
                source_row INTEGER DEFAULT 0,
                snapshot_date TEXT NOT NULL,
                synced_at TEXT DEFAULT ''
            )""",
        ]
        with self.db.connect() as conn:
            for sql in statements:
                conn.execute(sql)

    def list_sources(self, project_id: int) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM production_sheet_sources WHERE project_id=? ORDER BY name, created_at",
                (int(project_id),),
            ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            try:
                item["worksheet_names"] = list(json.loads(item.get("worksheet_names") or "[]"))
            except Exception:
                item["worksheet_names"] = []
            out.append(item)
        return out

    def save_source(
        self,
        project_id: int,
        *,
        source_id: str = "",
        name: str,
        spreadsheet_id: str,
        spreadsheet_title: str,
        worksheet_names: list[str],
        data_type: str = "PRODUCTION_PROGRESS",
        enabled: bool = True,
    ) -> str:
        source_id = str(source_id or uuid.uuid4().hex)
        now = _now()
        mode = str(data_type or "PRODUCTION_PROGRESS").strip().upper()
        with self.db.connect() as conn:
            current = conn.execute(
                "SELECT source_id FROM production_sheet_sources WHERE source_id=? AND project_id=?",
                (source_id, int(project_id)),
            ).fetchone()
            if current:
                conn.execute(
                    """UPDATE production_sheet_sources
                    SET name=?, spreadsheet_id=?, spreadsheet_title=?, worksheet_names=?, data_type=?, enabled=?, updated_at=?
                    WHERE source_id=? AND project_id=?""",
                    (
                        str(name).strip(), str(spreadsheet_id).strip(), str(spreadsheet_title or "").strip(),
                        json.dumps(list(worksheet_names), ensure_ascii=False), mode, 1 if enabled else 0, now,
                        source_id, int(project_id),
                    ),
                )
            else:
                conn.execute(
                    """INSERT INTO production_sheet_sources
                    (source_id, project_id, name, spreadsheet_id, spreadsheet_title, worksheet_names, data_type, enabled, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        source_id, int(project_id), str(name).strip(), str(spreadsheet_id).strip(),
                        str(spreadsheet_title or "").strip(), json.dumps(list(worksheet_names), ensure_ascii=False),
                        mode, 1 if enabled else 0, now, now,
                    ),
                )
        return source_id

    def delete_source(self, project_id: int, source_id: str) -> None:
        with self.db.connect() as conn:
            conn.execute("DELETE FROM production_progress_current WHERE project_id=? AND source_id=?", (int(project_id), source_id))
            conn.execute("DELETE FROM production_sheet_sources WHERE project_id=? AND source_id=?", (int(project_id), source_id))

    def replace_current(self, project_id: int, source_id: str, rows: list[ProductionRow]) -> None:
        now = _now()
        day = _today()
        with self.db.connect() as conn:
            conn.execute("DELETE FROM production_progress_current WHERE project_id=? AND source_id=?", (int(project_id), source_id))
            for row in rows:
                row_key = f"{source_id}|{row.worksheet}|{row.source_row}|{row.zone}"
                history_key = f"{row_key}|{day}"
                params = (
                    row_key, int(project_id), source_id, row.worksheet, row.work_item, row.zone,
                    float(row.progress_percent), int(row.source_row), now,
                )
                conn.execute(
                    """INSERT INTO production_progress_current
                    (row_key, project_id, source_id, worksheet, work_item, zone, progress_percent, source_row, synced_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    params,
                )
                existing = conn.execute(
                    "SELECT history_key FROM production_progress_history WHERE history_key=?", (history_key,)
                ).fetchone()
                if existing:
                    conn.execute(
                        "UPDATE production_progress_history SET progress_percent=?, synced_at=? WHERE history_key=?",
                        (float(row.progress_percent), now, history_key),
                    )
                else:
                    conn.execute(
                        """INSERT INTO production_progress_history
                        (history_key, project_id, source_id, worksheet, work_item, zone, progress_percent, source_row, snapshot_date, synced_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            history_key, int(project_id), source_id, row.worksheet, row.work_item, row.zone,
                            float(row.progress_percent), int(row.source_row), day, now,
                        ),
                    )
            conn.execute(
                "UPDATE production_sheet_sources SET last_sync=?, last_error='', updated_at=? WHERE source_id=? AND project_id=?",
                (now, now, source_id, int(project_id)),
            )

    def mark_error(self, project_id: int, source_id: str, error: str) -> None:
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE production_sheet_sources SET last_error=?, updated_at=? WHERE source_id=? AND project_id=?",
                (str(error)[:1000], _now(), source_id, int(project_id)),
            )

    def current_rows(self, project_id: int) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                """SELECT c.*, s.name AS source_name, s.spreadsheet_title
                FROM production_progress_current c
                JOIN production_sheet_sources s ON s.source_id=c.source_id
                WHERE c.project_id=? ORDER BY c.worksheet, c.work_item, c.zone""",
                (int(project_id),),
            ).fetchall()
        return [dict(x) for x in rows]

    def history_rows(self, project_id: int) -> list[dict[str, Any]]:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM production_progress_history WHERE project_id=? ORDER BY snapshot_date, worksheet, work_item, zone",
                (int(project_id),),
            ).fetchall()
        return [dict(x) for x in rows]
