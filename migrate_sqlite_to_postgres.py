from __future__ import annotations

import argparse
import os
from pathlib import Path

from postgres_backend_v622 import import_sqlite_bytes, postgres_healthcheck


def main() -> int:
    parser = argparse.ArgumentParser(
        description="QLDA V6.22 - migrate a legacy SQLite backup into PostgreSQL."
    )
    parser.add_argument("--sqlite", required=True, help="Path to qlda_cloud.db / backup .db")
    parser.add_argument(
        "--database-url",
        default="",
        help="PostgreSQL URL. If omitted, DATABASE_URL/QLDA_DATABASE_URL/POSTGRES_URL is used.",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Replace all QLDA business data in the target database. Required for safety.",
    )
    args = parser.parse_args()

    path = Path(args.sqlite).expanduser().resolve()
    if not path.is_file():
        parser.error(f"SQLite file not found: {path}")
    if not args.replace:
        parser.error("Add --replace to confirm replacing target QLDA data.")
    if args.database_url:
        os.environ["DATABASE_URL"] = args.database_url.strip()

    health = postgres_healthcheck()
    if not health.get("ok"):
        raise SystemExit(f"PostgreSQL connection failed: {health.get('error', 'unknown error')}")

    stats = import_sqlite_bytes(path.read_bytes(), replace=True)
    print(f"Connected PostgreSQL database: {health.get('database', '')}")
    print("Migration completed:")
    for table, count in stats.items():
        print(f"  {table}: {count}")
    print(f"Total rows: {sum(stats.values())}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
