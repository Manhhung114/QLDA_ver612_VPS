from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from qlda.presentation.streamlit.schedule_unified_import_ui import (
    _read_uploaded_schedule, classify_schedule_file,
    render_schedule_delete_all,
)
from qlda.runtime_core.project_store import CloudDatabase
from tests.test_schedule_excel_multiline import _complex_workbook


class Uploaded:
    def __init__(self, name: str, data: bytes):
        self.name = name
        self.data = data
        self.size = len(data)

    def getvalue(self) -> bytes:
        return self.data


class ScheduleUnifiedUploadTests(unittest.TestCase):
    def test_auto_routes_excel_and_project_without_crossing_parsers(self):
        for extension in (".xlsx", ".xlsm"):
            self.assertEqual(classify_schedule_file("Tien_do" + extension), "excel")
        for extension in (".mpp", ".xml"):
            self.assertEqual(classify_schedule_file("Tien_do" + extension), "project")
        self.assertEqual(classify_schedule_file("MODEL.MPP"), "project")
        with self.assertRaisesRegex(ValueError, "Chỉ nhận Excel"):
            classify_schedule_file("invalid.csv")
        with self.assertRaisesRegex(ValueError, "Chỉ nhận Excel"):
            classify_schedule_file("legacy.xls")

    def test_excel_preview_preserves_source_calendar_dates_and_missing_dates(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "Rev1.xlsx"
            _complex_workbook(path)
            data = path.read_bytes()
            preview = _read_uploaded_schedule(Uploaded(path.name, data), status_date="2026-11-19")
            self.assertEqual(preview["kind"], "excel")
            self.assertEqual(preview["sha256"], hashlib.sha256(data).hexdigest())
            self.assertEqual(preview["result"]["header_row"], 7)
            self.assertEqual(preview["task_count"], 6)
            self.assertEqual(preview["dated_count"], 4)
            self.assertEqual(preview["undated_count"], 2)
            self.assertEqual(preview["tasks"][0]["start_date"], "2026-10-01")
            self.assertEqual(preview["tasks"][0]["end_date"], "2027-06-30")
            self.assertEqual(preview["tasks"][0]["duration"], 273)
            self.assertEqual(preview["tasks"][3]["start_date"], "")

    def test_project_preview_supports_both_mpp_and_xml_and_preserves_fields(self):
        task = {
            "task_id": 1, "source_uid": 12, "name": "Lắp đặt tủ điện",
            "start_date": "2026-11-10", "end_date": "2026-12-12",
            "duration": 32, "wbs": "1.1",
        }
        info = {"name": "Kế hoạch Project", "start_date": "2026-11-10", "end_date": "2026-12-12"}
        for name in ("progress.mpp", "progress.xml"):
            with self.subTest(name=name), patch(
                "qlda.presentation.streamlit.schedule_unified_import_ui.read_mpp",
                return_value=(info, [task]),
            ) as mock_read:
                preview = _read_uploaded_schedule(Uploaded(name, b"project-content"), status_date="2026-11-19")
                mock_read.assert_called_once()
                self.assertEqual(preview["kind"], "project")
                self.assertEqual(preview["task_count"], 1)
                self.assertEqual(preview["project_info"], info)
                self.assertEqual(preview["tasks"][0]["end_date"], "2026-12-12")

    def test_rejects_missing_and_oversized_file_before_parsing(self):
        with self.assertRaisesRegex(ValueError, "trống"):
            _read_uploaded_schedule(Uploaded("empty.xlsx", b""), status_date="2026-11-19")


class FakeSt:
    def __init__(self):
        self.session_state = {}
        self.pressed = set()
        self.messages = []
        self.reruns = 0

    def markdown(self, message):
        self.messages.append(message)

    def caption(self, message):
        self.messages.append(message)

    def warning(self, message):
        self.messages.append(message)

    def success(self, message):
        self.messages.append(message)

    def error(self, message):
        self.messages.append(message)

    def button(self, label, *, key, **kwargs):
        return key in self.pressed

    def columns(self, count):
        return [self] * count

    def rerun(self):
        self.reruns += 1


class QuickDeleteAllTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = CloudDatabase(Path(self.tmp.name) / "app.db")
        self.pid = self.db.add_project("A", "Dự án A")
        self.other = self.db.add_project("B", "Dự án B")
        for pid, label, source in (
            (self.pid, "Excel A", "schedule_excel:sha"),
            (self.pid, "MPP A", "mpp"),
            (self.pid, "Thủ công A", "manual"),
            (self.other, "Excel B", "schedule_excel:sha"),
        ):
            self.db.add_task(pid, {
                "name": label, "start_date": "2026-11-01",
                "end_date": "2026-12-01", "source_type": source,
            })

    def test_two_click_delete_clears_entire_active_workspace_only(self):
        st = FakeSt()
        st.pressed = {f"schedule_delete_all_begin_{self.pid}"}
        render_schedule_delete_all(st, self.db, self.pid, can_update=True, is_admin=True)
        self.assertEqual(len(self.db.tasks(self.pid)), 3)
        self.assertEqual(st.session_state[f"schedule_delete_everything_count_{self.pid}"], 3)
        st.pressed = {f"schedule_delete_all_confirm_{self.pid}"}
        render_schedule_delete_all(st, self.db, self.pid, can_update=True, is_admin=True)
        self.assertEqual(len(self.db.tasks(self.pid)), 0)
        self.assertEqual(len(self.db.tasks(self.other)), 1)
        self.assertEqual(st.reruns, 1)

    def test_cancel_does_not_delete(self):
        st = FakeSt()
        st.pressed = {f"schedule_delete_all_begin_{self.pid}"}
        render_schedule_delete_all(st, self.db, self.pid, can_update=True, is_admin=True)
        st.pressed = {f"schedule_delete_all_cancel_{self.pid}"}
        render_schedule_delete_all(st, self.db, self.pid, can_update=True, is_admin=True)
        self.assertEqual(len(self.db.tasks(self.pid)), 3)

    def test_non_admin_cannot_delete_even_with_forged_confirmation(self):
        st = FakeSt()
        st.session_state[f"schedule_delete_everything_open_{self.pid}"] = True
        st.session_state[f"schedule_delete_everything_count_{self.pid}"] = 3
        st.pressed = {f"schedule_delete_all_confirm_{self.pid}"}
        render_schedule_delete_all(st, self.db, self.pid, can_update=True, is_admin=False)
        self.assertEqual(len(self.db.tasks(self.pid)), 3)


if __name__ == "__main__":
    unittest.main()
