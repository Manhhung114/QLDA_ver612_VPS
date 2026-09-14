from __future__ import annotations

import os
import time
from typing import Any


def _bounded_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except Exception:
        value = default
    return max(minimum, min(maximum, value))


def install_vps_postgres_resilience(pg: Any) -> None:
    """Harden the V6.22 PostgreSQL adapter for long-running VPS sessions.

    psycopg_pool doesn't validate idle connections by default. Managed PostgreSQL
    services may close an SSL session while it is sitting idle, after which the
    next Streamlit rerun can receive a dead connection and fail with messages such
    as ``SSL connection has been closed unexpectedly``. This patch makes the pool
    validate every checked-out connection and retries portable backups once with a
    fresh pool when a transient connection failure occurs.
    """
    if getattr(pg, "_vps_postgres_resilience_installed", False):
        return

    def drop_pool(url: str) -> None:
        normalized = pg._normalize_database_url(url)
        pool = None
        with pg._POOL_LOCK:
            pool = pg._POOLS.pop(normalized, None)
        if pool is not None:
            try:
                pool.close()
            except Exception:
                pass

    def resilient_get_pool(url: str):
        normalized = pg._normalize_database_url(url)
        with pg._POOL_LOCK:
            pool = pg._POOLS.get(normalized)
            if pool is not None:
                return pool

            try:
                from psycopg_pool import ConnectionPool
            except Exception as exc:
                raise RuntimeError(
                    "Thiếu psycopg-pool. Hãy cài psycopg[binary] và psycopg-pool trong requirements.txt."
                ) from exc

            min_size, max_size, timeout = pg._pool_limits()
            max_idle = _bounded_float("QLDA_PG_POOL_MAX_IDLE", 60.0, 10.0, 1800.0)
            max_lifetime = _bounded_float("QLDA_PG_POOL_MAX_LIFETIME", 900.0, 60.0, 7200.0)
            reconnect_timeout = _bounded_float("QLDA_PG_RECONNECT_TIMEOUT", 15.0, 5.0, 120.0)

            pool = ConnectionPool(
                conninfo=normalized,
                min_size=min_size,
                max_size=max_size,
                timeout=timeout,
                max_idle=max_idle,
                max_lifetime=max_lifetime,
                reconnect_timeout=reconnect_timeout,
                check=ConnectionPool.check_connection,
                kwargs={
                    "connect_timeout": 10,
                    "keepalives": 1,
                    "keepalives_idle": 30,
                    "keepalives_interval": 10,
                    "keepalives_count": 3,
                },
                open=True,
            )
            pg._POOLS[normalized] = pool
            return pool

    original_backup = pg._portable_sqlite_backup

    def resilient_backup(database_url: str) -> bytes:
        try:
            return original_backup(database_url)
        except Exception as exc:
            # Retry only connection-level failures. Schema/data errors must remain
            # visible instead of being hidden by an automatic retry.
            try:
                import psycopg

                transient = isinstance(
                    exc,
                    (psycopg.OperationalError, psycopg.InterfaceError),
                )
            except Exception:
                transient = False

            message = str(exc or "").lower()
            transient = transient or any(
                marker in message
                for marker in (
                    "ssl connection has been closed unexpectedly",
                    "server closed the connection unexpectedly",
                    "connection is closed",
                    "consuming input failed",
                    "terminating connection",
                    "connection reset by peer",
                )
            )
            if not transient:
                raise

            drop_pool(database_url)
            time.sleep(0.25)
            return original_backup(database_url)

    pg._get_pool = resilient_get_pool
    pg._portable_sqlite_backup = resilient_backup
    pg._vps_drop_postgres_pool = drop_pool
    pg._vps_postgres_resilience_installed = True
