from __future__ import annotations

import io
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
    def _build(self, extraction, ocr_result=None):
        if ocr_result is None:
            ocr_result = {
                "status": "not_needed",
                "pages": [],
                "missed_pages": [],
                "from_cache": True,
            }
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
                patch.object(ctx, "_ocr_scanned_pdf", return_value=ocr_result) as ocr_mock,
            ):
                text = ctx.build_pdf_attachment_context(
                    connection,
                    [101],
                    "Ghi rõ nội dung biên bản hiện trường gần đây nhất",
                    max_files=4,
                    max_chars=18000,
                )
            return text, connection, ocr_mock

    def test_latest_field_minutes_reads_actual_pdf_pages(self):
        text, connection, ocr_mock = self._build(
            {
                "status": "ok",
                "page_count": 2,
                "pages": [
                    (1, "Nội dung PDF thật: khu vực B1 chưa nghiệm thu nội bộ trước khi mời TVGS."),
                    (2, "Yêu cầu nhà thầu hoàn tất checklist và khắc phục trước ngày 27/09/2026."),
                ],
                "blank_pages": 0,
                "blank_page_numbers": [],
                "truncated": False,
            }
        )
        self.assertIn("[PDF-FILE:file-b12]", text)
        self.assertIn("[PDF:file-b12:P1]", text)
        self.assertIn("khu vực B1 chưa nghiệm thu nội bộ", text)
        self.assertIn("[PDF:file-b12:P2]", text)
        self.assertIn("B12-MEP-002", text)
        self.assertNotIn("S2-MEP-001", text)
        ocr_mock.assert_not_called()
        scoped = [params for sql, params in connection.calls if "project_id=any" in sql]
        self.assertTrue(scoped)
        self.assertTrue(all(list(params[0]) == [101] for params in scoped if params))

    def test_scan_pdf_uses_vision_ocr_and_no_longer_stops_at_metadata(self):
        text, _, ocr_mock = self._build(
            {
                "status": "no_text",
                "page_count": 3,
                "pages": [],
                "blank_pages": 3,
                "blank_page_numbers": [1, 2, 3],
                "truncated": False,
            },
            {
                "status": "ok",
                "reason": "ok",
                "pages": [
                    (1, "BIÊN BẢN HIỆN TRƯỜNG - Nhà thầu phải hoàn thiện nghiệm thu nội bộ."),
                    (2, "Yêu cầu khắc phục trước ngày 27/09/2026 và báo cáo Ban điều hành."),
                    (3, "Đại diện các bên xác nhận nội dung nêu trên."),
                ],
                "missed_pages": [],
                "from_cache": False,
            },
        )
        self.assertIn("[PDF-OCR:file-b12:P1]", text)
        self.assertIn("hoàn thiện nghiệm thu nội bộ", text)
        self.assertIn("[PDF-OCR:file-b12:P2]", text)
        self.assertIn("[PDF-OCR-COMPLETE:file-b12]", text)
        self.assertNotIn("[PDF-SCAN-NO-TEXT:file-b12]", text)
        ocr_mock.assert_called_once()
        self.assertEqual(ocr_mock.call_args.kwargs["workspace_scope"], 101)
        self.assertEqual(list(ocr_mock.call_args.kwargs["page_numbers"]), [1, 2, 3])

    def test_partial_text_pdf_ocr_only_fills_blank_pages(self):
        text, _, ocr_mock = self._build(
            {
                "status": "ok",
                "page_count": 2,
                "pages": [(1, "Trang 1 có lớp text gốc.")],
                "blank_pages": 1,
                "blank_page_numbers": [2],
                "truncated": False,
            },
            {
                "status": "ok",
                "reason": "ok",
                "pages": [(2, "Trang 2 scan đã được OCR đúng nội dung.")],
                "missed_pages": [],
                "from_cache": True,
            },
        )
        self.assertIn("[PDF:file-b12:P1] Trang 1 có lớp text gốc.", text)
        self.assertIn("[PDF-OCR:file-b12:P2] Trang 2 scan đã được OCR đúng nội dung.", text)
        self.assertNotIn("PDF-PARTIAL-NO-TEXT", text)
        self.assertEqual(list(ocr_mock.call_args.kwargs["page_numbers"]), [2])

    def test_scan_pdf_still_refuses_metadata_when_ocr_unavailable(self):
        text, _, _ = self._build(
            {
                "status": "no_text",
                "page_count": 3,
                "pages": [],
                "blank_pages": 3,
                "blank_page_numbers": [1, 2, 3],
                "truncated": False,
            },
            {
                "status": "unavailable",
                "reason": "vision_provider_error",
                "pages": [],
                "missed_pages": [1, 2, 3],
                "from_cache": False,
            },
        )
        self.assertIn("[PDF-SCAN-NO-TEXT:file-b12]", text)
        self.assertIn("lý_do=vision_provider_error", text)
        self.assertIn("Không được suy nội dung từ tên file hoặc metadata", text)

    def test_real_scan_page_is_rasterized_when_pypdf_images_are_empty(self):
        import pymupdf
        from PIL import Image, ImageDraw

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf_path = root / "real_scan.pdf"
            canvas = Image.new("RGB", (1200, 1700), "white")
            ImageDraw.Draw(canvas).text(
                (80, 100),
                "BIEN BAN HOP - KE HOACH CHAY MAY DIEU HOA - 25/09/2026",
                fill="black",
            )
            png = io.BytesIO()
            canvas.save(png, format="PNG")

            document = pymupdf.open()
            page = document.new_page(width=595, height=842)
            page.insert_image(page.rect, stream=png.getvalue())
            document.save(str(pdf_path))
            document.close()

            extracted = ctx._extract_pdf_pages(pdf_path)
            self.assertEqual(extracted["status"], "no_text")
            self.assertEqual(extracted["blank_page_numbers"], [1])

            with (
                patch.dict(
                    os.environ,
                    {
                        "QLDA_LOCAL_STORAGE_ROOT": tmp,
                        "QLDA_AI_PDF_OCR_CACHE": "false",
                        "QLDA_AI_PDF_OCR_RENDER_DPI": "144",
                    },
                    clear=False,
                ),
                patch.object(ctx, "_page_images", return_value=[]) as embedded_mock,
                patch.object(
                    ctx.NativeProviderGateway,
                    "vision_text",
                    return_value="BIÊN BẢN HỌP - KẾ HOẠCH CHẠY MÁY ĐIỀU HÒA - 25/09/2026",
                ) as vision_mock,
            ):
                result = ctx._ocr_scanned_pdf(
                    pdf_path,
                    workspace_scope=101,
                    page_numbers=[1],
                    max_chars=5000,
                )

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["reason"], "ok")
            self.assertEqual(result["missed_pages"], [])
            self.assertEqual(result["rendered_pages"], [1])
            self.assertEqual(result["embedded_fallback_pages"], [])
            self.assertIn("KẾ HOẠCH CHẠY MÁY", result["pages"][0][1])
            embedded_mock.assert_not_called()
            vision_mock.assert_called_once()
            image_bytes = vision_mock.call_args.kwargs["data"]
            self.assertTrue(image_bytes.startswith(b"\x89PNG"))
            self.assertEqual(vision_mock.call_args.kwargs["mime_type"], "image/png")

    def test_render_failure_still_uses_embedded_image_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake_pdf = Path(tmp) / "fake.pdf"
            fake_pdf.write_bytes(b"%PDF-dummy")
            with (
                patch.object(ctx, "_render_pdf_page", return_value=None),
                patch.object(ctx, "_page_images", return_value=[b"x" * 4096]),
                patch.object(ctx, "_vision_ready_image", return_value=(b"PNG", "image/png")),
                patch.object(ctx.NativeProviderGateway, "vision_text", return_value="Nội dung OCR fallback"),
                patch("pypdf.PdfReader") as reader_cls,
                patch.dict(os.environ, {"QLDA_LOCAL_STORAGE_ROOT": tmp, "QLDA_AI_PDF_OCR_CACHE": "false"}, clear=False),
            ):
                reader = reader_cls.return_value
                reader.is_encrypted = False
                reader.pages = [object()]
                result = ctx._ocr_scanned_pdf(
                    fake_pdf,
                    workspace_scope=101,
                    page_numbers=[1],
                    max_chars=5000,
                )
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["embedded_fallback_pages"], [1])

    def test_storage_path_cannot_escape_authorized_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"QLDA_LOCAL_STORAGE_ROOT": tmp}, clear=False):
                self.assertIsNone(ctx._safe_path("../../etc/passwd"))

    def test_ocr_cache_round_trip_uses_sha_and_private_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"QLDA_LOCAL_STORAGE_ROOT": tmp, "QLDA_AI_PDF_OCR_CACHE": "true"}, clear=False):
                ctx._write_ocr_cache("abcdef1234567890", {1: "Trang một", 2: "Trang hai"})
                loaded = ctx._read_ocr_cache("abcdef1234567890")
                self.assertEqual(loaded, {1: "Trang một", 2: "Trang hai"})
                self.assertTrue(ctx._ocr_cache_path("abcdef1234567890").exists())


if __name__ == "__main__":
    unittest.main()
