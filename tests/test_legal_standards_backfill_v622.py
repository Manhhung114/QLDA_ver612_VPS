from __future__ import annotations

import unittest
from pathlib import Path

from legal_standards_backfill_v622 import (
    QCVN_PRIORITY_EXACT_NUMBERS,
    TCVN_PRIORITY_EXACT_NUMBERS,
    _collect_qcvn_bxd,
    _collect_tcvn_construction,
    _is_qcvn_bxd,
    _is_tcvn,
)
from v622_legal_qlda_patch import patch_legal_qlda


class LegalStandardsBackfillV622Test(unittest.TestCase):
    def test_qcvn_family_and_exact_backfill_recovers_broad_query_miss(self):
        calls = []
        target = {
            "category": "QCVN",
            "number": "QCVN 06:2022/BXD",
            "title": "Quy chuẩn kỹ thuật quốc gia về an toàn cháy cho nhà và công trình",
            "source_url": "https://thuvienphapluat.vn/van-ban/Xay-dung-Do-thi/example-qcvn-06.aspx",
            "is_draft": 0,
        }

        def fake_search(query: str, limit: int = 20):
            calls.append((query, limit))
            # Query tự nhiên rộng mô phỏng trường hợp nguồn trả văn bản khác trước.
            if query == "Quy chuẩn kỹ thuật quốc gia Bộ Xây dựng 2022":
                return [{
                    "category": "QCVN",
                    "number": "QCVN 01:2022/BYT",
                    "title": "Quy chuẩn lĩnh vực khác",
                    "source_url": "https://example.test/other-ministry",
                    "is_draft": 0,
                }]
            # Family suffix và exact-number mới cứu được QCVN cần lấy.
            if query in {":2022/BXD", "QCVN 2022/BXD", "QCVN 06:2022/BXD"}:
                return [
                    target,
                    {
                        "category": "Dự thảo QCVN",
                        "number": "QCVN 99:2022/BXD",
                        "title": "Dự thảo quy chuẩn",
                        "source_url": "https://example.test/draft-qcvn",
                        "is_draft": 1,
                    },
                ]
            return []

        rows = _collect_qcvn_bxd(fake_search, start_year=2022, end_year=2022, per_year=50)
        numbers = [r["number"] for r in rows]
        self.assertIn("QCVN 06:2022/BXD", numbers)
        self.assertEqual(numbers.count("QCVN 06:2022/BXD"), 1)
        self.assertNotIn("QCVN 99:2022/BXD", numbers)
        self.assertTrue(any(q == ":2022/BXD" for q, _ in calls))
        self.assertTrue(any(q == "QCVN 06:2022/BXD" for q, _ in calls))
        self.assertTrue(_is_qcvn_bxd(target))
        self.assertFalse(_is_qcvn_bxd({"number": "QCVN 01:2022/BYT", "title": "Khác"}))
        self.assertIn("QCVN 06:2022/BXD", QCVN_PRIORITY_EXACT_NUMBERS)

    def test_tcvn_family_and_exact_backfill_recovers_current_version(self):
        calls = []
        current = {
            "category": "TCVN",
            "number": "TCVN 2737:2023",
            "title": "Tải trọng và tác động",
            "source_url": "https://tieuchuan.vsqi.gov.vn/tieuchuan/view?sohieu=TCVN+2737%3A2023",
            "is_draft": 0,
        }
        old = {
            "category": "TCVN",
            "number": "TCVN 2737:1995",
            "title": "Tải trọng và tác động - Tiêu chuẩn thiết kế",
            "source_url": "https://tieuchuan.vsqi.gov.vn/tieuchuan/view?sohieu=TCVN+2737%3A1995",
            "is_draft": 0,
        }

        def fake_search(query: str, limit: int = 20):
            calls.append((query, limit))
            # Family/base query chỉ thấy bản cũ, exact query phải cứu bản hiện hành.
            if query == "TCVN 2737":
                return [old]
            if query == "TCVN 2737:2023":
                return [current]
            if query == "TCVN xây dựng nhà công trình":
                return [old]
            return []

        rows = _collect_tcvn_construction(fake_search, family_limit=45)
        numbers = [r["number"] for r in rows]
        self.assertIn("TCVN 2737:2023", numbers)
        self.assertIn("TCVN 2737:1995", numbers)
        self.assertEqual(numbers.count("TCVN 2737:2023"), 1)
        self.assertTrue(any(q == "TCVN 2737" for q, _ in calls))
        self.assertTrue(any(q == "TCVN 2737:2023" for q, _ in calls))
        self.assertTrue(_is_tcvn(current))
        self.assertIn("TCVN 2737:2023", TCVN_PRIORITY_EXACT_NUMBERS)

    def test_legal_ui_installs_standard_backfill(self):
        source = Path("dist/streamlit_app.py").read_text(encoding="utf-8")
        patched = patch_legal_qlda(source)
        compile(patched, "streamlit_app_legal_standards.py", "exec")
        self.assertIn("from legal_standards_backfill_v622 import install_legal_standard_backfill", patched)
        self.assertIn("install_legal_standard_backfill()", patched)
        # Giữ nguyên nhãn nút V6.22 để không thay đổi thói quen người dùng.
        self.assertIn("📐 QCVN / TCVN - VSQI", patched)
        self.assertIn("📚 QLXD mở rộng - TVPL", patched)


if __name__ == "__main__":
    unittest.main()
