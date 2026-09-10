from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

from work_tasks_v1_v622 import (
    COMMENTS_TABLE,
    FILES_TABLE,
    HISTORY_TABLE,
    TASKS_TABLE,
    _now_dt,
    add_work_task_comment,
    create_work_task,
    effective_status,
    ensure_schema,
    get_work_task,
    is_overdue,
    list_work_task_comments,
    list_work_task_history,
    list_work_tasks,
    transition_work_task,
    update_work_task_progress,
    work_task_summary,
)


class TinyDB:
    def __init__(self, path: Path):
        self.path = Path(path)
        with self.connect() as connection:
            connection.execute(
                """CREATE TABLE IF NOT EXISTS projects(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code TEXT NOT NULL UNIQUE,
                    name TEXT NOT NULL
                )"""
            )
            connection.execute("INSERT INTO projects(code,name) VALUES('MASTER','Master Project')")
            connection.execute("INSERT INTO projects(code,name) VALUES('NT01','Contractor 01')")
            connection.execute("INSERT INTO projects(code,name) VALUES('NT02','Contractor 02')")

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def project(self, project_id: int):
        with self.connect() as connection:
            return connection.execute("SELECT * FROM projects WHERE id=?", (int(project_id),)).fetchone()


class WorkTasksV1Test(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = TinyDB(Path(self.temp.name) / "work_tasks.db")
        ensure_schema(self.db)
        self.manager = {
            "email": "manager@example.com",
            "name": "Ban điều hành",
            "role": "update",
            "approval_role": "SITE_MANAGEMENT",
        }
        self.assignee = {
            "email": "worker@example.com",
            "name": "Người thực hiện",
            "role": "update",
            "approval_role": "CONTRACTOR",
        }
        self.other = {
            "email": "other@example.com",
            "name": "Người khác",
            "role": "update",
            "approval_role": "CONTRACTOR",
        }

    def tearDown(self):
        self.temp.cleanup()

    def _create(self):
        return create_work_task(
            self.db,
            master_project_id=1,
            workspace_project_id=2,
            title="Khắc phục NCR sprinkler tầng 20",
            description="Hoàn tất khắc phục và cập nhật ảnh hiện trường.",
            assignee_email=self.assignee["email"],
            assignee_name=self.assignee["name"],
            priority="Khẩn",
            due_at=_now_dt() + timedelta(days=2),
            actor=self.manager,
            source_module="Hồ sơ",
            source_type="NCR",
            source_id="25",
            source_code="NCR-025",
            source_title="Sprinkler tầng 20",
        )

    def test_schema_contains_separate_task_comment_history_file_tables(self):
        with self.db.connect() as connection:
            names = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        self.assertTrue({TASKS_TABLE, COMMENTS_TABLE, HISTORY_TABLE, FILES_TABLE}.issubset(names))

    def test_full_assignment_lifecycle_requires_management_confirmation(self):
        created = self._create()
        task_id = int(created["id"])
        self.assertRegex(str(created["task_code"]), r"^TASK-\d{5,}$")
        self.assertEqual(created["status"], "MỚI")
        self.assertEqual(created["source_code"], "NCR-025")
        self.assertEqual(int(created["workspace_project_id"]), 2)

        accepted = transition_work_task(
            self.db, task_id, 2, "ACCEPT", actor=self.assignee
        )
        self.assertEqual(accepted["status"], "ĐÃ NHẬN")

        started = transition_work_task(
            self.db, task_id, 2, "START", actor=self.assignee
        )
        self.assertEqual(started["status"], "ĐANG XỬ LÝ")

        progressed = update_work_task_progress(
            self.db,
            task_id,
            2,
            65,
            actor=self.assignee,
            note="Đã xử lý phần chính.",
        )
        self.assertEqual(int(progressed["progress_percent"]), 65)

        add_work_task_comment(
            self.db,
            task_id,
            2,
            "Đã bổ sung ảnh hiện trường.",
            actor=self.assignee,
        )
        comments = list_work_task_comments(self.db, task_id)
        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0]["author_email"], self.assignee["email"])

        requested = transition_work_task(
            self.db,
            task_id,
            2,
            "REQUEST_COMPLETION",
            actor=self.assignee,
        )
        self.assertEqual(requested["status"], "CHỜ XÁC NHẬN")
        self.assertEqual(int(requested["progress_percent"]), 100)

        with self.assertRaises(PermissionError):
            transition_work_task(
                self.db, task_id, 2, "CONFIRM", actor=self.other
            )

        confirmed = transition_work_task(
            self.db, task_id, 2, "CONFIRM", actor=self.manager
        )
        self.assertEqual(confirmed["status"], "HOÀN THÀNH")
        self.assertTrue(str(confirmed["completed_at"] or ""))

        closed = transition_work_task(
            self.db, task_id, 2, "CLOSE", actor=self.manager
        )
        self.assertEqual(closed["status"], "ĐÓNG")
        self.assertTrue(str(closed["closed_at"] or ""))

        actions = [row["action"] for row in list_work_task_history(self.db, task_id)]
        for expected in (
            "CREATE",
            "ACCEPT",
            "START",
            "PROGRESS",
            "COMMENT",
            "REQUEST_COMPLETION",
            "CONFIRM",
            "CLOSE",
        ):
            self.assertIn(expected, actions)

    def test_workspace_scope_prevents_cross_contractor_access(self):
        created = self._create()
        task_id = int(created["id"])
        self.assertEqual(get_work_task(self.db, task_id, workspace_project_id=3), {})
        with self.assertRaises(ValueError):
            transition_work_task(
                self.db, task_id, 3, "START", actor=self.manager
            )
        self.assertEqual(len(list_work_tasks(self.db, 2)), 1)
        self.assertEqual(len(list_work_tasks(self.db, 3)), 0)

    def test_overdue_is_computed_and_not_a_manual_terminal_status(self):
        sample = {
            "status": "ĐANG XỬ LÝ",
            "due_at": (_now_dt() - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S"),
        }
        self.assertTrue(is_overdue(sample))
        self.assertEqual(effective_status(sample), "QUÁ HẠN")
        sample["status"] = "HOÀN THÀNH"
        self.assertFalse(is_overdue(sample))
        self.assertEqual(effective_status(sample), "HOÀN THÀNH")

    def test_summary_separates_mine_waiting_overdue_and_completed(self):
        created = self._create()
        task_id = int(created["id"])
        transition_work_task(self.db, task_id, 2, "START", actor=self.assignee)
        summary = work_task_summary(self.db, 2, self.assignee["email"])
        self.assertEqual(summary["mine"], 1)
        self.assertEqual(summary["active"], 1)
        self.assertEqual(summary["waiting_confirmation"], 0)
        self.assertEqual(summary["completed"], 0)


if __name__ == "__main__":
    unittest.main()
