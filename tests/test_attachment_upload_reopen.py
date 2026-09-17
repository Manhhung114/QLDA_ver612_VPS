from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "src/qlda/runtime_core/document_management_vps_ui.py"


class AttachmentUploadReopenTests(unittest.TestCase):
    def test_existing_record_attachment_button_prepares_upload(self):
        text = MODULE.read_text(encoding="utf-8")
        self.assertIn('"Đính kèm file" in text', text)
        self.assertIn("_prepare_selected_upload(st)", text)
        self.assertIn("st.form_submit_button = _form_submit_button", text)

    def test_refresh_file_db_also_reopens_upload(self):
        text = MODULE.read_text(encoding="utf-8")
        self.assertIn('"Làm mới file / File DB" in text', text)
        self.assertIn("st.button = _button", text)

    def test_documents_drawings_and_meeting_minutes_are_supported(self):
        text = MODULE.read_text(encoding="utf-8")
        self.assertIn('name in {"render_document_type", "_render_approval_document_type"}', text)
        self.assertIn('name in {"render_drawing_type", "_render_approval_shopdrawing_type"}', text)
        self.assertIn('name == "render_meeting_minutes_simple"', text)
        self.assertIn('kind not in {"document", "drawing"}', text)


if __name__ == "__main__":
    unittest.main()
