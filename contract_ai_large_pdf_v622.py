from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import contract_management_v622 as cm


PATCH_MARKER = "V6.22 CONTRACT AI LARGE PDF SPLIT"
PDF_CHUNK_TARGET_BYTES = 18 * 1024 * 1024

_ORIGINAL_COLLECT_AI_FILES = cm._collect_ai_files
_INSTALLED = False


def _pdf_chunk_bytes(reader, start: int, end: int) -> bytes:
    from pypdf import PdfWriter

    writer = PdfWriter()
    for index in range(start, end):
        writer.add_page(reader.pages[index])
    buffer = io.BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def split_large_pdf(name: str, data: bytes, *, max_bytes: int = PDF_CHUNK_TARGET_BYTES) -> list[tuple[str, bytes]]:
    """Split one oversized PDF into AI-safe page ranges without rasterizing it.

    Recursive size validation is used instead of assuming all pages have equal
    byte size. This also works for scanned/image PDFs because page images remain
    inside each generated PDF chunk for the AI provider to inspect.
    """
    from pypdf import PdfReader

    raw = bytes(data or b"")
    if not raw:
        return []
    max_bytes = max(1 * 1024 * 1024, int(max_bytes or PDF_CHUNK_TARGET_BYTES))
    if len(raw) <= max_bytes:
        return [(name, raw)]

    reader = PdfReader(io.BytesIO(raw), strict=False)
    page_count = len(reader.pages)
    if page_count <= 0:
        return []

    ranges: list[tuple[int, int, bytes]] = []

    def divide(start: int, end: int) -> None:
        payload = _pdf_chunk_bytes(reader, start, end)
        if len(payload) <= max_bytes:
            ranges.append((start, end, payload))
            return
        if end - start <= 1:
            # A single page that is still too large cannot be made provider-safe
            # without destructive image recompression/OCR. Leave it unsent.
            return
        middle = start + (end - start) // 2
        divide(start, middle)
        divide(middle, end)

    divide(0, page_count)
    ranges.sort(key=lambda item: item[0])
    if not ranges:
        return []

    stem = Path(name).stem or "contract"
    total = len(ranges)
    chunks: list[tuple[str, bytes]] = []
    for number, (start, end, payload) in enumerate(ranges, 1):
        chunk_name = f"{stem}__part_{number:02d}_pages_{start + 1}-{end}_of_{total:02d}.pdf"
        chunks.append((chunk_name, payload))
    return chunks


def _collect_ai_files_large_pdf(gateway, session_token: str, records: list[dict[str, Any]]) -> tuple[list[tuple[str, bytes]], list[str]]:
    files: list[tuple[str, bytes]] = []
    skipped: list[str] = []
    total_bytes = 0

    for row in records:
        file_id = cm._text(row.get("current_file_id"))
        name = cm._text(row.get("current_file_name")) or f"record-{row.get('id')}.bin"
        if not file_id:
            continue
        if len(files) >= cm.AI_MAX_FILES:
            skipped.append(f"{name} (đã đạt giới hạn {cm.AI_MAX_FILES} file AI)")
            continue

        try:
            downloaded_name, _mime, content = gateway.download_bytes(session_token, file_id)
            content = bytes(content)
            source_name = cm._text(downloaded_name) or name
        except Exception as exc:
            skipped.append(f"{name} (không tải được từ VPS: {exc})")
            continue

        if not content:
            skipped.append(f"{source_name} (file rỗng)")
            continue

        # Normal-size files keep the proven upload path unchanged.
        if len(content) <= cm.AI_MAX_FILE_BYTES:
            if total_bytes + len(content) > cm.AI_MAX_TOTAL_BYTES:
                skipped.append(f"{source_name} (vượt giới hạn tổng dữ liệu AI)")
                continue
            files.append((source_name, content))
            total_bytes += len(content)
            continue

        # Oversized PDFs are split by page and each part is uploaded to the AI.
        # This preserves text, vector content and scanned page images.
        if Path(source_name).suffix.lower() != ".pdf":
            skipped.append(
                f"{source_name} (vượt {cm.AI_MAX_FILE_BYTES // 1024 // 1024} MB và không phải PDF để chia nhỏ)"
            )
            continue

        try:
            chunks = split_large_pdf(source_name, content)
        except Exception as exc:
            skipped.append(f"{source_name} (không chia được PDF: {exc})")
            continue

        if not chunks:
            skipped.append(f"{source_name} (PDF quá lớn hoặc có trang đơn vượt giới hạn AI)")
            continue

        added = 0
        for chunk_name, chunk_data in chunks:
            if len(files) >= cm.AI_MAX_FILES:
                break
            if total_bytes + len(chunk_data) > cm.AI_MAX_TOTAL_BYTES:
                break
            files.append((chunk_name, chunk_data))
            total_bytes += len(chunk_data)
            added += 1

        if added < len(chunks):
            skipped.append(
                f"{source_name} (đã gửi {added}/{len(chunks)} phần; phần còn lại vượt giới hạn tổng/file AI)"
            )

    return files, skipped


def install_contract_ai_large_pdf_v622() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    cm._collect_ai_files = _collect_ai_files_large_pdf
    _INSTALLED = True


__all__ = [
    "PATCH_MARKER",
    "PDF_CHUNK_TARGET_BYTES",
    "split_large_pdf",
    "install_contract_ai_large_pdf_v622",
]
