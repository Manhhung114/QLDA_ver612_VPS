from __future__ import annotations

import unittest

from qlda.presentation.streamlit.document_management_uniform_interaction import (
    _document_option_label,
    _drawing_option_label,
)


class DocumentSelectionLabelTests(unittest.TestCase):
    def test_drawing_label_contains_id_code_revision_and_title(self):
        rows = [
            {
                "id": 4,
                "drawing_no": "S234-MEP-001",
                "revision": "00 / A / C01",
                "title": "HỒ SƠ HẠ TẦNG TUYẾN ĐƯỜNG E3",
            }
        ]
        self.assertEqual(
            _drawing_option_label(4, rows),
            "#4 - S234-MEP-001 Rev.00 / A / C01 - HỒ SƠ HẠ TẦNG TUYẾN ĐƯỜNG E3",
        )

    def test_document_label_contains_id_code_and_subject(self):
        rows = [
            {
                "id": 7,
                "code": "S2-MEP-001",
                "subject": "Biên bản bàn giao mặt bằng",
            }
        ]
        self.assertEqual(
            _document_option_label(7, rows),
            "#7 - S2-MEP-001 - Biên bản bàn giao mặt bằng",
        )

    def test_none_keeps_add_new_option(self):
        self.assertEqual(_drawing_option_label(None, []), "➕ Thêm mới")
        self.assertEqual(_document_option_label(None, []), "➕ Thêm mới")


if __name__ == "__main__":
    unittest.main()
