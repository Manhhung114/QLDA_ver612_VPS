from __future__ import annotations

"""PostgreSQL connection primitives for native V7 infrastructure adapters."""

import os


def database_url() -> str:
    value = str(
        os.environ.get("DATABASE_URL")
        or os.environ.get("QLDA_DATABASE_URL")
        or os.environ.get("POSTGRES_URL")
        or ""
    ).strip()
    if value.startswith("postgres://"):
        value = "postgresql://" + value[len("postgres://"):]
    if not value:
        raise RuntimeError("DATABASE_URL đang trống; native V7 infrastructure cần PostgreSQL.")
    return value


def connect(*, autocommit: bool = False):
    try:
        import psycopg
        from psycopg.rows import dict_row
    except Exception as exc:
        raise RuntimeError("Thiếu psycopg để dùng native V7 infrastructure.") from exc
    return psycopg.connect(database_url(), autocommit=autocommit, row_factory=dict_row)
