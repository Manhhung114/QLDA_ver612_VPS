from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openpyxl import Workbook

from qlda.application.excel.template_recognition import (
    analyze_workbook_path,
    recognize_sheet,
)
from qlda.import_engines.schedule_background import parse_schedule_excel_path
from qlda.infrastructure.native_excel import NativeExcelImportAdapter


def _write(path: Path, header: list[object], rows: list[list[object]], *, sheet_name: str = "Dữ liệu") -> None:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = sheet_name
    for _ in range(4):
        sheet.append(["Thông tin công trình"])
    sheet.append(header)
    for row in rows:
        sheet.append(row)
    workbook.save(path)
    workbook.close()


class UniversalExcelRecognitionTests(unittest.TestCase):
    def test_boq_reordered_and_renamed_columns(self):
        profile = recognize_sheet(
            "Danh mục thiết bị điện",
            [
                ["Dự án thi công"],
                ["Giá đơn vị", "Nội dung công tác", "KL thi công", "Thành tiền"],
                [50_000, "Ống", 100, 5_000_000],
            ],
        )
        self.assertEqual(profile["status"], "recognized")
        self.assertEqual(profile["role"], "BOQ")
        self.assertEqual(profile["header_row"], 2)
        self.assertEqual(profile["columns"]["description"], 1)
        self.assertEqual(profile["columns"]["quantity"], 2)
        self.assertEqual(profile["columns"]["unit_price"], 0)
        self.assertEqual(profile["columns"]["amount"], 3)

    def test_payment_summary_can_be_recognized_without_description(self):
        profile = recognize_sheet(
            "Bảng DNTT kỳ 1",
            [
                ["Giá trị đề nghị thanh toán kỳ này", "Lũy kế giá trị thanh toán"],
                [100_000, 300_000],
            ],
        )
        self.assertEqual(profile["role"], "IPC")
        self.assertEqual(profile["status"], "recognized")
        self.assertEqual(profile["columns"]["current_amount"], 0)
        self.assertEqual(profile["columns"]["cumulative_amount"], 1)

    def test_vo_can_detect_change_in_column_order(self):
        profile = recognize_sheet(
            "Chi tiết điều chỉnh",
            [
                ["Giá trị phát sinh", "Nội dung phát sinh", "Phát sinh tăng giảm"],
                [500_000, "Thêm ổ cắm", 2],
            ],
        )
        self.assertEqual(profile["role"], "VO")
        self.assertEqual(profile["columns"]["description"], 1)
        self.assertEqual(profile["columns"]["variation_quantity"], 2)

    def test_malformed_or_ambiguous_financial_layout_is_for_review(self):
        profile = recognize_sheet(
            "Tổng hợp",
            [["Nội dung công việc", "Thành tiền"], ["Hệ thống điện", 100]],
        )
        self.assertNotEqual(profile["status"], "recognized")
        self.assertTrue(profile["review_required"])

    def test_unknown_sheet_is_not_assigned_a_role(self):
        profile = recognize_sheet("Ghi chú", [["Lời chào", "Người nhận"], ["A", "B"]])
        self.assertEqual(profile["status"], "unsupported")
        self.assertIsNone(profile["role"])

    def test_schedule_bounded_multiline_header_and_new_aliases(self):
        profile = recognize_sheet(
            "Sheet tháng 9",
            [
                ["THÔNG TIN DỰ ÁN"],
                ["WBS", "Nội dung", "Ngày", "Ngày", "Số"],
                ["WBS", "Nội dung công tác", "Khởi công", "Hoàn thành", "Thời lượng"],
                ["1", "Thi công móng", "01/09/2026", "10/09/2026", 10],
            ],
            preferred_role="SCHEDULE_EXCEL",
        )
        self.assertEqual(profile["status"], "recognized")
        self.assertEqual(profile["header_row"], 3)
        self.assertEqual(profile["columns"]["name"], 1)
        self.assertEqual(profile["columns"]["start"], 2)
        self.assertEqual(profile["columns"]["end"], 3)

    def test_openpyxl_file_preflight_is_read_only_and_skips_hidden(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "test.xlsx"
            _write(path, ["Nội dung công tác", "Khối lượng", "Đơn giá"],
                   [["Cáp điện", 2, 50_000]], sheet_name="BOQ điện")
            from openpyxl import load_workbook
            workbook = load_workbook(path)
            helper = workbook.create_sheet("Helper")
            helper.sheet_state = "veryHidden"
            workbook.save(path)
            workbook.close()

            before = hashlib.sha256(path.read_bytes()).hexdigest()
            output = analyze_workbook_path(path)
            after = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertEqual(before, after)
            self.assertEqual(output["sheet_count"], 1)
            self.assertEqual(output["recognized_count"], 1)
            self.assertEqual(output["sheets"][0]["role"], "BOQ")

            scan = NativeExcelImportAdapter.scan_workbook(path)
            self.assertEqual(scan["sheet_count"], 2)
            self.assertEqual(scan["recognized_sheets"], 1)
            self.assertEqual(scan["sheets"][0]["template_recognition"]["role"], "BOQ")
            self.assertIsNone(scan["sheets"][1]["template_recognition"])

    def test_schedule_parser_accepts_new_template_without_rewriting_source(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "tien_do_moi.xlsx"
            _write(
                path,
                ["Số thứ tự", "Nội dung công tác", "Ngày khởi công", "Ngày hoàn thành", "Thời lượng"],
                [["1", "Đào móng", "01/09/2026", "10/09/2026", 10],
                 ["2", "Xây tường", "", "", 6]],
                sheet_name="Mẫu mới",
            )
            before = hashlib.sha256(path.read_bytes()).hexdigest()
            output = parse_schedule_excel_path(path, status_date="2026-09-01")
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)
            self.assertEqual(output["task_count"], 2)
            self.assertEqual(output["header_row"], 5)
            self.assertEqual(output["tasks"][0]["start_date"], "2026-09-01")
            self.assertEqual(output["tasks"][1]["start_date"], "")
            self.assertEqual(output["template_recognition"]["role"], "SCHEDULE_EXCEL")

    def test_native_import_job_preserves_existing_parser_and_adds_preflight(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "boq.xlsx"
            _write(path, ["Nội dung công tác", "Khối lượng", "Đơn giá"], [["Cáp điện", 10, 50]])
            service = NativeExcelImportAdapter()
            job = {"job_type": "BOQ", "workspace_project_id": 12, "options": {}}
            result = {"written_rows": 1, "verification_status": "HOÀN TẤT"}
            with patch.object(service, "_process_boq", return_value=result) as process:
                output = service.process_job(job, path, {"name": "boq.xlsx", "size": 123})
            process.assert_called_once()
            self.assertEqual(output["written_rows"], 1)
            self.assertEqual(output["template_recognition"]["preferred_role"], "BOQ")
            self.assertEqual(output["template_recognition"]["recognized_count"], 1)

    def test_cancellation_prevents_import(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "boq.xlsx"
            _write(path, ["Nội dung công tác", "Khối lượng", "Đơn giá"], [["Cáp điện", 10, 50]])
            service = NativeExcelImportAdapter()
            with patch.object(service, "_process_boq") as process:
                with self.assertRaises(InterruptedError):
                    service.process_job(
                        {"job_type": "BOQ", "workspace_project_id": 12}, path,
                        {"name": "boq.xlsx", "size": 123}, cancelled=lambda: True,
                    )
            process.assert_not_called()


if __name__ == "__main__":
    unittest.main()
