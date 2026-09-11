from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from legal_pccc_backfill_v622 import (
    OFFICIAL_CORE_DOCS,
    PCCC_SYNC_QUERIES,
    clear_pccc_seed_for_tests,
    seed_official_legal_documents,
)
from legal_qlda_v622 import (
    BXD_PRIORITY_EXACT_NUMBERS,
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


class _SeedRepo:
    def __init__(self, path: str = "seed-test"):
        self.path = path
        self.docs: list[dict] = []
        self.calls = 0

    def upsert_many(self, docs, source_name=""):
        self.calls += 1
        self.docs = [dict(x) for x in docs]
        return {"found": len(self.docs), "added": len(self.docs), "updated": 0}


class LegalQLDAV622Test(unittest.TestCase):
    def test_generated_ui_has_no_draft_controls_and_has_pccc(self):
        source = Path("dist/streamlit_app.py").read_text(encoding="utf-8")
        patched = patch_legal_qlda(source)
        compile(patched, "streamlit_app_legal_qlda.py", "exec")
        self.assertIn(PATCH_MARKER, patched)
        self.assertIn("📚 QLXD mở rộng - TVPL", patched)
        self.assertIn("🔄 Cập nhật QLXD", patched)
        self.assertIn("🔥 PCCC / CNCH", patched)
        self.assertIn("install_legal_pccc_backfill()", patched)
        self.assertIn("seed_official_legal_documents(legal_repo)", patched)
        self.assertIn('m4.metric("PCCC / CNCH", pccc)', patched)
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
        self.assertIn("06/2021/TT-BXD", BXD_PRIORITY_EXACT_NUMBERS)

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

    def test_tt_bxd_family_backfill_reproduces_real_broad_query_miss(self):
        """Broad natural-language search misses 06/2021; family/exact search must recover it."""
        calls = []

        target = {
            "number": "06/2021/TT-BXD",
            "title": "Thông tư 06/2021/TT-BXD quy định về phân cấp công trình xây dựng",
            "source_url": "https://thuvienphapluat.vn/van-ban/Xay-dung-Do-thi/Thong-tu-06-2021-TT-BXD-480818.aspx",
            "is_draft": 0,
        }

        def fake_search(query: str, limit: int = 20):
            calls.append((query, limit))
            # Đây là hành vi gây lỗi của V3: query rộng trả kết quả khác, không có 06/2021.
            if query == "Thông tư Bộ Xây dựng 2021 TT-BXD":
                return [
                    {
                        "number": "06/2021/TT-BYT",
                        "title": "Thông tư Bộ Y tế",
                        "source_url": "https://thuvienphapluat.vn/van-ban/y-te/example.aspx",
                        "is_draft": 0,
                    }
                ]
            # Family suffix và exact-number query mới lấy đúng văn bản cần thiết.
            if query in {"/2021/TT-BXD", "2021/TT-BXD", "06/2021/TT-BXD"}:
                return [
                    target,
                    {
                        "number": "07/2021/TT-BXD",
                        "title": "Dự thảo Thông tư 07/2021/TT-BXD",
                        "source_url": "https://example.test/draft",
                        "is_draft": 1,
                    },
                ]
            return []

        rows = _collect_bxd_circulars(fake_search, start_year=2021, end_year=2021, per_year=40)
        numbers = [r["number"] for r in rows]
        self.assertIn("06/2021/TT-BXD", numbers)
        self.assertEqual(numbers.count("06/2021/TT-BXD"), 1)
        self.assertNotIn("07/2021/TT-BXD", numbers)
        self.assertTrue(any(q == "/2021/TT-BXD" for q, _ in calls))
        self.assertTrue(any(q == "06/2021/TT-BXD" for q, _ in calls))
        self.assertTrue(_is_bxd_circular(target))
        self.assertFalse(_is_bxd_circular({"number": "06/2021/TT-BYT", "title": "Khác"}))

    def test_official_seed_guarantees_requested_tt06_and_current_pccc_core(self):
        clear_pccc_seed_for_tests()
        repo = _SeedRepo()
        stats = seed_official_legal_documents(repo)
        self.assertEqual(stats["found"], len(OFFICIAL_CORE_DOCS))
        numbers = {str(d.get("number", "")) for d in repo.docs}
        for required in (
            "06/2021/TT-BXD",
            "06/VBHN-BXD",
            "55/2024/QH15",
            "105/2025/NĐ-CP",
            "106/2025/NĐ-CP",
            "69/2026/NĐ-CP",
            "36/2025/TT-BCA",
            "63/2025/TT-BXD",
            "QCVN 06:2022/BXD",
            "QCVN 03:2023/BCA",
            "QCVN 10:2025/BCA",
            "TCVN 3890:2023",
            "TCVN 7336:2021",
            "TCVN 7568-14:2025",
        ):
            self.assertIn(required, numbers)

        tt06 = next(d for d in repo.docs if d.get("number") == "06/2021/TT-BXD")
        self.assertEqual(
            tt06["source_url"],
            "https://congbao.chinhphu.vn/van-ban/thong-tu-so-06-2021-tt-bxd-33988.htm",
        )
        self.assertIn("Công báo", tt06["source_name"])

        # Process-level guard prevents write amplification on Streamlit reruns.
        second = seed_official_legal_documents(repo)
        self.assertEqual(second, {"found": 0, "added": 0, "updated": 0})
        self.assertEqual(repo.calls, 1)

    def test_pccc_queries_cover_legal_design_equipment_and_standards(self):
        joined = " | ".join(PCCC_SYNC_QUERIES).lower()
        for token in (
            "luật phòng cháy",
            "xử phạt",
            "thẩm định thiết kế",
            "nghiệm thu",
            "qcvn 06",
            "qcvn 10",
            "tcvn",
            "báo cháy",
            "chữa cháy tự động",
        ):
            self.assertIn(token, joined)

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
