from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from legal_qlda_v622 import (
    EXTRA_CONSTRUCTION_KEYWORDS,
    EXTRA_TVPL_SYNC_QUERIES,
    _collect_bxd_circulars,
    _is_bxd_circular,
    install_legal_qlda,
    purge_drafts,
)
from v622_legal_qlda_patch import PATCH_MARKER, patch_legal_qlda


class _Repo:
    def __init__(self, path: Path):
        self.path = path

    def connect(self):
        return sqlite3.connect(self.path)


class LegalQLDAV622Test(unittest.TestCase):
    def test_generated_ui_has_no_draft_controls(self):
        source = Path("dist/streamlit_app.py").read_text(encoding="utf-8")
        patched = patch_legal_qlda(source)
        compile(patched, "streamlit_app_legal_qlda.py", "exec")
        self.assertIn(PATCH_MARKER, patched)
        self.assertIn("📚 QLXD mở rộng - TVPL", patched)
        self.assertIn("🔄 Cập nhật QLXD", patched)
        self.assertIn("purge_drafts(legal_repo)", patched)
        self.assertNotIn("📝 Dự thảo BXD", patched)
        self.assertNotIn("Hiển thị cả dự thảo đang lấy ý kiến", patched)
        self.assertNotIn('m4.metric("Dự thảo", drafts)', patched)
        self.assertIn("legal_repo.list_documents(keyword, category, status, source, False)", patched)

    def test_install_expands_qlda_scope_and_sync_all_excludes_drafts(self):
        import legal_documents as ld

        install_legal_qlda()
        self.assertTrue(getattr(ld, "_v622_legal_qlda_installed", False))
        self.assertGreaterEqual(len(EXTRA_TVPL_SYNC_QUERIES), 30)
        self.assertGreaterEqual(len(EXTRA_CONSTRUCTION_KEYWORDS), 40)
        self.assertIn("nghiệm thu công việc", ld.CONSTRUCTION_KEYWORDS)
        self.assertIn("BIM mô hình thông tin công trình xây dựng", ld.TVPL_SYNC_QUERIES)
        self.assertIn("phân cấp công trình xây dựng TT-BXD", ld.TVPL_SYNC_QUERIES)

        seen = []
        original = ld.sync_source
        try:
            ld.sync_source = lambda repo, source: seen.append(source) or {"source": source}
            out = ld.sync_all(object())
        finally:
            ld.sync_source = original
        self.assertEqual(seen, ["vbpl", "vsqi", "tvpl"])
        self.assertEqual(len(out), 3)
        self.assertNotIn("moc_drafts", seen)

    def test_tt_bxd_year_index_keeps_bxd_and_rejects_other_ministries(self):
        calls = []

        def fake_search(query: str, limit: int = 20):
            calls.append((query, limit))
            if "2021" not in query:
                return []
            return [
                {
                    "number": "06/2021/TT-BXD",
                    "title": "Thông tư 06/2021/TT-BXD quy định về phân cấp công trình xây dựng",
                    "source_url": "https://thuvienphapluat.vn/van-ban/Xay-dung-Do-thi/Thong-tu-06-2021-TT-BXD-480818.aspx",
                    "is_draft": 0,
                },
                {
                    "number": "06/2021/TT-BYT",
                    "title": "Thông tư Bộ Y tế",
                    "source_url": "https://thuvienphapluat.vn/van-ban/y-te/example.aspx",
                    "is_draft": 0,
                },
                {
                    "number": "07/2021/TT-BXD",
                    "title": "Dự thảo Thông tư 07/2021/TT-BXD",
                    "source_url": "https://example.test/draft",
                    "is_draft": 1,
                },
            ]

        rows = _collect_bxd_circulars(fake_search, start_year=2021, end_year=2021, per_year=35)
        self.assertEqual(len(calls), 1)
        self.assertIn("2021", calls[0][0])
        self.assertIn("TT-BXD", calls[0][0])
        self.assertEqual([r["number"] for r in rows], ["06/2021/TT-BXD"])
        self.assertTrue(_is_bxd_circular(rows[0]))
        self.assertFalse(_is_bxd_circular({"number": "06/2021/TT-BYT", "title": "Khác"}))

    def test_purge_drafts_removes_old_draft_rows_and_logs_only(self):
        with tempfile.TemporaryDirectory() as td:
            db_path = Path(td) / "legal.sqlite"
            with sqlite3.connect(db_path) as c:
                c.execute(
                    """CREATE TABLE legal_documents(
                        id INTEGER PRIMARY KEY, category TEXT, status TEXT, is_draft INTEGER, title TEXT
                    )"""
                )
                c.execute(
                    """CREATE TABLE legal_sync_log(
                        id INTEGER PRIMARY KEY, source_name TEXT
                    )"""
                )
                c.executemany(
                    "INSERT INTO legal_documents(id,category,status,is_draft,title) VALUES(?,?,?,?,?)",
                    [
                        (1, "Dự thảo", "Dự thảo / lấy ý kiến", 1, "Draft A"),
                        (2, "Dự thảo QCVN", "Đang lấy ý kiến", 1, "Draft B"),
                        (3, "Nghị định", "Còn hiệu lực", 0, "Official C"),
                        (4, "TCVN", "Cần kiểm tra hiệu lực", 0, "Standard D"),
                    ],
                )
                c.executemany(
                    "INSERT INTO legal_sync_log(id,source_name) VALUES(?,?)",
                    [(1, "Dự thảo Bộ Xây dựng"), (2, "Thư Viện Pháp Luật")],
                )

            deleted = purge_drafts(_Repo(db_path))
            self.assertEqual(deleted, 2)
            with sqlite3.connect(db_path) as c:
                rows = c.execute("SELECT title FROM legal_documents ORDER BY id").fetchall()
                logs = c.execute("SELECT source_name FROM legal_sync_log ORDER BY id").fetchall()
            self.assertEqual(rows, [("Official C",), ("Standard D",)])
            self.assertEqual(logs, [("Thư Viện Pháp Luật",)])


if __name__ == "__main__":
    unittest.main()
