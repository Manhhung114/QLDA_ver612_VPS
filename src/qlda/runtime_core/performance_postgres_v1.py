from __future__ import annotations

import os
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any


PATCH_MARKER = "V6.22 PERFORMANCE V1 POSTGRES"
_SCHEMA_LOCK = threading.RLock()
_SCHEMA_READY: set[tuple[str, str]] = set()


def _enabled() -> bool:
    value = str(os.environ.get("QLDA_PERFORMANCE_V1", "1") or "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def install_performance_postgres_v1(pg: Any) -> None:
    """Install low-risk process-level PostgreSQL optimizations.

    1. Cache deterministic SQLite->PostgreSQL SQL rewrites. The app executes the
       same query templates on every Streamlit rerun; regex translation does not
       need to be repeated thousands of times.
    2. Run the inherited core create_tables()/migrate() bootstrap once per
       database per Streamlit process instead of on every CloudDatabase object.
       Deployments restart the process, so every new release still performs a
       full schema bootstrap before serving normal traffic.

    Business queries, writes, transactions, permissions and schemas are not
    changed. Set QLDA_PERFORMANCE_V1=0 for an immediate runtime fallback.
    """
    if getattr(pg, "_performance_v1_postgres_installed", False):
        return

    original_rewrite = pg._rewrite_sql

    @lru_cache(maxsize=4096)
    def cached_rewrite(sql: str, *, return_insert_id: bool = True):
        return original_rewrite(sql, return_insert_id=return_insert_id)

    cls = pg.PostgresCloudDatabase
    original_init = cls.__init__

    def fast_init(self, path):
        if not _enabled():
            return original_init(self, path)

        self.path = Path(path)  # compatibility/UI label; data remains PostgreSQL
        self.database_url = pg.resolve_database_url()
        if not self.database_url:
            raise RuntimeError("V6.22 PostgreSQL được chọn nhưng DATABASE_URL đang trống.")

        key = (str(getattr(pg, "V622_SCHEMA_VERSION", "6.22")), str(self.database_url))
        if key in _SCHEMA_READY:
            return

        # Only the first concurrent session performs DDL/migration. Other first
        # sessions wait briefly here, then continue without repeating the work.
        with _SCHEMA_LOCK:
            if key in _SCHEMA_READY:
                return
            self.create_tables()
            self.migrate()
            _SCHEMA_READY.add(key)

    pg._rewrite_sql = cached_rewrite
    cls.__init__ = fast_init
    cls._performance_v1_original_init = original_init
    cls._performance_v1_schema_ready = _SCHEMA_READY
    pg._performance_v1_postgres_installed = True


def performance_postgres_snapshot(pg: Any) -> dict[str, Any]:
    info = getattr(getattr(pg, "_rewrite_sql", None), "cache_info", None)
    cache = info() if callable(info) else None
    with _SCHEMA_LOCK:
        ready_count = len(_SCHEMA_READY)
    return {
        "marker": PATCH_MARKER,
        "enabled": _enabled(),
        "schema_ready_count": ready_count,
        "sql_rewrite_cache": None
        if cache is None
        else {
            "hits": int(cache.hits),
            "misses": int(cache.misses),
            "maxsize": int(cache.maxsize or 0),
            "currsize": int(cache.currsize),
        },
    }


def clear_performance_postgres_cache_for_tests(pg: Any | None = None) -> None:
    with _SCHEMA_LOCK:
        _SCHEMA_READY.clear()
    if pg is not None:
        clear = getattr(getattr(pg, "_rewrite_sql", None), "cache_clear", None)
        if callable(clear):
            clear()
