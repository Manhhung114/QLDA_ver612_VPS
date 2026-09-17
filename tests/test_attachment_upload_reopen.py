from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "src/qlda/runtime_core/document_management_vps_ui.py"


class AttachmentUploadReopenTests(unittest.TestCase):
    def test_existing_record_attachment_button_uses_preclick_callback(self):
        text = MODULE.read_text(encoding="utf-8")
        self.assertIn('"Đính kèm file" in text', text)
        self.assertIn("_install_preclick_upload_callback", text)
        self.assertIn("out[\"on_click\"] = _before_rerun_callback", text)
        self.assertIn("st.form_submit_button = _form_submit_button", text)

    def test_refresh_file_db_prepares_ticket_before_legacy_rerun(self):
        text = MODULE.read_text(encoding="utf-8")
        self.assertIn('"Làm mới file / File DB" in text', text)
        self.assertIn("_prepare_upload_context(st, context)", text)
        self.assertIn("st.button = _button", text)
        self.assertIn("VPS ATTACHMENT REOPEN V3", text)

    def test_existing_callbacks_are_preserved(self):
        text = MODULE.read_text(encoding="utf-8")
        self.assertIn('existing_callback = out.pop("on_click", None)', text)
        self.assertIn("existing_callback(*existing_args, **existing_kwargs)", text)

    def test_documents_drawings_and_meeting_minutes_are_supported(self):
        text = MODULE.read_text(encoding="utf-8")
        self.assertIn('name in {"render_document_type", "_render_approval_document_type"}', text)
        self.assertIn('name in {"render_drawing_type", "_render_approval_shopdrawing_type"}', text)
        self.assertIn('name == "render_meeting_minutes_simple"', text)
        self.assertIn('kind not in {"document", "drawing"}', text)


if __name__ == "__main__":
    unittest.main()
