from __future__ import annotations

import hashlib
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook

from qlda.import_engines.schedule_background import parse_schedule_excel_path
from qlda.import_engines.schedule_persistence import save_schedule_result_batched
from qlda.runtime_core.project_store import CloudDatabase


def _complex_workbook(path: Path) -> None:
    book = Workbook()
    meta = book.active
    meta.title = "Thông tin"
    meta.append(["Bảng tổng hợp"])
    sheet = book.create_sheet("tiến độ")
    sheet["C1"] = "TIẾN ĐỘ THI CÔNG HỆ THỐNG CƠ ĐIỆN"
    sheet["C2"] = "Gói thầu thử nghiệm nhiều dòng tiêu đề"
    sheet["B7"] = "STT"
    sheet["C7"] = "Hạng mục công việc"
    sheet["D7"] = "Bắt đầu"
    sheet["E7"] = "Số ngày"
    sheet["F7"] = "Kết thúc"
    sheet["G7"] = "Ghi chú"

    sheet["C8"] = "TIẾN ĐỘ THI CÔNG TỔNG THỂ"
    sheet["D8"] = datetime(2026, 10, 1)
    sheet["E8"] = 273
    sheet["F8"] = datetime(2027, 6, 30)

    sheet["B9"] = 1.0
    sheet["C9"] = "Tháp S4"
    sheet["D9"] = datetime(2026, 10, 1)
    sheet["E9"] = 120
    sheet["F9"] = datetime(2027, 1, 29)

    sheet["B10"] = "1.3.1"
    sheet["C10"] = "         Trong căn hộ T3~T38"
    sheet["D10"] = datetime(2027, 1, 1)
    sheet["E10"] = 18
    sheet["F10"] = datetime(2027, 1, 19)

    sheet["C11"] = "         Hệ thống điện"
    sheet["C12"] = "            Lắp đặt công tắc"
    sheet["D12"] = datetime(2027, 1, 10)
    sheet["E12"] = 20
    sheet["F12"] = datetime(2027, 1, 29)

    sheet["B13"] = "1.3.2"
    sheet["C13"] = "         Hành lang"
    sheet["C14"] = "Hạng mục lỗi ngày"
    sheet["F14"] = datetime(2027, 2, 1)
    book.save(path)


class ScheduleExcelMultiHeaderTests(unittest.TestCase):
    def test_detects_header_in_second_sheet_and_preserves_exact_dates(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "Tien_do_Rev1.xlsx"
            _complex_workbook(path)
            before = hashlib.sha256(path.read_bytes()).hexdigest()
            result = parse_schedule_excel_path(path, status_date="2026-11-19")
            self.assertEqual(result["source_sha256"], before)
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)
            self.assertEqual(result["sheet_name"], "tiến độ")
            self.assertEqual(result["header_row"], 7)
            self.assertEqual(result["task_count"], 6)
            self.assertEqual(result["dated_count"], 4)
            self.assertEqual(result["undated_count"], 2)
            self.assertEqual(result["skipped_rows"], 1)
            self.assertEqual(result["skipped_details"][0]["row_no"], 14)
            total, tower, group, section, detail, corridor = result["tasks"]
            self.assertEqual((total["start_date"], total["end_date"], total["duration"]),
                             ("2026-10-01", "2027-06-30", 273))
            self.assertEqual((tower["start_date"], tower["end_date"]), ("2026-10-01", "2027-01-29"))
            self.assertEqual(group["wbs"], "1.3.1")
            self.assertEqual(group["end_date"], "2027-01-19")
            self.assertEqual((section["start_date"], section["end_date"]), ("", ""))
            self.assertEqual(detail["end_date"], "2027-01-29")
            self.assertTrue(any(x["parent_row"] == 10 and x["child_row"] == 12
                                for x in result["summary_date_conflicts"]))

    def test_database_writes_undated_rows_without_touching_manual(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "schedule.xlsx"
            _complex_workbook(path)
            result = parse_schedule_excel_path(path, status_date="2026-11-19")
            db = CloudDatabase(root / "database.sqlite")
            pid = db.add_project("SCHEDULE-EXCEL-TEST", "Kiểm tra nhập tiến độ")
            manual_id = db.add_task(pid, {
                "name": "Công việc nhập tay",
                "start_date": "2026-11-01", "end_date": "2026-11-02",
            })
            first = save_schedule_result_batched(db, pid, result)
            second = save_schedule_result_batched(db, pid, result)
            self.assertTrue(first["verified_postgresql"])
            self.assertEqual(second["written_rows"], 6)
            with db.connect() as connection:
                manual = connection.execute(
                    "SELECT name FROM tasks WHERE id=?", (manual_id,),
                ).fetchone()
                rows = connection.execute(
                    "SELECT name,start_date,end_date,duration,outline_level,is_summary "
                    "FROM tasks WHERE project_id=? AND source_type LIKE 'schedule_excel:%' "
                    "ORDER BY source_uid", (pid,),
                ).fetchall()
            self.assertEqual(manual[0], "Công việc nhập tay")
            self.assertEqual(len(rows), 6)
            self.assertEqual(rows[0]["start_date"], "2026-10-01")
            self.assertEqual(rows[0]["end_date"], "2027-06-30")
            self.assertEqual(rows[0]["duration"], 273)
            self.assertEqual(rows[3]["start_date"], "")
            self.assertEqual(rows[3]["end_date"], "")
            self.assertGreater(rows[2]["outline_level"], 1)


if __name__ == "__main__":
    unittest.main()
