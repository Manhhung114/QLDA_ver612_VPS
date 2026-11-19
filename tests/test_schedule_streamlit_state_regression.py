from __future__ import annotations

import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

from qlda.presentation.streamlit.schedule_unified_import_ui import render_schedule_import
from qlda.runtime_core.project_store import CloudDatabase
from tests.test_schedule_excel_multiline import _complex_workbook
from tests.test_schedule_unified_import_ui import Uploaded


class ProtectedWidgetState(dict):
    """Minimal Streamlit state model: widget keys cannot be assigned after render."""

    def __init__(self):
        super().__init__()
        self.instantiated = set()

    def __setitem__(self, key, value):
        if key in self.instantiated:
            raise RuntimeError(
                f"StreamlitWidgetAlreadyInstantiatedError: {key} has been instantiated"
            )
        super().__setitem__(key, value)

    def next_run(self):
        self.instantiated.clear()


class FakeStreamlit:
    def __init__(self, uploaded):
        self.uploaded = uploaded
        self.session_state = ProtectedWidgetState()
        self.pressed = set()
        self.messages = []
        self.reruns = 0

    def button(self, label, *, key, **kwargs):
        return key in self.pressed and not kwargs.get("disabled", False)

    def file_uploader(self, *args, **kwargs):
        return self.uploaded

    def radio(self, label, options, *, key, **kwargs):
        if key not in self.session_state:
            self.session_state[key] = options[0]
        self.session_state.instantiated.add(key)
        return self.session_state[key]

    def checkbox(self, label, *, key, **kwargs):
        if key not in self.session_state:
            self.session_state[key] = False
        self.session_state.instantiated.add(key)
        return self.session_state[key]

    def spinner(self, *args, **kwargs):
        return nullcontext()

    def expander(self, *args, **kwargs):
        return nullcontext()

    def columns(self, count):
        return [self] * count

    def metric(self, *args, **kwargs):
        return None

    def dataframe(self, *args, **kwargs):
        return None

    def rerun(self):
        self.reruns += 1

    def __getattr__(self, name):
        if name in {"markdown", "caption", "warning", "success", "error", "info"}:
            return lambda message: self.messages.append(str(message))
        raise AttributeError(name)


class StreamlitScheduleImportStateTests(unittest.TestCase):
    def _confirm_import(self, st, db, pid):
        preview_key = f"schedule_unified_preview_{pid}"
        confirm_key = f"schedule_unified_commit_{pid}"
        accept_key = f"schedule_unified_upload_accept_{pid}"

        st.pressed = {preview_key}
        render_schedule_import(
            st, db, pid, status_date="2026-11-19",
            can_update=True,
        )
        self.assertIn(accept_key, st.session_state.instantiated)
        self.assertFalse(st.session_state[accept_key])
        self.assertEqual(len(db.tasks(pid)), 0)

        # Simulate a Streamlit client checking the checkbox and clicking Import.
        st.session_state.next_run()
        st.session_state[accept_key] = True
        st.pressed = {confirm_key}
        render_schedule_import(
            st, db, pid, status_date="2026-11-19",
            can_update=True,
        )
        self.assertEqual(st.reruns, 1)
        self.assertTrue(any("Đã nhập" in message for message in st.messages))

    def test_excel_import_does_not_mutate_instantiated_checkbox(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            xlsx = root / "Tien_Do.xlsx"
            _complex_workbook(xlsx)
            st = FakeStreamlit(Uploaded(xlsx.name, xlsx.read_bytes()))
            db = CloudDatabase(root / "qlda.db")
            pid = db.add_project("X-EXCEL", "Excel state regression")
            self._confirm_import(st, db, pid)
            rows = db.tasks(pid)
            self.assertEqual(len(rows), 6)
            self.assertEqual(
                (rows[0]["start_date"], rows[0]["end_date"]),
                ("2026-10-01", "2027-06-30"),
            )
            # Excel import must not rewrite source start/finish/duration.
            self.assertEqual(rows[0]["duration"], 273)

    def test_mpp_import_does_not_mutate_instantiated_checkbox(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            st = FakeStreamlit(Uploaded("test.mpp", b"mock-mpp"))
            db = CloudDatabase(root / "qlda.db")
            pid = db.add_project("X-MPP", "MPP state regression")
            info = {"start_date": "2026-10-01", "end_date": "2026-12-15"}
            tasks = [{
                "source_uid": 4,
                "task_id": 4,
                "name": "Lắp đặt thiết bị",
                "start_date": "2026-10-01",
                "end_date": "2026-12-15",
                "duration": 54,
                "outline_level": 1,
            }]
            with patch(
                "qlda.presentation.streamlit.schedule_unified_import_ui.read_mpp",
                return_value=(info, tasks),
            ):
                self._confirm_import(st, db, pid)
            rows = db.tasks(pid)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["source_type"], "mpp")
            self.assertEqual(rows[0]["end_date"], "2026-12-15")


if __name__ == "__main__":
    unittest.main()
