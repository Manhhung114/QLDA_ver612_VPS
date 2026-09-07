from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import ai_service
import postgres_backend_v622 as pg
from ai_live_context_v622 import install_ai_live_context
from cloud_db import CloudDatabase


class AILiveContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        install_ai_live_context()

    def test_snapshot_reads_current_cost_files_approvals_and_boq_upload(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "qlda_ai_live.db"
            db = CloudDatabase(db_path)
            pid = db.add_project("AI01", "Dự án AI Live")

            with db.connect() as c:
                c.execute(
                    """INSERT INTO cost_budgets(
                           project_id,task_ref,boq_item,quantity,unit,unit_price,budget_total,
                           contract_type,contractor,note,created_at,updated_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (pid, "", "BOQ A", 1, "Gói", 100, 100, "", "", "", "2026-09-07 10:00:00", "2026-09-07 10:00:00"),
                )
                c.execute(
                    """INSERT INTO cost_budgets(
                           project_id,task_ref,boq_item,quantity,unit,unit_price,budget_total,
                           contract_type,contractor,note,created_at,updated_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (pid, "", "BOQ B", 1, "Gói", 200, 200, "", "", "", "2026-09-07 10:01:00", "2026-09-07 10:01:00"),
                )
                cur = c.execute(
                    """INSERT INTO documents(
                           project_id,doc_type,code,subject,status,created_at,updated_at
                       ) VALUES(?,?,?,?,?,?,?)""",
                    (pid, "RFI", "RFI-001", "Kiểm tra AI", "Chờ phản hồi", "2026-09-07 10:02:00", "2026-09-07 10:02:00"),
                )
                doc_id = int(cur.lastrowid)
                c.execute(
                    """INSERT INTO document_attachments(
                           document_id,file_name,mime_type,storage_backend,created_at
                       ) VALUES(?,?,?,?,?)""",
                    (doc_id, "RFI_001.pdf", "application/pdf", "sqlite", "2026-09-07 10:03:00"),
                )
                c.execute(
                    """INSERT INTO approval_workflows(
                           project_id,record_kind,subtype,record_id,record_code,
                           overall_status,current_stage,updated_at
                       ) VALUES(?,?,?,?,?,?,?,?)""",
                    (pid, "document", "RFI", doc_id, "RFI-001", "Đang phê duyệt", "BĐH", "2026-09-07 10:04:00"),
                )
                c.execute(
                    """CREATE TABLE IF NOT EXISTS boq_excel_workbooks(
                           project_id INTEGER PRIMARY KEY,
                           filename TEXT NOT NULL,
                           batch_id TEXT NOT NULL,
                           payload TEXT NOT NULL,
                           updated_at TEXT NOT NULL
                       )"""
                )
                c.execute(
                    "INSERT INTO boq_excel_workbooks(project_id,filename,batch_id,payload,updated_at) VALUES(?,?,?,?,?)",
                    (pid, "BOQ_S234.xlsx", "abc123", "gz1:eA==", "2026-09-07 10:05:00"),
                )

            snapshot = ai_service.ProjectContextBuilder(db_path).build(pid, "chi phí và hồ sơ")
            self.assertIn("Chi phí: BAC 300 VND", snapshot)
            self.assertIn("Nguồn dữ liệu AI: SQLite LIVE", snapshot)
            self.assertIn("LIVE chi phí: 2 dòng BOQ | BAC 300 VND", snapshot)
            self.assertIn("LIVE file: 1 file đính kèm", snapshot)
            self.assertIn("RFI_001.pdf", snapshot)
            self.assertIn("RFI-001", snapshot)
            self.assertIn("[UPLOAD:BOQ] file=BOQ_S234.xlsx", snapshot)
            self.assertIn("AI truy vấn trên toàn bộ 2 dòng", snapshot)

    def test_specific_boq_query_finds_rows_outside_legacy_first_60(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "qlda_ai_boq_search.db"
            db = CloudDatabase(db_path)
            pid = db.add_project("AI02", "Dự án BOQ Search")
            sql = """INSERT INTO cost_budgets(
                       project_id,task_ref,boq_item,quantity,unit,unit_price,budget_total,
                       contract_type,contractor,note,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)"""
            with db.connect() as c:
                # Insert relevant rows first. After 4,045 filler rows are inserted,
                # these records are far outside the old ORDER BY id DESC [:60]
                # snapshot window.
                c.execute(sql, (pid, "HVAC-S2", "Dàn nóng điều hòa VRV", 4, "bộ", 1000, 4000, "", "", "Sheet HVAC - dòng 120", "2026-09-07 09:00:00", "2026-09-07 09:00:00"))
                c.execute(sql, (pid, "HVAC-S3", "Dàn nóng điều hòa VRV", 6, "bộ", 1000, 6000, "", "", "Sheet HVAC - dòng 220", "2026-09-07 09:00:01", "2026-09-07 09:00:01"))
                filler = [
                    (pid, "", f"Cáp điện động lực loại {i}", 1, "m", 10, 10, "", "", "", "2026-09-07 10:00:00", "2026-09-07 10:00:00")
                    for i in range(4045)
                ]
                c.executemany(sql, filler)

            snapshot = ai_service.ProjectContextBuilder(db_path).build(
                pid,
                "Bóc tách số lượng dàn nóng điều hòa trong toàn bộ BOQ",
            )
            self.assertIn("LIVE chi phí: 4,047 dòng BOQ", snapshot)
            self.assertIn("AI truy vấn trên toàn bộ 4,047 dòng", snapshot)
            self.assertIn("Dàn nóng điều hòa VRV", snapshot)
            self.assertIn("SL=10 bộ", snapshot)
            self.assertIn("2 dòng", snapshot)
            self.assertNotIn("các dòng BOQ còn lại 'chưa nạp vào snapshot'", "")

    def test_compat_rows_are_converted_for_postgres_context(self):
        row = pg.CompatRow(["id", "name"], [7, "ABC"])
        converted = ai_service._rows_to_dicts([row])
        self.assertEqual(converted, [{"id": 7, "name": "ABC"}])

    def test_connect_routes_to_postgres_context_when_database_url_exists(self):
        original_resolve = pg.resolve_database_url
        original_ctx = pg._PGConnectionContext
        sentinel = object()
        try:
            pg.resolve_database_url = lambda: "postgresql://example/live"
            pg._PGConnectionContext = lambda url: (sentinel, url)
            builder = ai_service.ProjectContextBuilder("ignored.db")
            context = builder.connect()
            self.assertEqual(context, (sentinel, "postgresql://example/live"))
        finally:
            pg.resolve_database_url = original_resolve
            pg._PGConnectionContext = original_ctx


if __name__ == "__main__":
    unittest.main()
