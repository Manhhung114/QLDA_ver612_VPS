from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from qlda.infrastructure.schedule_workspace import (
    delete_schedule_scope, filtered_schedule_rows, schedule_source_kind,
    schedule_source_label, source_counts,
)
from qlda.runtime_core.project_store import CloudDatabase


class ScheduleSourceClassificationTests(unittest.TestCase):
    def test_classifies_project_excel_and_legacy_separately(self):
        rows = [
            {"source_type": "schedule_excel:abc"},
            {"source_type": "SCHEDULE_EXCEL:def"},
            {"source_type": "mpp"},
            {"source_type": "manual"},
            {"source_type": ""},
            {"source_type": "unknown"},
        ]
        self.assertEqual(source_counts(rows), {
            "excel": 2, "mpp": 1, "manual": 2, "other": 1, "all": 6
        })
        self.assertEqual(len(filtered_schedule_rows(rows, "excel")), 2)
        self.assertEqual(len(filtered_schedule_rows(rows, "mpp")), 1)
        self.assertEqual(len(filtered_schedule_rows(rows, "all")), 6)
        self.assertEqual(schedule_source_kind("schedule_excel:123"), "excel")
        self.assertEqual(schedule_source_label("mpp"), "Microsoft Project")
        self.assertEqual(schedule_source_label("manual"), "Thủ công / Excel cũ")
        with self.assertRaises(ValueError):
            filtered_schedule_rows(rows, "invalid")


class ScheduleSourceDeletionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = CloudDatabase(Path(self.tmp.name) / "schedule.db")
        self.pid = self.db.add_project("DELETION-A", "Project A")
        self.other_pid = self.db.add_project("DELETION-B", "Project B")
        self._task(self.pid, "Excel A1", "schedule_excel:abc", source_uid=10)
        self._task(self.pid, "Excel A2", "schedule_excel:abc", source_uid=11)
        self._task(self.pid, "Excel A3", "schedule_excel:xyz", source_uid=10)
        self._task(self.pid, "Project A", "mpp", source_uid=15)
        self._task(self.pid, "Thủ công", "manual")
        self._task(self.other_pid, "Excel B", "schedule_excel:abc", source_uid=20)
        self._task(self.other_pid, "Project B", "mpp", source_uid=25)
        with self.db.connect() as conn:
            conn.execute(
                "UPDATE projects SET source_mpp_path='plan.mpp',last_sync='some date' WHERE id=?",
                (self.pid,),
            )

    def _task(self, pid, name, source_type, source_uid=None):
        return self.db.add_task(pid, {
            "name": name, "wbs": "", "start_date": "2026-10-01",
            "end_date": "2027-06-30", "duration": 273,
            "source_type": source_type, "source_uid": source_uid,
        })

    def _names(self, pid=None):
        return {r["name"] for r in self.db.tasks(self.pid if pid is None else pid)}

    def test_excel_only_keeps_mpp_manual_and_other_project(self):
        deleted = delete_schedule_scope(
            self.db, self.pid, "excel", expected_rows=3, authorized=True,
        )
        self.assertEqual(deleted, 3)
        self.assertEqual(self._names(), {"Project A", "Thủ công"})
        self.assertEqual(self._names(self.other_pid), {"Excel B", "Project B"})
        self.assertEqual(self.db.project(self.pid)["source_mpp_path"], "plan.mpp")

    def test_mpp_only_clears_mpp_metadata_but_keeps_excel(self):
        deleted = delete_schedule_scope(
            self.db, self.pid, "mpp", expected_rows=1, authorized=True,
        )
        self.assertEqual(deleted, 1)
        self.assertIn("Excel A1", self._names())
        self.assertIn("Thủ công", self._names())
        self.assertEqual(self.db.project(self.pid)["source_mpp_path"], "")
        self.assertEqual(self.db.project(self.pid)["last_sync"], "")

    def test_admin_manual_and_all_scopes(self):
        self.assertEqual(
            delete_schedule_scope(self.db, self.pid, "manual", expected_rows=1, authorized=True), 1
        )
        self.assertNotIn("Thủ công", self._names())
        self.assertEqual(
            delete_schedule_scope(self.db, self.pid, "all", expected_rows=4, authorized=True), 4
        )
        self.assertEqual(self._names(), set())
        self.assertEqual(self._names(self.other_pid), {"Excel B", "Project B"})

    def test_wrong_count_fails_closed_and_does_not_change_database(self):
        with self.assertRaisesRegex(ValueError, "Số công việc đã thay đổi"):
            delete_schedule_scope(
                self.db, self.pid, "excel", expected_rows=2, authorized=True,
            )
        self.assertEqual(len(self._names()), 5)
        self.assertEqual(self.db.project(self.pid)["source_mpp_path"], "plan.mpp")

    def test_rejects_unapproved_deletion_and_invalid_scope(self):
        with self.assertRaises(PermissionError):
            delete_schedule_scope(self.db, self.pid, "all", expected_rows=5)
        with self.assertRaises(ValueError):
            delete_schedule_scope(self.db, self.pid, "untrusted", expected_rows=5, authorized=True)
        self.assertEqual(len(self._names()), 5)

    def test_rollback_if_delete_is_interrupted(self):
        with self.db.connect() as conn:
            conn.execute(
                "CREATE TRIGGER block_excel_deletion BEFORE DELETE ON tasks "
                "WHEN OLD.name='Excel A2' "
                "BEGIN SELECT RAISE(ABORT,'blocked'); END"
            )
        with self.assertRaises(Exception):
            delete_schedule_scope(self.db, self.pid, "excel", expected_rows=3, authorized=True)
        self.assertEqual(len(self._names()), 5)


if __name__ == "__main__":
    unittest.main()
