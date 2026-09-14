from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from pathlib import Path

from cloud_db import CloudDatabase
from openpyxl import Workbook
from schedule_background_v624 import parse_schedule_excel_path
from schedule_persist_v624 import SOURCE_PREFIX, VERIFY_COMPLETE, save_schedule_result_batched


def _workbook(path: Path) -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Tiến độ"
    sheet.append(["WBS", "Công việc", "Bắt đầu", "Kết thúc", "KH %", "TT %", "Phụ trách", "Predecessor", "Ghi chú"])
    sheet.append(["1", "Thi công móng", "01/09/2026", "10/09/2026", 50, 25, "Đội A", "", ""])
    sheet.append(["2", "Thi công thân", "2026-09-11", "2026-10-20", 10, 0.5, "Đội B", "1", "Ưu tiên"])
    sheet.append(["3", "Dòng lỗi", "", "2026-10-30", 0, 0, "", "", ""])
    workbook.save(path)


def _bulk_result(count: int = 2_505, batch_id: str = "schedule-2505-proof") -> dict:
    tasks = [
        {
            "source_row_no": index + 2,
            "wbs": str(index),
            "name": f"Công việc {index}",
            "responsible": "Đội thi công",
            "start_date": "2026-09-01",
            "end_date": "2026-09-30",
            "duration": 30,
            "planned_progress": 50,
            "actual_progress": 25,
            "predecessor": "",
            "note": "",
        }
        for index in range(1, count + 1)
    ]
    return {
        "filename": "TienDo.xlsx",
        "batch_id": batch_id,
        "status_date": "2026-09-14",
        "sheet_name": "Tiến độ",
        "tasks": tasks,
        "task_count": count,
        "detail_line_count": count,
        "source_row_count": count,
        "skipped_rows": 0,
    }


class ScheduleBackgroundPathTests(unittest.TestCase):
    def test_parses_valid_rows_and_preserves_exact_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "TienDo.xlsx"
            _workbook(path)
            before = hashlib.sha256(path.read_bytes()).hexdigest()
            result = parse_schedule_excel_path(path, path.name, status_date="2026-09-14")

            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)
            self.assertEqual(result["source_sha256"], before)
            self.assertEqual(result["task_count"], 2)
            self.assertEqual(result["source_row_count"], 3)
            self.assertEqual(result["skipped_rows"], 1)
            self.assertEqual(result["tasks"][0]["start_date"], "2026-09-01")
            self.assertEqual(result["tasks"][1]["planned_progress"], 10)
            self.assertEqual(result["tasks"][1]["actual_progress"], 50)


class ScheduleBackgroundPersistenceTests(unittest.TestCase):
    def _database(self, directory: str) -> tuple[CloudDatabase, int]:
        db = CloudDatabase(Path(directory) / "qlda.db")
        return db, db.add_project("P-V6245", "Kiểm tra tiến độ V6.24.5")

    def test_replaces_only_background_schedule_and_verifies_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            db, project_id = self._database(tmp)
            manual_id = db.add_task(
                project_id,
                {
                    "name": "Công việc thủ công",
                    "start_date": "2026-09-01",
                    "end_date": "2026-09-02",
                },
            )
            first = save_schedule_result_batched(db, project_id, _bulk_result(3, "batch-first"), batch_size=50)
            self.assertEqual(first["expected_rows"], 3)
            self.assertEqual(first["written_rows"], 3)
            self.assertEqual(first["failed_rows"], 0)
            self.assertEqual(first["verification_status"], VERIFY_COMPLETE)

            second = save_schedule_result_batched(db, project_id, _bulk_result(2, "batch-second"), batch_size=50)
            self.assertEqual(second["written_rows"], 2)
            with db.connect() as connection:
                manual = connection.execute("SELECT COUNT(*) FROM tasks WHERE id=?", (manual_id,)).fetchone()
                background = connection.execute(
                    "SELECT COUNT(*) FROM tasks WHERE project_id=? AND source_type LIKE ?",
                    (project_id, SOURCE_PREFIX + "%"),
                ).fetchone()
            self.assertEqual(int(manual[0]), 1)
            self.assertEqual(int(background[0]), 2)

    def test_mismatch_rolls_back_and_keeps_previous_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            db, project_id = self._database(tmp)
            first = save_schedule_result_batched(db, project_id, _bulk_result(3, "batch-stable"), batch_size=50)
            with db.connect() as connection:
                connection.execute(
                    "CREATE TRIGGER skip_schedule_row_v6245 BEFORE INSERT ON tasks "
                    "WHEN NEW.name='Công việc 2' AND NEW.source_type LIKE 'schedule_excel:%' "
                    "BEGIN SELECT RAISE(IGNORE); END"
                )
            with self.assertRaisesRegex(Exception, "CHƯA ĐỦ"):
                save_schedule_result_batched(db, project_id, _bulk_result(4, "batch-failed"), batch_size=50)
            with db.connect() as connection:
                row = connection.execute(
                    "SELECT source_type,COUNT(*) AS row_count FROM tasks "
                    "WHERE project_id=? AND source_type LIKE ? GROUP BY source_type",
                    (project_id, SOURCE_PREFIX + "%"),
                ).fetchone()
            self.assertEqual(row["source_type"], first["source_type"])
            self.assertEqual(int(row["row_count"]), 3)

    def test_batches_and_verifies_2505_tasks(self):
        with tempfile.TemporaryDirectory() as tmp:
            db, project_id = self._database(tmp)
            stats = save_schedule_result_batched(db, project_id, _bulk_result(), batch_size=500)
            self.assertEqual(stats["expected_rows"], 2_505)
            self.assertEqual(stats["inserted_rows"], 2_505)
            self.assertEqual(stats["written_rows"], 2_505)
            self.assertEqual(stats["failed_rows"], 0)
            self.assertEqual(stats["verification_status"], VERIFY_COMPLETE)


@unittest.skipUnless(os.environ.get("DATABASE_URL"), "DATABASE_URL chưa được cấu hình")
class ScheduleBackgroundPostgreSQLTests(unittest.TestCase):
    def test_confirms_all_schedule_rows_in_real_postgresql(self):
        from postgres_backend_v622 import _PGConnectionContext

        class PostgreSQLTestDatabase:
            def __init__(self, url: str):
                self.url = url

            def connect(self):
                return _PGConnectionContext(self.url)

        db = PostgreSQLTestDatabase(str(os.environ["DATABASE_URL"]))
        with db.connect() as connection:
            connection.execute("DROP TABLE IF EXISTS tasks")
            connection.execute(
                "CREATE TABLE tasks("
                "id BIGSERIAL PRIMARY KEY,project_id BIGINT NOT NULL,wbs TEXT DEFAULT '',name TEXT NOT NULL,"
                "responsible TEXT DEFAULT '',start_date TEXT NOT NULL,end_date TEXT NOT NULL,duration REAL DEFAULT 1,"
                "planned_progress INTEGER DEFAULT 0,actual_progress INTEGER DEFAULT 0,actual_override INTEGER,"
                "actual_update_date TEXT DEFAULT '',actual_finish_date TEXT DEFAULT '',status TEXT DEFAULT '',"
                "predecessor TEXT DEFAULT '',note TEXT DEFAULT '',source_type TEXT DEFAULT 'manual',source_uid INTEGER,"
                "source_task_id INTEGER,outline_level INTEGER DEFAULT 1,is_summary INTEGER DEFAULT 0,"
                "is_milestone INTEGER DEFAULT 0,critical INTEGER DEFAULT 0,total_slack REAL DEFAULT 0,"
                "resource_names TEXT DEFAULT '',baseline_start TEXT DEFAULT '',baseline_finish TEXT DEFAULT '')"
            )
        try:
            stats = save_schedule_result_batched(db, 6245, _bulk_result(), batch_size=500)
            with db.connect() as connection:
                row = connection.execute(
                    "SELECT COUNT(*) AS row_count FROM tasks WHERE project_id=? AND source_type=?",
                    (6245, stats["source_type"]),
                ).fetchone()
            self.assertEqual(int(row["row_count"]), 2_505)
            self.assertEqual(stats["written_rows"], 2_505)
            self.assertEqual(stats["failed_rows"], 0)
            self.assertTrue(stats["verified_postgresql"])
        finally:
            with db.connect() as connection:
                connection.execute("DROP TABLE IF EXISTS tasks")


if __name__ == "__main__":
    unittest.main()
