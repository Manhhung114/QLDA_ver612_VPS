from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cloud_db import CloudDatabase
import contractor_workspace_v622 as cw
import work_tasks_v1_v622 as work_tasks
from contractor_workspace_reset_v622 import (
    PATCH_MARKER,
    _is_protected_project_table,
    _workspace_scope_tables,
    reset_default_workspace,
)


class DefaultWorkspaceResetTests(unittest.TestCase):
    def _build_db(self):
        tmp = tempfile.TemporaryDirectory()
        db = CloudDatabase(Path(tmp.name) / "reset.db")
        pid = db.add_project("RESET-PROJ", "Dự án reset")
        default = cw.ensure_default_contractor(db, pid)
        with db.connect() as c:
            c.execute(
                "UPDATE projects SET source_mpp_path=?,last_sync=? WHERE id=?",
                ("old.mpp", "2026-09-11 08:00:00", pid),
            )
            c.execute(
                "INSERT INTO tasks(project_id,name,start_date,end_date) VALUES(?,?,?,?)",
                (pid, "Tiến độ cũ", "2026-09-01", "2026-09-30"),
            )
            c.execute(
                "INSERT INTO cost_budgets(project_id,boq_item,quantity,unit_price,budget_total) VALUES(?,?,?,?,?)",
                (pid, "BOQ cũ", 1, 100, 100),
            )
            work_tasks.ensure_schema_connection(c)
            c.execute(
                f"""INSERT INTO {work_tasks.TASKS_TABLE}(
                       task_code,master_project_id,workspace_project_id,title,due_at,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?)""",
                ("TASK-00001", pid, pid, "Việc giao cũ", "2026-09-20 17:00:00", "2026-09-11", "2026-09-11"),
            )
            # Simulate project-scoped access metadata: reset must preserve it.
            c.execute(
                "CREATE TABLE IF NOT EXISTS project_user_access(id INTEGER PRIMARY KEY AUTOINCREMENT,project_id INTEGER NOT NULL,email TEXT)"
            )
            c.execute(
                "INSERT INTO project_user_access(project_id,email) VALUES(?,?)",
                (pid, "admin@example.com"),
            )
        return tmp, db, pid, default

    def test_reset_clears_business_data_but_keeps_identity_and_access(self):
        tmp, db, pid, default = self._build_db()
        try:
            result = reset_default_workspace(db, int(default["id"]), actor="admin@example.com")
            self.assertEqual(result["workspace_project_id"], pid)

            with db.connect() as c:
                self.assertEqual(c.execute("SELECT COUNT(*) FROM tasks WHERE project_id=?", (pid,)).fetchone()[0], 0)
                self.assertEqual(c.execute("SELECT COUNT(*) FROM cost_budgets WHERE project_id=?", (pid,)).fetchone()[0], 0)
                self.assertEqual(
                    c.execute(f"SELECT COUNT(*) FROM {work_tasks.TASKS_TABLE} WHERE workspace_project_id=?", (pid,)).fetchone()[0],
                    0,
                )
                self.assertEqual(c.execute("SELECT COUNT(*) FROM projects WHERE id=?", (pid,)).fetchone()[0], 1)
                self.assertEqual(
                    c.execute("SELECT COUNT(*) FROM project_contractors WHERE id=?", (int(default["id"]),)).fetchone()[0],
                    1,
                )
                self.assertEqual(c.execute("SELECT COUNT(*) FROM project_user_access WHERE project_id=?", (pid,)).fetchone()[0], 1)
                project = c.execute("SELECT source_mpp_path,last_sync FROM projects WHERE id=?", (pid,)).fetchone()
                self.assertEqual(project[0], "")
                self.assertEqual(project[1], "")
                self.assertEqual(c.execute("SELECT COUNT(*) FROM admin_workspace_reset_log").fetchone()[0], 1)
        finally:
            tmp.cleanup()

    def test_non_default_workspace_cannot_use_default_reset(self):
        tmp, db, pid, _default = self._build_db()
        try:
            child = cw.add_contractor(db, pid, "NT-02", "Nhà thầu 02")
            with self.assertRaises(ValueError):
                reset_default_workspace(db, int(child["id"]), actor="admin@example.com")
        finally:
            tmp.cleanup()

    def test_discovery_includes_workspace_scoped_work_tasks_and_protects_access(self):
        tmp, db, pid, _default = self._build_db()
        try:
            with db.connect() as c:
                scopes = dict(_workspace_scope_tables(c))
            self.assertEqual(scopes.get(work_tasks.TASKS_TABLE), "workspace_project_id")
            self.assertNotIn("project_user_access", scopes)
            self.assertTrue(_is_protected_project_table("project_user_access"))
            self.assertFalse(_is_protected_project_table("payment_claims"))
            self.assertTrue(PATCH_MARKER.startswith("V6.22 DEFAULT CONTRACTOR WORKSPACE RESET"))
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
