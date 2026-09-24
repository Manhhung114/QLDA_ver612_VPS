from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from typing import Any, Iterable


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


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


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(str(value or "[]"))
        return list(parsed) if isinstance(parsed, list) else []
    except Exception:
        return []


class ContractorDataHubRepository:
    """Persistent storage for contractor-isolated external data warehouses.

    Every row is scoped by ``master_project_id`` + ``contractor_id`` +
    ``workspace_project_id``.  PostgreSQL remains one physical database, but the
    logical warehouse boundary is explicit and can be enforced by the existing
    QLDA contractor-access policy.
    """

    def __init__(self, db):
        self.db = db
        self.ensure_schema()

    def ensure_schema(self) -> None:
        statements = [
            """CREATE TABLE IF NOT EXISTS contractor_data_spaces (
                data_space_id TEXT PRIMARY KEY,
                master_project_id INTEGER NOT NULL,
                contractor_id INTEGER NOT NULL,
                workspace_project_id INTEGER NOT NULL,
                contractor_code TEXT DEFAULT '',
                contractor_name TEXT DEFAULT '',
                google_account TEXT DEFAULT '',
                drive_folder_id TEXT DEFAULT '',
                drive_folder_url TEXT DEFAULT '',
                sync_interval_minutes INTEGER DEFAULT 60,
                enabled INTEGER DEFAULT 1,
                last_sync TEXT DEFAULT '',
                last_error TEXT DEFAULT '',
                created_at TEXT DEFAULT '',
                updated_at TEXT DEFAULT '',
                UNIQUE(master_project_id, contractor_id),
                UNIQUE(workspace_project_id)
            )""",
            """CREATE TABLE IF NOT EXISTS contractor_data_sources (
                source_id TEXT PRIMARY KEY,
                data_space_id TEXT NOT NULL,
                master_project_id INTEGER NOT NULL,
                contractor_id INTEGER NOT NULL,
                workspace_project_id INTEGER NOT NULL,
                source_kind TEXT NOT NULL,
                access_mode TEXT DEFAULT 'GOOGLE_OAUTH',
                category TEXT DEFAULT 'AUTO',
                name TEXT NOT NULL,
                external_id TEXT DEFAULT '',
                source_url TEXT DEFAULT '',
                worksheet_names TEXT DEFAULT '[]',
                discover_children INTEGER DEFAULT 0,
                enabled INTEGER DEFAULT 1,
                last_sync TEXT DEFAULT '',
                last_error TEXT DEFAULT '',
                created_at TEXT DEFAULT '',
                updated_at TEXT DEFAULT ''
            )""",
            """CREATE TABLE IF NOT EXISTS contractor_data_records (
                record_key TEXT PRIMARY KEY,
                master_project_id INTEGER NOT NULL,
                contractor_id INTEGER NOT NULL,
                workspace_project_id INTEGER NOT NULL,
                data_space_id TEXT NOT NULL,
                source_id TEXT NOT NULL,
                source_name TEXT DEFAULT '',
                source_kind TEXT DEFAULT '',
                category TEXT DEFAULT '',
                external_item_id TEXT DEFAULT '',
                external_path TEXT DEFAULT '',
                worksheet TEXT DEFAULT '',
                record_type TEXT DEFAULT 'TEXT',
                record_ref TEXT DEFAULT '',
                work_item TEXT DEFAULT '',
                zone TEXT DEFAULT '',
                progress_percent REAL,
                content TEXT DEFAULT '',
                source_row INTEGER DEFAULT 0,
                external_modified_time TEXT DEFAULT '',
                synced_at TEXT DEFAULT ''
            )""",
            """CREATE TABLE IF NOT EXISTS contractor_data_snapshots (
                snapshot_id TEXT PRIMARY KEY,
                master_project_id INTEGER NOT NULL,
                contractor_id INTEGER NOT NULL,
                workspace_project_id INTEGER NOT NULL,
                data_space_id TEXT NOT NULL,
                source_id TEXT NOT NULL,
                snapshot_date TEXT NOT NULL,
                captured_at TEXT NOT NULL,
                checksum TEXT NOT NULL,
                item_count INTEGER DEFAULT 0,
                payload_json TEXT DEFAULT ''
            )""",
            """CREATE TABLE IF NOT EXISTS contractor_data_sync_runs (
                run_id TEXT PRIMARY KEY,
                master_project_id INTEGER NOT NULL,
                contractor_id INTEGER,
                data_space_id TEXT DEFAULT '',
                source_id TEXT DEFAULT '',
                trigger_type TEXT DEFAULT 'MANUAL',
                status TEXT DEFAULT 'RUNNING',
                started_at TEXT DEFAULT '',
                finished_at TEXT DEFAULT '',
                discovered_files INTEGER DEFAULT 0,
                records_written INTEGER DEFAULT 0,
                production_points INTEGER DEFAULT 0,
                message TEXT DEFAULT ''
            )""",
            "CREATE INDEX IF NOT EXISTS idx_cdh_space_project ON contractor_data_spaces(master_project_id,contractor_id)",
            "CREATE INDEX IF NOT EXISTS idx_cdh_source_space ON contractor_data_sources(data_space_id,enabled,source_kind)",
            "CREATE INDEX IF NOT EXISTS idx_cdh_record_project ON contractor_data_records(master_project_id,workspace_project_id,category)",
            "CREATE INDEX IF NOT EXISTS idx_cdh_record_source ON contractor_data_records(source_id,record_type,worksheet)",
            "CREATE INDEX IF NOT EXISTS idx_cdh_snapshot_source ON contractor_data_snapshots(source_id,snapshot_date,captured_at)",
            "CREATE INDEX IF NOT EXISTS idx_cdh_sync_project ON contractor_data_sync_runs(master_project_id,started_at)",
        ]
        with self.db.connect() as connection:
            for sql in statements:
                connection.execute(sql)

    @staticmethod
    def space_id(master_project_id: int, contractor_id: int) -> str:
        return f"cds:{int(master_project_id)}:{int(contractor_id)}"

    def ensure_space(self, master_project_id: int, contractor: dict[str, Any]) -> dict[str, Any]:
        master_id = int(master_project_id)
        contractor_id = int(contractor.get("id") or 0)
        workspace_id = int(contractor.get("workspace_project_id") or 0)
        if contractor_id <= 0 or workspace_id <= 0:
            raise ValueError("Nhà thầu chưa có contractor/workspace ID hợp lệ.")
        sid = self.space_id(master_id, contractor_id)
        stamp = _now()
        with self.db.connect() as connection:
            current = connection.execute(
                "SELECT data_space_id FROM contractor_data_spaces WHERE data_space_id=?",
                (sid,),
            ).fetchone()
            values = (
                str(contractor.get("contractor_code") or ""),
                str(contractor.get("contractor_name") or ""),
                workspace_id,
                stamp,
                sid,
            )
            if current:
                connection.execute(
                    """UPDATE contractor_data_spaces
                    SET contractor_code=?,contractor_name=?,workspace_project_id=?,updated_at=?
                    WHERE data_space_id=?""",
                    values,
                )
            else:
                connection.execute(
                    """INSERT INTO contractor_data_spaces(
                        data_space_id,master_project_id,contractor_id,workspace_project_id,
                        contractor_code,contractor_name,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?)""",
                    (
                        sid, master_id, contractor_id, workspace_id,
                        str(contractor.get("contractor_code") or ""),
                        str(contractor.get("contractor_name") or ""), stamp, stamp,
                    ),
                )
            row = connection.execute(
                "SELECT * FROM contractor_data_spaces WHERE data_space_id=?", (sid,)
            ).fetchone()
        return _rowdict(row)

    def update_space(
        self,
        data_space_id: str,
        *,
        google_account: str | None = None,
        drive_folder_id: str | None = None,
        drive_folder_url: str | None = None,
        sync_interval_minutes: int | None = None,
        enabled: bool | None = None,
    ) -> None:
        sets: list[str] = []
        params: list[Any] = []
        for key, value in (
            ("google_account", google_account),
            ("drive_folder_id", drive_folder_id),
            ("drive_folder_url", drive_folder_url),
        ):
            if value is not None:
                sets.append(f"{key}=?")
                params.append(str(value).strip())
        if sync_interval_minutes is not None:
            sets.append("sync_interval_minutes=?")
            params.append(max(15, min(int(sync_interval_minutes), 1440)))
        if enabled is not None:
            sets.append("enabled=?")
            params.append(1 if enabled else 0)
        if not sets:
            return
        sets.append("updated_at=?")
        params.append(_now())
        params.append(str(data_space_id))
        with self.db.connect() as connection:
            connection.execute(
                "UPDATE contractor_data_spaces SET " + ",".join(sets) + " WHERE data_space_id=?",
                params,
            )

    def list_spaces(
        self,
        master_project_id: int,
        *,
        contractor_ids: Iterable[int] | None = None,
        enabled_only: bool = False,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM contractor_data_spaces WHERE master_project_id=?"
        params: list[Any] = [int(master_project_id)]
        ids = [int(x) for x in (contractor_ids or []) if int(x) > 0]
        if ids:
            sql += " AND contractor_id IN (" + ",".join("?" for _ in ids) + ")"
            params.extend(ids)
        if enabled_only:
            sql += " AND enabled=1"
        sql += " ORDER BY contractor_code,contractor_name"
        with self.db.connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [_rowdict(x) for x in rows]

    def get_space(self, data_space_id: str) -> dict[str, Any]:
        with self.db.connect() as connection:
            row = connection.execute(
                "SELECT * FROM contractor_data_spaces WHERE data_space_id=?", (str(data_space_id),)
            ).fetchone()
        return _rowdict(row)

    def save_source(
        self,
        data_space_id: str,
        *,
        source_id: str = "",
        source_kind: str,
        name: str,
        external_id: str = "",
        source_url: str = "",
        category: str = "AUTO",
        access_mode: str = "GOOGLE_OAUTH",
        worksheet_names: list[str] | None = None,
        discover_children: bool = False,
        enabled: bool = True,
    ) -> str:
        space = self.get_space(data_space_id)
        if not space:
            raise ValueError("Không tìm thấy kho dữ liệu nhà thầu.")
        sid = str(source_id or uuid.uuid4().hex)
        stamp = _now()
        payload = (
            str(source_kind or "SHEET").upper(),
            str(access_mode or "GOOGLE_OAUTH").upper(),
            str(category or "AUTO").upper(),
            str(name or "Nguồn dữ liệu").strip(),
            str(external_id or "").strip(),
            str(source_url or "").strip(),
            json.dumps(list(worksheet_names or []), ensure_ascii=False),
            1 if discover_children else 0,
            1 if enabled else 0,
            stamp,
            sid,
        )
        with self.db.connect() as connection:
            current = connection.execute(
                "SELECT source_id FROM contractor_data_sources WHERE source_id=?", (sid,)
            ).fetchone()
            if current:
                connection.execute(
                    """UPDATE contractor_data_sources SET
                    source_kind=?,access_mode=?,category=?,name=?,external_id=?,source_url=?,
                    worksheet_names=?,discover_children=?,enabled=?,updated_at=? WHERE source_id=?""",
                    payload,
                )
            else:
                connection.execute(
                    """INSERT INTO contractor_data_sources(
                        source_id,data_space_id,master_project_id,contractor_id,workspace_project_id,
                        source_kind,access_mode,category,name,external_id,source_url,worksheet_names,
                        discover_children,enabled,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        sid, str(data_space_id), int(space["master_project_id"]), int(space["contractor_id"]),
                        int(space["workspace_project_id"]), str(source_kind or "SHEET").upper(),
                        str(access_mode or "GOOGLE_OAUTH").upper(), str(category or "AUTO").upper(),
                        str(name or "Nguồn dữ liệu").strip(), str(external_id or "").strip(),
                        str(source_url or "").strip(), json.dumps(list(worksheet_names or []), ensure_ascii=False),
                        1 if discover_children else 0, 1 if enabled else 0, stamp, stamp,
                    ),
                )
        return sid

    def list_sources(self, data_space_id: str, *, enabled_only: bool = False) -> list[dict[str, Any]]:
        sql = "SELECT * FROM contractor_data_sources WHERE data_space_id=?"
        params: list[Any] = [str(data_space_id)]
        if enabled_only:
            sql += " AND enabled=1"
        sql += " ORDER BY source_kind,name,created_at"
        with self.db.connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = _rowdict(row)
            item["worksheet_names"] = _json_list(item.get("worksheet_names"))
            result.append(item)
        return result

    def list_project_sources(self, master_project_id: int) -> list[dict[str, Any]]:
        with self.db.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM contractor_data_sources WHERE master_project_id=? ORDER BY contractor_id,name",
                (int(master_project_id),),
            ).fetchall()
        result = []
        for row in rows:
            item = _rowdict(row)
            item["worksheet_names"] = _json_list(item.get("worksheet_names"))
            result.append(item)
        return result

    def delete_source(self, source_id: str) -> None:
        sid = str(source_id)
        with self.db.connect() as connection:
            connection.execute("DELETE FROM contractor_data_records WHERE source_id=?", (sid,))
            connection.execute("DELETE FROM contractor_data_sources WHERE source_id=?", (sid,))

    def mark_source_error(self, source_id: str, error: str) -> None:
        with self.db.connect() as connection:
            connection.execute(
                "UPDATE contractor_data_sources SET last_error=?,updated_at=? WHERE source_id=?",
                (str(error or "")[:1500], _now(), str(source_id)),
            )

    def replace_records(
        self,
        source: dict[str, Any],
        records: list[dict[str, Any]],
        *,
        snapshot_payload: Any = None,
    ) -> dict[str, Any]:
        sid = str(source.get("source_id") or "")
        if not sid:
            raise ValueError("source_id trống.")
        stamp = _now()
        day = _today()
        master = int(source.get("master_project_id") or 0)
        contractor = int(source.get("contractor_id") or 0)
        workspace = int(source.get("workspace_project_id") or 0)
        data_space_id = str(source.get("data_space_id") or "")
        serializable: list[dict[str, Any]] = []
        with self.db.connect() as connection:
            connection.execute("DELETE FROM contractor_data_records WHERE source_id=?", (sid,))
            for index, raw in enumerate(records):
                row = dict(raw or {})
                raw_key = str(row.get("record_key") or "")
                if not raw_key:
                    raw_key = "|".join([
                        sid,
                        str(row.get("external_item_id") or ""),
                        str(row.get("worksheet") or ""),
                        str(row.get("record_type") or "TEXT"),
                        str(row.get("source_row") or index),
                        str(row.get("zone") or ""),
                    ])
                key = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
                content = str(row.get("content") or "")[:12000]
                params = (
                    key, master, contractor, workspace, data_space_id, sid,
                    str(row.get("source_name") or source.get("name") or ""),
                    str(row.get("source_kind") or source.get("source_kind") or ""),
                    str(row.get("category") or source.get("category") or "AUTO"),
                    str(row.get("external_item_id") or ""),
                    str(row.get("external_path") or ""),
                    str(row.get("worksheet") or ""),
                    str(row.get("record_type") or "TEXT"),
                    str(row.get("record_ref") or ""),
                    str(row.get("work_item") or ""),
                    str(row.get("zone") or ""),
                    row.get("progress_percent"),
                    content,
                    int(row.get("source_row") or 0),
                    str(row.get("external_modified_time") or ""),
                    stamp,
                )
                connection.execute(
                    """INSERT INTO contractor_data_records(
                        record_key,master_project_id,contractor_id,workspace_project_id,data_space_id,source_id,
                        source_name,source_kind,category,external_item_id,external_path,worksheet,record_type,
                        record_ref,work_item,zone,progress_percent,content,source_row,external_modified_time,synced_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    params,
                )
                serializable.append({
                    "record_type": str(row.get("record_type") or "TEXT"),
                    "worksheet": str(row.get("worksheet") or ""),
                    "record_ref": str(row.get("record_ref") or ""),
                    "content": content[:1000],
                    "progress_percent": row.get("progress_percent"),
                    "zone": str(row.get("zone") or ""),
                    "work_item": str(row.get("work_item") or ""),
                })

            snapshot_obj = snapshot_payload if snapshot_payload is not None else serializable
            try:
                payload_json = json.dumps(snapshot_obj, ensure_ascii=False, default=str)
            except Exception:
                payload_json = json.dumps(serializable, ensure_ascii=False)
            checksum = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
            previous = connection.execute(
                """SELECT checksum FROM contractor_data_snapshots
                WHERE source_id=? ORDER BY captured_at DESC LIMIT 1""",
                (sid,),
            ).fetchone()
            prev = str((_rowdict(previous).get("checksum") if previous else "") or "")
            snapshot_created = False
            if checksum != prev:
                snapshot_id = uuid.uuid4().hex
                connection.execute(
                    """INSERT INTO contractor_data_snapshots(
                        snapshot_id,master_project_id,contractor_id,workspace_project_id,data_space_id,
                        source_id,snapshot_date,captured_at,checksum,item_count,payload_json
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        snapshot_id, master, contractor, workspace, data_space_id, sid,
                        day, stamp, checksum, len(records), payload_json[:2_000_000],
                    ),
                )
                snapshot_created = True
            connection.execute(
                "UPDATE contractor_data_sources SET last_sync=?,last_error='',updated_at=? WHERE source_id=?",
                (stamp, stamp, sid),
            )
            connection.execute(
                "UPDATE contractor_data_spaces SET last_sync=?,last_error='',updated_at=? WHERE data_space_id=?",
                (stamp, stamp, data_space_id),
            )
        return {"records": len(records), "snapshot_created": snapshot_created, "checksum": checksum}

    def records(
        self,
        master_project_id: int,
        *,
        workspace_ids: Iterable[int] | None = None,
        category: str = "",
        record_type: str = "",
        limit: int = 5000,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM contractor_data_records WHERE master_project_id=?"
        params: list[Any] = [int(master_project_id)]
        ids = [int(x) for x in (workspace_ids or []) if int(x) > 0]
        if ids:
            sql += " AND workspace_project_id IN (" + ",".join("?" for _ in ids) + ")"
            params.extend(ids)
        if category:
            sql += " AND category=?"
            params.append(str(category).upper())
        if record_type:
            sql += " AND record_type=?"
            params.append(str(record_type).upper())
        sql += " ORDER BY synced_at DESC,contractor_id,source_name,worksheet,source_row LIMIT ?"
        params.append(max(1, min(int(limit), 20000)))
        with self.db.connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [_rowdict(x) for x in rows]

    def snapshots(self, master_project_id: int, *, limit: int = 300) -> list[dict[str, Any]]:
        with self.db.connect() as connection:
            rows = connection.execute(
                """SELECT s.*,d.name AS source_name,d.category,sp.contractor_code,sp.contractor_name
                FROM contractor_data_snapshots s
                LEFT JOIN contractor_data_sources d ON d.source_id=s.source_id
                LEFT JOIN contractor_data_spaces sp ON sp.data_space_id=s.data_space_id
                WHERE s.master_project_id=? ORDER BY s.captured_at DESC LIMIT ?""",
                (int(master_project_id), max(1, min(int(limit), 1000))),
            ).fetchall()
        result = []
        for row in rows:
            item = _rowdict(row)
            item.pop("payload_json", None)
            result.append(item)
        return result

    def begin_sync_run(
        self,
        master_project_id: int,
        *,
        contractor_id: int | None = None,
        data_space_id: str = "",
        source_id: str = "",
        trigger_type: str = "MANUAL",
    ) -> str:
        run_id = uuid.uuid4().hex
        with self.db.connect() as connection:
            connection.execute(
                """INSERT INTO contractor_data_sync_runs(
                    run_id,master_project_id,contractor_id,data_space_id,source_id,trigger_type,status,started_at
                ) VALUES(?,?,?,?,?,?,?,?)""",
                (
                    run_id, int(master_project_id), int(contractor_id) if contractor_id else None,
                    str(data_space_id), str(source_id), str(trigger_type or "MANUAL").upper(),
                    "RUNNING", _now(),
                ),
            )
        return run_id

    def finish_sync_run(
        self,
        run_id: str,
        *,
        status: str,
        discovered_files: int = 0,
        records_written: int = 0,
        production_points: int = 0,
        message: str = "",
    ) -> None:
        with self.db.connect() as connection:
            connection.execute(
                """UPDATE contractor_data_sync_runs SET status=?,finished_at=?,discovered_files=?,
                records_written=?,production_points=?,message=? WHERE run_id=?""",
                (
                    str(status or "DONE").upper(), _now(), int(discovered_files), int(records_written),
                    int(production_points), str(message or "")[:2000], str(run_id),
                ),
            )

    def sync_runs(self, master_project_id: int, *, limit: int = 200) -> list[dict[str, Any]]:
        with self.db.connect() as connection:
            rows = connection.execute(
                """SELECT r.*,sp.contractor_code,sp.contractor_name,d.name AS source_name
                FROM contractor_data_sync_runs r
                LEFT JOIN contractor_data_spaces sp ON sp.data_space_id=r.data_space_id
                LEFT JOIN contractor_data_sources d ON d.source_id=r.source_id
                WHERE r.master_project_id=? ORDER BY r.started_at DESC LIMIT ?""",
                (int(master_project_id), max(1, min(int(limit), 1000))),
            ).fetchall()
        return [_rowdict(x) for x in rows]

    def project_metrics(
        self,
        master_project_id: int,
        *,
        workspace_ids: Iterable[int] | None = None,
    ) -> list[dict[str, Any]]:
        ids = [int(x) for x in (workspace_ids or []) if int(x) > 0]
        sql = """SELECT sp.contractor_id,sp.contractor_code,sp.contractor_name,sp.workspace_project_id,
            sp.last_sync,sp.last_error,
            COUNT(DISTINCT d.source_id) AS source_count,
            COUNT(r.record_key) AS record_count,
            SUM(CASE WHEN r.record_type='PRODUCTION' THEN 1 ELSE 0 END) AS production_points,
            AVG(CASE WHEN r.record_type='PRODUCTION' THEN r.progress_percent ELSE NULL END) AS avg_progress
            FROM contractor_data_spaces sp
            LEFT JOIN contractor_data_sources d ON d.data_space_id=sp.data_space_id AND d.enabled=1
            LEFT JOIN contractor_data_records r ON r.data_space_id=sp.data_space_id
            WHERE sp.master_project_id=?"""
        params: list[Any] = [int(master_project_id)]
        if ids:
            sql += " AND sp.workspace_project_id IN (" + ",".join("?" for _ in ids) + ")"
            params.extend(ids)
        sql += " GROUP BY sp.contractor_id,sp.contractor_code,sp.contractor_name,sp.workspace_project_id,sp.last_sync,sp.last_error ORDER BY sp.contractor_code"
        with self.db.connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [_rowdict(x) for x in rows]

    def migrate_legacy_production_sources(
        self,
        master_project_id: int,
        contractors: Iterable[dict[str, Any]],
    ) -> int:
        """One-way non-destructive import from the earlier production source table."""
        migrated = 0
        with self.db.connect() as connection:
            try:
                connection.execute("SELECT 1 FROM production_sheet_sources LIMIT 1")
            except Exception:
                return 0
            for contractor in contractors:
                workspace = int(contractor.get("workspace_project_id") or 0)
                cid = int(contractor.get("id") or 0)
                if workspace <= 0 or cid <= 0:
                    continue
                space = self.ensure_space(master_project_id, dict(contractor))
                rows = connection.execute(
                    "SELECT * FROM production_sheet_sources WHERE project_id=?",
                    (workspace,),
                ).fetchall()
                for raw in rows:
                    item = _rowdict(raw)
                    legacy_id = str(item.get("source_id") or "")
                    sid = f"legacy:{legacy_id}" if legacy_id else ""
                    existing = connection.execute(
                        "SELECT source_id FROM contractor_data_sources WHERE source_id=?", (sid,)
                    ).fetchone() if sid else None
                    if existing:
                        continue
                    access_mode = str(item.get("data_type") or "GOOGLE_OAUTH").upper()
                    self.save_source(
                        space["data_space_id"],
                        source_id=sid,
                        source_kind="SHEET",
                        name=str(item.get("name") or "Google Sheet"),
                        external_id=str(item.get("spreadsheet_id") or ""),
                        source_url="",
                        category="PRODUCTION",
                        access_mode=access_mode,
                        worksheet_names=_json_list(item.get("worksheet_names")),
                        enabled=bool(item.get("enabled", 1)),
                    )
                    migrated += 1
        return migrated
