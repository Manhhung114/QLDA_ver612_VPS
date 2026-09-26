from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

import qlda.autonomy  # noqa: F401 - package import installs the production compatibility guard
from qlda.autonomy.loop_engine import ClosedLoopRepository


class _StrictTextTimestampConnection:
    """SQLite proxy that rejects the PostgreSQL-invalid Closed Loop DML pattern."""

    def __init__(self, path: Path) -> None:
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            self._conn.commit()
        self._conn.close()

    def execute(self, sql, params=()):
        normalized = " ".join(str(sql).upper().split())
        is_loop_dml = (
            "UPDATE QLDA_ENGINEERING_LOOPS" in normalized
            or "INSERT INTO QLDA_ENGINEERING_LOOPS" in normalized
        )
        if is_loop_dml and "CURRENT_TIMESTAMP" in normalized:
            raise AssertionError(
                "Closed Loop DML must bind TEXT timestamps instead of mixing "
                "TEXT columns with CURRENT_TIMESTAMP/timestamptz."
            )
        return self._conn.execute(sql, params)

    def commit(self) -> None:
        self._conn.commit()


class ClosedLoopPostgresTextTimestampTest(unittest.TestCase):
    def test_save_loop_binds_text_timestamps_and_preserves_closed_at(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "closed-loop.sqlite"
            repo = ClosedLoopRepository(lambda: _StrictTextTimestampConnection(path))
            row = {
                "loop_id": "loop-pg-safe",
                "project_id": 1,
                "workspace_project_id": 1,
                "status": "OPEN",
                "current_stage": "RECOMMEND",
                "actor": "admin@example.com",
                "baseline_health": 70,
                "latest_health": 70,
            }

            repo.save_loop(row)
            opened = repo.get_loop(project_id=1, loop_id="loop-pg-safe")
            self.assertTrue(str(opened.get("created_at") or ""))
            self.assertTrue(str(opened.get("updated_at") or ""))
            self.assertFalse(opened.get("closed_at"))

            row["status"] = "CLOSED"
            row["current_stage"] = "CLOSED"
            row["latest_health"] = 100
            repo.save_loop(row)
            closed = repo.get_loop(project_id=1, loop_id="loop-pg-safe")
            closed_at = str(closed.get("closed_at") or "")
            self.assertTrue(closed_at)

            repo.save_loop(row)
            closed_again = repo.get_loop(project_id=1, loop_id="loop-pg-safe")
            self.assertEqual(str(closed_again.get("closed_at") or ""), closed_at)

    def test_package_installs_postgres_safe_repository_write(self) -> None:
        self.assertTrue(
            getattr(
                ClosedLoopRepository.save_loop,
                "_qlda_postgres_text_timestamp_safe",
                False,
            )
        )


if __name__ == "__main__":
    unittest.main()
