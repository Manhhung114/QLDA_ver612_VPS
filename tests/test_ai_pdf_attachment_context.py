from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from qlda.infrastructure.ai import pdf_attachment_context as ctx


class _Result:
    def __init__(self, rows):
        self.rows = list(rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)


class _Connection:
    def __init__(self, storage_path: str):
        self.storage_path = storage_path
        self.calls = []

    def execute(self, sql, params=()):
        text = " ".join(str(sql).split()).lower()
        self.calls.append((text, params))
        if "select id,code from projects" in text:
            return _Result([{"id": 101, "code": "SIGMA-SCOPE"}])
        if " from documents " in f" {text} ":
            return _Result([
                {
                    "id": 10,
                    "project_id": 101,
                    "doc_type": "BBHT",
                    "code": "B12-MEP-002",
                    "subject": "Nhà thầu không nghiệm thu nội bộ - ĐIỆN - Hầm B1, B2",
                    "description": "Metadata của hồ sơ",
                    "issue_date": "2026-09-25",
                    "updated_at": "2026-09-25 09:00:00",
                    "created_at": "2026-09-25 09:00:00",
                },
                {
                    "id": 4,
                    "project_id": 101,
                    "doc_type": "BBHT",
                    "code": "S2-MEP-001",
                    "subject": "Biên bản bàn giao mặt bằng",
                    "description": "Biên bản cũ",
                    "issue_date": "2026-09-16",
                    "updated_at": "2026-09-16 09:00:00",
                    "created_at": "2026-09-16 09:00:00",
                },
            ])
        if " from qlda_local_files " in f" {text} ":
            return _Result([
                {
                    "id": "file-b12",
                    "project_code": "SIGMA-SCOPE",
                    "kind": "document",
                    "subtype": "BBHT",
                    "record_code": "B12-MEP-002",
                    "name": "BBHT_B12_MEP_002.pdf",
                    "mime_type": "application/pdf",
                    "size": 12345,
                    "sha256": "abc",
                    "storage_path": self.storage_path,
                    "upload_purpose": "evidence",
                    "created_at": "2026-09-25 10:00:00",
                    "modified_at": "2026-09-25 10:00:00",
                }
            ])
        return _Result([])


class PDFAttachmentContextTests(unittest.TestCase):
    def _build(self, extraction):
        with tempfile.TemporaryDirectory() as tmp:
            rel = "projects/SIGMA-SCOPE/document/BBHT/B12-MEP-002/file-b12__BBHT.pdf"
            target = Path(tmp) / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"%PDF-dummy")
            connection = _Connection(rel)
            existing = {"projects", "documents", "qlda_local_files"}
            with (
                patch.dict(os.environ, {"QLDA_LOCAL_STORAGE_ROOT": tmp}, clear=False),
                patch.object(ctx, "_table_exists", side_effect=lambda _c, table: table in existing),
                patch.object(ctx, "_extract_pdf_pages", return_value=extraction),
            ):
                text = ctx.build_pdf_attachment_context(
                    connection,
                    [101],
                    "Ghi rõ nội dung biên bản hiện trường gần đây nhất",
                    max_files=4,
                    max_chars=18000,
                )
            return text, connection

    def test_latest_field_minutes_reads_actual_pdf_pages(self):
        text, connection = self._build(
            {
                "status": "ok",
                "page_count": 2,
                "pages": [
                    (1, "Nội dung PDF thật: khu vực B1 chưa nghiệm thu nội bộ trước khi mời TVGS."),
                    (2, "Yêu cầu nhà thầu hoàn tất checklist và khắc phục trước ngày 27/09/2026."),
                ],
                "blank_pages": 0,
                "truncated": False,
            }
        )
        self.assertIn("[PDF-FILE:file-b12]", text)
        self.assertIn("[PDF:file-b12:P1]", text)
        self.assertIn("khu vực B1 chưa nghiệm thu nội bộ", text)
        self.assertIn("[PDF:file-b12:P2]", text)
        self.assertIn("B12-MEP-002", text)
        self.assertNotIn("S2-MEP-001", text)
        scoped = [params for sql, params in connection.calls if "project_id=any" in sql]
        self.assertTrue(scoped)
        self.assertTrue(all(list(params[0]) == [101] for params in scoped if params))

    def test_scan_pdf_is_never_treated_as_readable_metadata(self):
        text, _ = self._build(
            {
                "status": "no_text",
                "page_count": 3,
                "pages": [],
                "blank_pages": 3,
                "truncated": False,
            }
        )
        self.assertIn("[PDF-SCAN-NO-TEXT:file-b12]", text)
        self.assertIn("Không được suy nội dung từ tên file hoặc metadata", text)

    def test_storage_path_cannot_escape_authorized_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"QLDA_LOCAL_STORAGE_ROOT": tmp}, clear=False):
                self.assertIsNone(ctx._safe_path("../../etc/passwd"))


if __name__ == "__main__":
    unittest.main()
