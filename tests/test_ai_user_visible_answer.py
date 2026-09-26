from __future__ import annotations

import unittest

from qlda.infrastructure.ai.project_chat_complete import sanitize_user_visible_answer


class UserVisibleAIAnswerTests(unittest.TestCase):
    def test_internal_source_markers_are_hidden(self):
        raw = (
            'Dựa trên biên bản gần nhất [DOC:9], thời gian họp là 09:00 '
            '[PDF-OCR:cd8ee241e6ed45d1ba5e6a537a9db085:P2]. '
            'Nhà thầu Sigma tham dự [PDF-OCR:cd8ee241e6ed45d1ba5e6a537a9db085:P1]. '
            '[PDF-OCR-COMPLETE:cd8ee241e6ed45d1ba5e6a537a9db085]'
        )
        cleaned = sanitize_user_visible_answer(raw)

        self.assertNotIn('[DOC:', cleaned)
        self.assertNotIn('[PDF-OCR:', cleaned)
        self.assertNotIn('[PDF-OCR-COMPLETE:', cleaned)
        self.assertIn('Dựa trên biên bản gần nhất, thời gian họp là 09:00.', cleaned)
        self.assertIn('Nhà thầu Sigma tham dự.', cleaned)

    def test_other_internal_live_labels_are_hidden(self):
        raw = (
            'Tiến độ hiện tại 72% [DATA-HUB-ROW]. '
            'Có 12 hồ sơ [EXACT-DOC-SUMMARY:documents=12]. '
            'Phạm vi đã được mở rộng hợp lệ [SCOPE-RECOVERY].'
        )
        cleaned = sanitize_user_visible_answer(raw)

        self.assertEqual(
            cleaned,
            'Tiến độ hiện tại 72%. Có 12 hồ sơ. Phạm vi đã được mở rộng hợp lệ.',
        )

    def test_normal_square_bracket_content_is_not_removed(self):
        raw = 'Khu vực [Khu A] đã hoàn thành; mục [1] là ghi chú của người dùng.'
        self.assertEqual(sanitize_user_visible_answer(raw), raw)


if __name__ == '__main__':
    unittest.main()
