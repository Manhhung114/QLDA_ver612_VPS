from __future__ import annotations

import importlib.abc
import importlib.machinery
import os
import re
import shutil
import sqlite3
import sys
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable

import cloud_db as _cloud_db

V622_SCHEMA_VERSION = "6.22"
_SQLITE_CLOUD_DATABASE = _cloud_db.CloudDatabase

# Parent -> child order is important when importing a legacy SQLite backup.
TABLE_ORDER = (
    "projects",
    "tasks",
    "documents",
    "document_attachments",
    "drawings",
    "drawing_attachments",
    "approval_workflows",
    "approval_steps",
    "approval_history",
    "cost_budgets",
    "payment_tracking",
    "cost_variations",
    "material_master",
    "procurement_schedule",
    "inventory_inspection",
    "legal_documents",
    "legal_sync_log",
)
_ID_TABLES = set(TABLE_ORDER)

# LegalRepository lives outside cloud_db.py, therefore its two tables are created
# here as well so backup/restore and AI context work before the Legal tab is opened.
LEGAL_SQLITE_SCHEMA = r"""
CREATE TABLE IF NOT EXISTS legal_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT DEFAULT '',
    number TEXT DEFAULT '',
    title TEXT NOT NULL,
    issuer TEXT DEFAULT '',
    issue_date TEXT DEFAULT '',
    effective_date TEXT DEFAULT '',
    expiry_date TEXT DEFAULT '',
    status TEXT DEFAULT '',
    field TEXT DEFAULT '',
    source_name TEXT DEFAULT '',
    source_url TEXT NOT NULL UNIQUE,
    is_draft INTEGER DEFAULT 0,
    note TEXT DEFAULT '',
    online_updated_at TEXT DEFAULT '',
    created_at TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_legal_category ON legal_documents(category);
CREATE INDEX IF NOT EXISTS idx_legal_status ON legal_documents(status);
CREATE TABLE IF NOT EXISTS legal_sync_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_name TEXT NOT NULL,
    sync_time TEXT NOT NULL,
    status TEXT NOT NULL,
    found_count INTEGER DEFAULT 0,
    added_count INTEGER DEFAULT 0,
    updated_count INTEGER DEFAULT 0,
    message TEXT DEFAULT ''
);
"""

POSTGRES_EXTRA_SCHEMA = r"""
CREATE TABLE IF NOT EXISTS qlda_meta (
    key TEXT PRIMARY KEY,
    value TEXT DEFAULT '',
    updated_at TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_tasks_project_source_order
    ON tasks(project_id, source_type, source_task_id, start_date, id);
CREATE INDEX IF NOT EXISTS idx_documents_project_type_date
    ON documents(project_id, doc_type, issue_date, id);
CREATE INDEX IF NOT EXISTS idx_document_attachments_document
    ON document_attachments(document_id, id);
CREATE INDEX IF NOT EXISTS idx_drawings_project_type_date
    ON drawings(project_id, drawing_type, received_date, id);
CREATE INDEX IF NOT EXISTS idx_drawing_attachments_drawing
    ON drawing_attachments(drawing_id, id);
CREATE INDEX IF NOT EXISTS idx_approval_workflows_lookup
    ON approval_workflows(project_id, record_kind, subtype, record_id);
CREATE INDEX IF NOT EXISTS idx_approval_workflows_record_fast
    ON approval_workflows(project_id, record_kind, record_id, current_stage, id);
CREATE INDEX IF NOT EXISTS idx_approval_steps_workflow_order
    ON approval_steps(workflow_id, stage_order, stage_code);
"""


def _normalize_database_url(value: str) -> str:
    value = str(value or "").strip()
    if value.startswith("postgres://"):
        value = "postgresql://" + value[len("postgres://"):]
    return value


def resolve_database_url() -> str:
    """Read PostgreSQL connection string from env or Streamlit root secrets."""
    for name in ("DATABASE_URL", "QLDA_DATABASE_URL", "POSTGRES_URL"):
        value = str(os.environ.get(name, "") or "").strip()
        if value:
            return _normalize_database_url(value)
    try:
        import streamlit as st

        for name in ("DATABASE_URL", "QLDA_DATABASE_URL", "POSTGRES_URL"):
            try:
                value = str(st.secrets.get(name, "") or "").strip()
            except Exception:
                value = ""
            if value:
                return _normalize_database_url(value)
    except Exception:
        pass
    return ""


def postgres_enabled() -> bool:
    return bool(resolve_database_url())


def _safe_identifier(name: str) -> str:
    value = str(name or "")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError(f"Tên bảng/cột không hợp lệ: {value!r}")
    return value


def _replace_qmarks(sql: str) -> str:
    """Translate SQLite qmark placeholders to psycopg %s outside quoted text."""
    out: list[str] = []
    single = False
    double = False
    i = 0
    while i < len(sql):
        ch = sql[i]
        if ch == "'" and not double:
            out.append(ch)
            if single and i + 1 < len(sql) and sql[i + 1] == "'":
                out.append("'")
                i += 2
                continue
            single = not single
        elif ch == '"' and not single:
            double = not double
            out.append(ch)
        elif ch == "?" and not single and not double:
            out.append("%s")
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def _translate_sqlite_ddl(sql: str) -> str:
    """Translate the small SQLite DDL subset used by QLDA to PostgreSQL."""
    text = str(sql or "")
    text = re.sub(
        r"\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b",
        "SERIAL PRIMARY KEY",
        text,
        flags=re.I,
    )
    text = re.sub(r"\bAUTOINCREMENT\b", "", text, flags=re.I)
    text = re.sub(r"\bBLOB\b", "BYTEA", text, flags=re.I)
    return text


def _normalize_param(value: Any) -> Any:
    # sqlite3.Binary(bytes) is a memoryview; psycopg accepts bytes directly.
    if isinstance(value, memoryview):
        return bytes(value)
    if isinstance(value, bytearray):
        return bytes(value)
    return value


def _normalize_params(params: Any) -> Any:
    if params is None:
        return None
    if isinstance(params, dict):
        return {k: _normalize_param(v) for k, v in params.items()}
    return tuple(_normalize_param(v) for v in params)


def _insert_table(sql: str) -> str:
    m = re.search(r"\bINSERT\s+(?:OR\s+\w+\s+)?INTO\s+([A-Za-z_][A-Za-z0-9_]*)", sql, re.I)
    return str(m.group(1)).lower() if m else ""


def _rewrite_sql(sql: str, *, return_insert_id: bool = True) -> tuple[str, bool]:
    raw = _translate_sqlite_ddl(str(sql or "")).strip()
    is_ignore = bool(re.search(r"\bINSERT\s+OR\s+IGNORE\b", raw, re.I))
    raw = re.sub(r"\bINSERT\s+OR\s+IGNORE\b", "INSERT", raw, flags=re.I)
    table = _insert_table(raw)
    text = _replace_qmarks(raw).strip()
    if is_ignore and "ON CONFLICT" not in text.upper():
        text = text.rstrip(";") + " ON CONFLICT DO NOTHING"
    has_returning = bool(re.search(r"\bRETURNING\b", text, re.I))
    wants_id = bool(return_insert_id and table in _ID_TABLES and not has_returning)
    if wants_id:
        text = text.rstrip(";") + " RETURNING id"
    return text, wants_id


def _split_sql_script(script: str) -> list[str]:
    out: list[str] = []
    buf: list[str] = []
    single = False
    double = False
    i = 0
    text = str(script or "")
    while i < len(text):
        ch = text[i]
        if ch == "'" and not double:
            if single and i + 1 < len(text) and text[i + 1] == "'":
                buf.extend(["'", "'"])
                i += 2
                continue
            single = not single
        elif ch == '"' and not single:
            double = not double
        if ch == ";" and not single and not double:
            statement = "".join(buf).strip()
            if statement:
                out.append(statement)
            buf = []
        else:
            buf.append(ch)
        i += 1
    statement = "".join(buf).strip()
    if statement:
        out.append(statement)
    return out


class CompatRow:
    """sqlite3.Row-like object supporting both row[0] and row['column']."""

    __slots__ = ("_names", "_values", "_index")

    def __init__(self, names: Iterable[str], values: Iterable[Any]):
        self._names = tuple(str(x) for x in names)
        self._values = tuple(values)
        self._index = {name: i for i, name in enumerate(self._names)}

    def keys(self):
        return list(self._names)

    def items(self):
        return [(name, self[name]) for name in self._names]

    def get(self, key, default=None):
        try:
            return self[key]
        except (KeyError, IndexError, TypeError):
            return default

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        return self._values[self._index[str(key)]]

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def __repr__(self):
        return repr(dict(self.items()))


class _CompatCursor:
    def __init__(self, cursor, *, lastrowid: int | None = None, consumed: bool = False):
        self._cursor = cursor
        self.lastrowid = lastrowid
        self._consumed = consumed

    @property
    def rowcount(self):
        return self._cursor.rowcount

    @property
    def description(self):
        return self._cursor.description

    def _names(self) -> list[str]:
        desc = self._cursor.description or []
        return [str(getattr(item, "name", None) or item[0]) for item in desc]

    def _wrap(self, row):
        if row is None:
            return None
        if isinstance(row, CompatRow):
            return row
        if isinstance(row, dict):
            return CompatRow(row.keys(), row.values())
        return CompatRow(self._names(), row)

    def fetchone(self):
        if self._consumed:
            return None
        return self._wrap(self._cursor.fetchone())

    def fetchall(self):
        if self._consumed:
            return []
        return [self._wrap(row) for row in self._cursor.fetchall()]

    def __iter__(self):
        if self._consumed:
            return iter(())
        return (self._wrap(row) for row in self._cursor)

    def close(self):
        try:
            self._cursor.close()
        except Exception:
            pass


class _CompatConnection:
    """SQLite-shaped connection backed by a psycopg connection."""

    def __init__(self, raw):
        self._raw = raw

    def execute(self, sql: str, params: Any = None):
        text = str(sql or "").strip()
        if text.upper().startswith("PRAGMA"):
            cur = self._raw.cursor()
            cur.execute("SELECT 1 WHERE FALSE")
            return _CompatCursor(cur)
        rewritten, wants_id = _rewrite_sql(text, return_insert_id=True)
        cur = self._raw.cursor()
        values = _normalize_params(params)
        if values is None:
            cur.execute(rewritten)
        else:
            cur.execute(rewritten, values)
        last_id = None
        consumed = False
        if wants_id:
            row = cur.fetchone()
            if row:
                last_id = int(row[0])
            consumed = True
        return _CompatCursor(cur, lastrowid=last_id, consumed=consumed)

    def executemany(self, sql: str, seq_of_params):
        rewritten, _ = _rewrite_sql(sql, return_insert_id=False)
        cur = self._raw.cursor()
        cur.executemany(rewritten, [_normalize_params(values) for values in seq_of_params])
        return _CompatCursor(cur)

    def executescript(self, script: str):
        last = None
        for statement in _split_sql_script(script):
            last = self.execute(statement)
        return last

    def commit(self):
        return self._raw.commit()

    def rollback(self):
        return self._raw.rollback()

    def close(self):
        # Pool context owns the actual psycopg connection lifecycle.
        return None


_POOL_LOCK = threading.RLock()
_POOLS: dict[str, Any] = {}


def _pool_limits() -> tuple[int, int, float]:
    try:
        min_size = max(0, min(2, int(os.environ.get("QLDA_PG_POOL_MIN", "0"))))
    except Exception:
        min_size = 0
    try:
        max_size = max(1, min(10, int(os.environ.get("QLDA_PG_POOL_MAX", "5"))))
    except Exception:
        max_size = 5
    try:
        timeout = max(5.0, min(60.0, float(os.environ.get("QLDA_PG_POOL_TIMEOUT", "20"))))
    except Exception:
        timeout = 20.0
    return min_size, max(max_size, min_size or 1), timeout


def _get_pool(url: str):
    with _POOL_LOCK:
        pool = _POOLS.get(url)
        if pool is not None:
            return pool
        try:
            from psycopg_pool import ConnectionPool
        except Exception as exc:
            raise RuntimeError(
                "Thiếu psycopg-pool. Hãy cài psycopg[binary] và psycopg-pool trong requirements.txt."
            ) from exc
        min_size, max_size, timeout = _pool_limits()
        pool = ConnectionPool(
            conninfo=url,
            min_size=min_size,
            max_size=max_size,
            timeout=timeout,
            open=True,
        )
        _POOLS[url] = pool
        return pool


class _PGConnectionContext:
    def __init__(self, url: str):
        self.url = _normalize_database_url(url)
        self._cm = None
        self._raw = None

    def __enter__(self):
        if not self.url:
            raise RuntimeError("DATABASE_URL đang trống.")
        self._cm = _get_pool(self.url).connection()
        self._raw = self._cm.__enter__()
        # Transaction/connection poolers work more reliably without prepared
        # statement caching at the client protocol layer.
        try:
            self._raw.prepare_threshold = None
        except Exception:
            pass
        return _CompatConnection(self._raw)

    def __exit__(self, exc_type, exc, tb):
        return self._cm.__exit__(exc_type, exc, tb)


def _table_exists(c: _CompatConnection, table: str) -> bool:
    table = _safe_identifier(table)
    row = c.execute(
        """SELECT EXISTS(
               SELECT 1 FROM information_schema.tables
               WHERE table_schema=current_schema() AND table_name=?
           ) AS ok""",
        (table,),
    ).fetchone()
    return bool(row and row[0])


def _table_columns(c: _CompatConnection, table: str) -> list[str]:
    table = _safe_identifier(table)
    rows = c.execute(
        """SELECT column_name FROM information_schema.columns
           WHERE table_schema=current_schema() AND table_name=? ORDER BY ordinal_position""",
        (table,),
    ).fetchall()
    return [str(row[0]) for row in rows]


def _ensure_legal_and_extra_schema(c: _CompatConnection) -> None:
    c.executescript(LEGAL_SQLITE_SCHEMA)
    c.executescript(POSTGRES_EXTRA_SCHEMA)
    c.execute(
        """INSERT INTO qlda_meta(key,value,updated_at) VALUES('schema_version',?,CURRENT_TIMESTAMP::text)
           ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value,updated_at=EXCLUDED.updated_at""",
        (V622_SCHEMA_VERSION,),
    )


def _reset_sequence(c: _CompatConnection, table: str) -> None:
    table = _safe_identifier(table)
    seq = c.execute("SELECT pg_get_serial_sequence(?, 'id')", (table,)).fetchone()
    if not seq or not seq[0]:
        return
    row = c.execute(f"SELECT MAX(id) FROM {table}").fetchone()
    max_id = int(row[0]) if row and row[0] is not None else 0
    c.execute(
        "SELECT setval(CAST(? AS regclass), ?, ?)",
        (str(seq[0]), max(1, max_id), bool(max_id)),
    )


def _sqlite_columns(c: sqlite3.Connection, table: str) -> list[str]:
    table = _safe_identifier(table)
    return [str(row[1]) for row in c.execute(f"PRAGMA table_info({table})").fetchall()]


def _truncate_business_tables(c: _CompatConnection) -> None:
    existing = [table for table in TABLE_ORDER if _table_exists(c, table)]
    if existing:
        c.execute(
            "TRUNCATE TABLE "
            + ",".join(_safe_identifier(table) for table in existing)
            + " RESTART IDENTITY CASCADE"
        )


def _import_sqlite_connection(
    target: _CompatConnection,
    source: sqlite3.Connection,
    *,
    replace: bool = True,
) -> dict[str, int]:
    source.row_factory = sqlite3.Row
    _ensure_legal_and_extra_schema(target)
    if replace:
        _truncate_business_tables(target)
    source_tables = {
        str(row[0])
        for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    stats: dict[str, int] = {}
    for table in TABLE_ORDER:
        if table not in source_tables or not _table_exists(target, table):
            continue
        source_cols = _sqlite_columns(source, table)
        target_cols = set(_table_columns(target, table))
        cols = [col for col in source_cols if col in target_cols]
        if not cols:
            continue
        rows = source.execute(f"SELECT {','.join(cols)} FROM {table} ORDER BY id").fetchall()
        stats[table] = len(rows)
        if not rows:
            continue
        sql = f"INSERT INTO {table}({','.join(cols)}) VALUES({','.join('?' for _ in cols)})"
        payload = [tuple(_normalize_param(row[col]) for col in cols) for row in rows]
        for start in range(0, len(payload), 300):
            target.executemany(sql, payload[start:start + 300])
        if "id" in cols:
            _reset_sequence(target, table)
    return stats


def import_sqlite_bytes(
    data: bytes,
    *,
    replace: bool = True,
    url: str | None = None,
) -> dict[str, int]:
    """Import an old QLDA SQLite .db backup into PostgreSQL."""
    raw = bytes(data or b"")
    if not raw.startswith(b"SQLite format 3\x00"):
        raise ValueError("File không phải SQLite database hợp lệ.")
    database_url = _normalize_database_url(url or resolve_database_url())
    if not database_url:
        raise RuntimeError("Chưa cấu hình DATABASE_URL cho PostgreSQL.")

    fd, temp_path = tempfile.mkstemp(prefix="qlda_v622_import_", suffix=".db")
    os.close(fd)
    try:
        Path(temp_path).write_bytes(raw)
        source = sqlite3.connect(temp_path)
        try:
            source.row_factory = sqlite3.Row
            check = source.execute("PRAGMA integrity_check").fetchone()[0]
            if check != "ok":
                raise ValueError(f"SQLite integrity_check thất bại: {check}")
            with _PGConnectionContext(database_url) as target:
                # Core schema comes from the existing CloudDatabase code, keeping
                # V6.22 automatically aligned with all V6.21 fields/workflows.
                pg_db = PostgresCloudDatabase.__new__(PostgresCloudDatabase)
                pg_db.path = Path("qlda_cloud.db")
                pg_db.database_url = database_url
                _SQLITE_CLOUD_DATABASE.create_tables(pg_db)
                _SQLITE_CLOUD_DATABASE.migrate(pg_db)
                _ensure_legal_and_extra_schema(target)
                return _import_sqlite_connection(target, source, replace=replace)
        finally:
            source.close()
    finally:
        try:
            os.remove(temp_path)
        except OSError:
            pass


def _portable_sqlite_backup(database_url: str) -> bytes:
    """Export PostgreSQL back to a real SQLite file for backup/rollback."""
    temp_dir = Path(tempfile.mkdtemp(prefix="qlda_v622_backup_"))
    path = temp_dir / "qlda_backup.db"
    try:
        legacy = _SQLITE_CLOUD_DATABASE(path)
        del legacy
        sq = sqlite3.connect(path)
        try:
            sq.row_factory = sqlite3.Row
            sq.executescript(LEGAL_SQLITE_SCHEMA)
            sq.execute("PRAGMA foreign_keys=OFF")
            with _PGConnectionContext(database_url) as source:
                for table in TABLE_ORDER:
                    if not _table_exists(source, table):
                        continue
                    target_cols = set(_sqlite_columns(sq, table))
                    cols = [col for col in _table_columns(source, table) if col in target_cols]
                    if not cols:
                        continue
                    rows = source.execute(f"SELECT {','.join(cols)} FROM {table} ORDER BY id").fetchall()
                    if not rows:
                        continue
                    sql = f"INSERT INTO {table}({','.join(cols)}) VALUES({','.join('?' for _ in cols)})"
                    payload = []
                    for row in rows:
                        values = []
                        for col in cols:
                            value = row[col]
                            if isinstance(value, memoryview):
                                value = bytes(value)
                            values.append(value)
                        payload.append(tuple(values))
                    sq.executemany(sql, payload)
            sq.commit()
        finally:
            sq.close()
        return path.read_bytes()
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


class PostgresCloudDatabase(_SQLITE_CLOUD_DATABASE):
    """PostgreSQL backend preserving the existing V6.21 CloudDatabase API."""

    _v622_postgres = True
    backend_name = "postgresql"

    def __init__(self, path: str | Path):
        self.path = Path(path)  # only retained for compatibility/UI labels
        self.database_url = resolve_database_url()
        if not self.database_url:
            raise RuntimeError("V6.22 PostgreSQL được chọn nhưng DATABASE_URL đang trống.")
        # Run the inherited SQLite schema/migration code through our PostgreSQL
        # compatibility connection so the business API stays one single source.
        self.create_tables()
        self.migrate()

    @contextmanager
    def connect(self):
        with _PGConnectionContext(self.database_url) as c:
            yield c

    def create_tables(self):
        _SQLITE_CLOUD_DATABASE.create_tables(self)
        with self.connect() as c:
            _ensure_legal_and_extra_schema(c)

    def migrate(self):
        _SQLITE_CLOUD_DATABASE.migrate(self)
        with self.connect() as c:
            _ensure_legal_and_extra_schema(c)

    def _columns(self, c, table: str) -> set[str]:
        return set(_table_columns(c, table))

    def approval_workflows_for_records(self, project_id, record_kind, subtype, record_ids):
        # V6.15 legacy workflow compatibility intentionally ignores subtype drift.
        ids: list[int] = []
        seen: set[int] = set()
        for value in record_ids or []:
            try:
                record_id = int(value)
            except Exception:
                continue
            if record_id > 0 and record_id not in seen:
                seen.add(record_id)
                ids.append(record_id)
        if not ids:
            return {}
        out = {}
        with self.connect() as c:
            for start in range(0, len(ids), 500):
                chunk = ids[start:start + 500]
                marks = ",".join("?" for _ in chunk)
                rows = c.execute(
                    f"""SELECT * FROM approval_workflows
                           WHERE project_id=? AND LOWER(TRIM(record_kind))=?
                             AND record_id IN ({marks})
                           ORDER BY id""",
                    [int(project_id), str(record_kind).strip().lower(), *chunk],
                ).fetchall()
                for row in rows:
                    out[int(row["record_id"])] = row
        return out

    def backup_bytes(self) -> bytes:
        # Existing Settings > Backup DB keeps downloading a portable SQLite .db.
        return _portable_sqlite_backup(self.database_url)

    def restore_bytes(self, data: bytes):
        # Existing Settings > Restore DB is the migration path from SQLite legacy.
        stats = import_sqlite_bytes(bytes(data or b""), replace=True, url=self.database_url)
        self.create_tables()
        self.migrate()
        return stats


def _patch_ai_service(module) -> None:
    cls = getattr(module, "ProjectContextBuilder", None)
    if cls is None or getattr(cls, "_v622_postgres", False):
        return

    def pg_connect(self):
        return _PGConnectionContext(resolve_database_url())

    def pg_table_exists(self, c, table: str) -> bool:
        return _table_exists(c, table)

    cls.connect = pg_connect
    cls.table_exists = pg_table_exists
    cls._v622_postgres = True


def _patch_legal_documents(module) -> None:
    cls = getattr(module, "LegalRepository", None)
    if cls is None or getattr(cls, "_v622_postgres", False):
        return

    def pg_connect(self):
        return _PGConnectionContext(resolve_database_url())

    cls.connect = pg_connect
    cls._v622_postgres = True


_PATCHERS = {
    "ai_service": _patch_ai_service,
    "legal_documents": _patch_legal_documents,
}


class _PostLoadLoader(importlib.abc.Loader):
    def __init__(self, loader, fullname: str):
        self.loader = loader
        self.fullname = fullname

    def create_module(self, spec):
        create = getattr(self.loader, "create_module", None)
        return create(spec) if create else None

    def exec_module(self, module):
        self.loader.exec_module(module)
        patcher = _PATCHERS.get(self.fullname)
        if patcher:
            patcher(module)


class _PostgresPatchFinder(importlib.abc.MetaPathFinder):
    _v622_finder = True

    def find_spec(self, fullname, path=None, target=None):
        if fullname not in _PATCHERS:
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec is None or spec.loader is None or isinstance(spec.loader, _PostLoadLoader):
            return spec
        spec.loader = _PostLoadLoader(spec.loader, fullname)
        return spec


def _install_post_import_patches() -> None:
    for name, patcher in _PATCHERS.items():
        module = sys.modules.get(name)
        if module is not None:
            patcher(module)
    if not any(getattr(finder, "_v622_finder", False) for finder in sys.meta_path):
        sys.meta_path.insert(0, _PostgresPatchFinder())


def install_postgres_backend() -> bool:
    """Use PostgreSQL when DATABASE_URL exists; otherwise keep SQLite fallback."""
    if not resolve_database_url():
        return False
    if not getattr(_cloud_db.CloudDatabase, "_v622_postgres", False):
        _cloud_db.SQLiteCloudDatabase = _SQLITE_CLOUD_DATABASE
        _cloud_db.CloudDatabase = PostgresCloudDatabase
    _install_post_import_patches()
    return True


def backend_name() -> str:
    return "PostgreSQL" if postgres_enabled() else "SQLite fallback"


def postgres_healthcheck() -> dict[str, Any]:
    url = resolve_database_url()
    if not url:
        return {"ok": False, "backend": "sqlite", "error": "DATABASE_URL chưa cấu hình"}
    try:
        with _PGConnectionContext(url) as c:
            row = c.execute(
                "SELECT current_database() AS db,current_user AS usr,version() AS version"
            ).fetchone()
            return {
                "ok": True,
                "backend": "postgresql",
                "database": str(row["db"] or ""),
                "user": str(row["usr"] or ""),
                "version": str(row["version"] or "").split(",", 1)[0],
            }
    except Exception as exc:
        return {"ok": False, "backend": "postgresql", "error": str(exc)}
