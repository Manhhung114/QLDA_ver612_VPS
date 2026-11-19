from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from openpyxl import Workbook

from qlda.import_engines.schedule_background import parse_schedule_excel_path
from qlda.import_engines.schedule_persistence import save_schedule_result_batched
from qlda.infrastructure.schedule_snapshot import save_project_schedule
from qlda.runtime_core.project_store import CloudDatabase


def _schedule_265(path: Path):
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "tiến độ"
    sheet["C1"] = "TIẾN ĐỘ CƠ ĐIỆN - PCCC"
    sheet["B7"] = "STT"
    sheet["C7"] = "Hạng mục công việc"
    sheet["D7"] = "Bắt đầu"
    sheet["E7"] = "Số ngày"
    sheet["F7"] = "Kết thúc"
    sheet["G7"] = "Ghi chú"
    base = datetime(2026, 10, 1)
    for i in range(265):
        row = i + 8
        sheet.cell(row, 2).value = f"{1+i//30}.{i+1}"
        sheet.cell(row, 3).value = f"   Hạng mục {i+1:03d}"
        if i < 185:
            sheet.cell(row, 4).value = base + timedelta(days=i)
            sheet.cell(row, 5).value = 10
            sheet.cell(row, 6).value = base + timedelta(days=i + 10)
    sheet["B274"] = "Ghi chú:\n1. Bảng tiến độ thể hiện công tác còn lại"
    sheet["B275"] = "ĐẠI DIỆN BQLDA/CĐ                 ĐẠI DIỆN NHÀ THẦU"
    workbook.save(path)


def _project_tasks():
    return [{
        "source_uid": i+1, "task_id": i+1, "name": f"Project task {i+1}",
        "wbs": f"2.{i+1}", "start_date": "2026-11-01",
        "end_date": "2026-11-20", "duration": 19, "outline_level": 2,
        "planned_progress": 10, "actual_progress": 0,
    } for i in range(3)]


class FullScheduleReplacementTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.db = CloudDatabase(self.folder / "qlda.db")
        self.pid = self.db.add_project("A", "Current contractor")
        self.other = self.db.add_project("B", "Another contractor")
        self.db.add_task(self.pid, {
            "name": "Old manual task", "start_date": "2026-01-01",
            "end_date": "2026-01-03", "source_type": "manual",
        })
        self.db.add_task(self.pid, {
            "name": "Old MPP task", "start_date": "2026-02-01",
            "end_date": "2026-02-03", "source_type": "mpp", "source_uid": 800,
        })
        self.db.add_task(self.other, {
            "name": "Other project task", "start_date": "2026-01-01",
            "end_date": "2026-01-03",
        })
        self.book = self.folder / "schedule.xlsx"
        _schedule_265(self.book)

    def _parse(self):
        return parse_schedule_excel_path(self.book, status_date="2026-11-19")

    def test_realistic_265_tasks_including_80_undated_and_no_false_footer_warnings(self):
        result = self._parse()
        self.assertEqual(result["task_count"], 265)
        self.assertEqual(result["dated_count"], 185)
        self.assertEqual(result["undated_count"], 80)
        self.assertEqual(result["header_row"], 7)
        self.assertEqual(result["source_row_count"], 265)
        self.assertEqual(result["skipped_rows"], 0)
        self.assertEqual(result["tasks"][-1]["source_row_no"], 272)

    def test_replace_all_excel_clears_old_mpp_and_manual_and_verifies_all_265(self):
        result = self._parse()
        stats = save_schedule_result_batched(
            self.db, self.pid, result, replace_all=True, expected_existing_rows=2,
        )
        self.assertEqual(stats["written_rows"], 265)
        self.assertEqual(stats["total_rows"], 265)
        self.assertTrue(stats["verified_postgresql"])
        rows = self.db.tasks(self.pid)
        self.assertEqual(len(rows), 265)
        self.assertEqual(len(self.db.tasks(self.other)), 1)
        self.assertEqual(rows[0]["name"], "Hạng mục 001")
        self.assertEqual(rows[-1]["name"], "Hạng mục 265")
        self.assertEqual(rows[-1]["start_date"], "")
        self.assertEqual(rows[-1]["end_date"], "")
        self.assertEqual(rows[0]["start_date"], "2026-10-01")
        self.assertEqual(rows[0]["end_date"], "2026-10-11")
        self.assertEqual(rows[0]["duration"], 10)
        self.assertTrue(all(x["source_type"].startswith("schedule_excel:") for x in rows))

    def test_excel_replace_rolls_back_on_failed_insert(self):
        with self.db.connect() as connection:
            connection.execute(
                "CREATE TRIGGER bad_schedule BEFORE INSERT ON tasks "
                "WHEN NEW.name='Hạng mục 003' "
                "BEGIN SELECT RAISE(ABORT, 'blocked'); END"
            )
        with self.assertRaises(Exception):
            save_schedule_result_batched(
                self.db, self.pid, self._parse(), replace_all=True,
                expected_existing_rows=2,
            )
        self.assertEqual({x["name"] for x in self.db.tasks(self.pid)},
                         {"Old manual task", "Old MPP task"})

    def test_excel_replace_fails_if_old_row_count_changed(self):
        with self.assertRaisesRegex(ValueError, "đã thay đổi"):
            save_schedule_result_batched(
                self.db, self.pid, self._parse(),
                replace_all=True, expected_existing_rows=1,
            )
        self.assertEqual(len(self.db.tasks(self.pid)), 2)

    def test_replace_all_project_clears_old_excel_and_manual(self):
        save_schedule_result_batched(
            self.db, self.pid, self._parse(),
            replace_all=True, expected_existing_rows=2,
        )
        info = {"start_date": "2026-11-01", "end_date": "2026-11-20", "manager": "A"}
        stats = save_project_schedule(
            self.db, self.pid, _project_tasks(), "new.mpp", info,
            replace_all=True, expected_existing_rows=265,
        )
        self.assertEqual(stats["total_rows"], 3)
        self.assertEqual(stats["written_rows"], 3)
        rows = self.db.tasks(self.pid)
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(row["source_type"] == "mpp" for row in rows))
        self.assertEqual(self.db.project(self.pid)["source_mpp_path"], "new.mpp")
        self.assertEqual(len(self.db.tasks(self.other)), 1)

    def test_project_replace_rolls_back_on_failed_insert(self):
        with self.db.connect() as connection:
            connection.execute(
                "CREATE TRIGGER bad_project BEFORE INSERT ON tasks "
                "WHEN NEW.name='Project task 2' "
                "BEGIN SELECT RAISE(ABORT, 'blocked'); END"
            )
        with self.assertRaises(Exception):
            save_project_schedule(
                self.db, self.pid, _project_tasks(), "new.mpp",
                replace_all=True, expected_existing_rows=2,
            )
        self.assertEqual({x["name"] for x in self.db.tasks(self.pid)},
                         {"Old manual task", "Old MPP task"})

    def test_project_rejects_duplicate_uid_and_count_races(self):
        with self.assertRaisesRegex(ValueError, "UniqueID"):
            save_project_schedule(self.db, self.pid, _project_tasks()[:1] * 2, "bad.mpp",
                                  replace_all=True, expected_existing_rows=2)
        with self.assertRaisesRegex(ValueError, "đã thay đổi"):
            save_project_schedule(self.db, self.pid, _project_tasks(), "new.mpp",
                                  replace_all=True, expected_existing_rows=99)
        self.assertEqual(len(self.db.tasks(self.pid)), 2)

    def test_same_source_mode_keeps_other_sources(self):
        stats = save_schedule_result_batched(self.db, self.pid, self._parse())
        self.assertEqual(stats["total_rows"], 267)
        self.assertEqual(len(self.db.tasks(self.pid)), 267)
        self.assertEqual(len(self.db.tasks(self.other)), 1)
        second = save_project_schedule(
            self.db, self.pid, _project_tasks(), "other.mpp",
            replace_all=False,
        )
        self.assertEqual(second["written_rows"], 3)
        self.assertEqual(second["total_rows"], 269)


if __name__ == "__main__":
    unittest.main()
