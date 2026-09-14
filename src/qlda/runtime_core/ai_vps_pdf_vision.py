from __future__ import annotations

"""Vision fallback for scanned PDFs stored by Quản lý hồ sơ on the VPS.

Searchable PDFs are handled by ``ai_vps_pdf_fullscan``. This module handles the
important remaining case: image/scanned PDFs with little or no text layer. It
selects the PDFs relevant to the user's question, sends the actual PDF pages to
the already configured OpenAI/Gemini provider, caches the extracted page content
on the VPS, and appends that evidence to the shared QLDA assistant context.

The scope follows the existing contractor AI access ContextVar, so a contractor
account never expands into another contractor workspace.
"""

import os
import re
from pathlib import Path
from typing import Any

from qlda.runtime_core import ai_vps_pdf_fullscan as fullscan


PATCH_MARKER = "V7.6 VPS PDF VISION V3"
VISION_MAX_FILES = max(1, min(12, int(os.environ.get("QLDA_AI_PDF_VISION_MAX_FILES", "6"))))
VISION_FILE_CONTEXT_CHARS = max(
    20_000,
    min(180_000, int(os.environ.get("QLDA_AI_PDF_VISION_FILE_CHARS", "100000"))),
)
VISION_TOTAL_CONTEXT_CHARS = max(
    40_000,
    min(320_000, int(os.environ.get("QLDA_AI_PDF_VISION_TOTAL_CHARS", "180000"))),
)


def _row_value(row: Any, key: str, default: Any = "") -> Any:
    try:
        value = row[key]
    except Exception:
        try:
            value = dict(row).get(key, default)
        except Exception:
            return default
    return default if value is None else value


def _scope_project_codes(builder, project_id: int) -> list[str]:
    """Return only project codes that the current AI session is allowed to read."""
    ids: list[int] = []
    pid = int(project_id)

    try:
        from qlda.runtime_core.contractor_access_control import _AI_WORKSPACE_SCOPE

        restricted = _AI_WORKSPACE_SCOPE.get()
    except Exception:
        restricted = None

    if restricted:
        ids = [int(restricted)]
    else:
        ids = [pid]
        try:
            from qlda.runtime_core.contractor_workspace import (
                contractor_rows_connection,
                resolve_master_project_id_connection,
            )

            with builder.connect() as connection:
                master_id = int(resolve_master_project_id_connection(connection, pid) or pid)
                if master_id not in ids:
                    ids.append(master_id)
                for row in contractor_rows_connection(connection, master_id, active_only=True):
                    wid = int(_row_value(row, "workspace_project_id", 0) or 0)
                    if wid > 0 and wid not in ids:
                        ids.append(wid)
        except Exception:
            pass

    codes: list[str] = []
    try:
        with builder.connect() as connection:
            for item_id in ids:
                try:
                    row = connection.execute(
                        "SELECT code FROM projects WHERE id=? LIMIT 1", (int(item_id),)
                    ).fetchone()
                    code = str(_row_value(row, "code", "") or "").strip()
                except Exception:
                    code = ""
                if code and code not in codes:
                    codes.append(code)
    except Exception:
        pass

    if not codes:
        fallback = fullscan._project_code(builder, pid)
        if fallback:
            codes.append(str(fallback))
    return codes


def _all_pdf_rows(builder, project_id: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for code in _scope_project_codes(builder, int(project_id)):
        for row in fullscan._pdf_rows(code):
            file_id = str(row.get("id") or "")
            if not file_id or file_id in seen:
                continue
            seen.add(file_id)
            rows.append(dict(row))
    return rows


def _needs_vision(row: dict[str, Any]) -> tuple[bool, int, int, int]:
    """Detect PDFs whose text layer is absent or too sparse to trust."""
    pages = fullscan._extract_pages(row)
    page_count = len(pages)
    if page_count <= 0:
        return True, 0, 0, 0

    chars = [len(str(text or "").strip()) for _page, text in pages]
    total_chars = sum(chars)
    text_pages = sum(1 for n in chars if n >= 40)

    # Scans commonly expose no text at all, or only page numbers/stamps. Avoid
    # treating that tiny layer as the document body.
    sparse = total_chars < max(500, page_count * 120)
    low_coverage = text_pages < max(1, int(page_count * 0.60))
    return bool(sparse or low_coverage), page_count, text_pages, total_chars


def _cache_path(row: dict[str, Any]) -> Path | None:
    try:
        from qlda.runtime_core import local_vps_backend as local

        root = local.storage_root() / ".ai_cache" / "pdf_vision_v3"
        root.mkdir(parents=True, exist_ok=True)
        identity = str(row.get("sha256") or row.get("id") or "pdf")
        identity = re.sub(r"[^A-Za-z0-9_.-]+", "_", identity)[:160]
        return root / f"{identity}.txt"
    except Exception:
        return None


def _read_cached(row: dict[str, Any]) -> str:
    path = _cache_path(row)
    if path is None or not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _write_cached(row: dict[str, Any], text: str) -> None:
    path = _cache_path(row)
    if path is None or not text.strip():
        return
    try:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(text.strip(), encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        pass


def _vision_prompt(name: str, subtype: str, record_code: str, part_name: str) -> str:
    return f"""Bạn đang làm nhiệm vụ ĐỌC TRỰC TIẾP PDF SCAN/HÌNH ẢNH cho hệ thống QLDA xây dựng.
Nguồn hồ sơ: sheet={subtype or 'không rõ'} | mã={record_code or 'không rõ'}
File gốc: {name}
Phần đang đọc: {part_name}

YÊU CẦU BẮT BUỘC:
1. Đọc TẤT CẢ các trang nhìn thấy trong PDF/phần PDF này bằng khả năng thị giác của model, kể cả khi PDF không có text layer.
2. Trích lại nội dung đủ chi tiết theo từng trang. Mỗi trang bắt đầu bằng [TRANG n].
3. Giữ nguyên tên người/đơn vị, ngày tháng, số hiệu, số liệu, tỷ lệ, mốc tiến độ, trách nhiệm, kết luận và các action item nếu nhìn thấy.
4. Bảng phải chuyển thành Markdown/text có đủ hàng/cột quan trọng; chữ ký/con dấu chỉ mô tả khi cần nhận diện bên ký, không suy đoán chữ không đọc được.
5. Không được trả lời rằng cần OCR hoặc cần file searchable. Chính lượt này là bước đọc bằng vision.
6. Không tóm tắt làm mất dữ kiện. Nếu một vùng thực sự không đọc được, ghi [KHÔNG ĐỌC RÕ] đúng tại vị trí đó.
7. Chỉ xuất nội dung đọc được từ tài liệu, không thêm kiến thức ngoài file.
"""


def _extract_with_provider(row: dict[str, Any]) -> str:
    cached = _read_cached(row)
    if cached:
        return cached

    path = fullscan._local_pdf_path(str(row.get("id") or ""))
    if path is None or not path.exists():
        return ""
    try:
        data = path.read_bytes()
    except Exception:
        return ""
    if not data:
        return ""

    try:
        from qlda.runtime_core.settings_store import get_ai_runtime_settings
        from qlda.runtime_core.document_pdf_vision_provider import scan_document_pdf
        from qlda.runtime_core.contract_ai_large_pdf import split_large_pdf

        settings = dict(get_ai_runtime_settings() or {})
        provider = str(settings.get("provider") or "openai").strip().lower()
        if provider not in {"openai", "gemini"}:
            provider = "openai"
        if not str(settings.get("api_key") or "").strip():
            return ""

        name = str(row.get("name") or path.name or "document.pdf")
        try:
            parts = split_large_pdf(name, data)
        except Exception:
            parts = [(name, data)]
        if not parts:
            parts = [(name, data)]

        outputs: list[str] = []
        subtype = str(row.get("subtype") or "")
        record_code = str(row.get("record_code") or "")
        for index, (part_name, part_bytes) in enumerate(parts, start=1):
            prompt = _vision_prompt(name, subtype, record_code, part_name)
            try:
                text = scan_document_pdf(provider, settings, part_name, bytes(part_bytes), prompt)
            except Exception as exc:
                text = f"[PHẦN {index} - LỖI ĐỌC VISION: {exc}]"
            if text and text.strip():
                outputs.append(f"\n===== PHẦN {index}/{len(parts)}: {part_name} =====\n{text.strip()}")

        result = "\n".join(outputs).strip()
        # Do not cache a pure failure response; a transient provider error should
        # be retried on the next user question.
        if result and "LỖI ĐỌC VISION" not in result:
            _write_cached(row, result)
        return result
    except Exception:
        return ""


def _question_looks_document_related(question: str) -> bool:
    q = fullscan._norm(question)
    return any(
        term in q
        for term in (
            "pdf", "file", "ho so", "bien ban", "bb hop", "bbhop", "ncr", "rfi", "rfa",
            "nghiem thu", "ntcv", "ntvl", "kdvt", "kiem dinh", "tai lieu", "doc", "tong hop",
        )
    )


def _vision_appendix(builder, project_id: int, question: str) -> str:
    rows = _all_pdf_rows(builder, int(project_id))
    if not rows:
        return ""

    tokens = fullscan._tokens(question)
    candidates: list[tuple[int, dict[str, Any], tuple[int, int, int]]] = []
    for row in rows:
        needs, page_count, text_pages, total_chars = _needs_vision(row)
        if not needs:
            continue
        score = int(fullscan._metadata_score(row, question, tokens))
        # Content-independent fallback: if the user explicitly asks to read a
        # document and the scoped project has only a small number of scanned PDFs,
        # allow recent candidates even when metadata wording is imperfect.
        if score <= 0 and _question_looks_document_related(question) and len(rows) <= 3:
            score = 1
        if score > 0:
            candidates.append((score, row, (page_count, text_pages, total_chars)))

    if not candidates:
        return ""

    candidates.sort(key=lambda item: item[0], reverse=True)
    lines = [
        "",
        "## PDF SCAN/HÌNH ẢNH – ĐÃ ĐỌC BẰNG AI VISION",
        "QUY TẮC ƯU TIÊN: nội dung VISION bên dưới là nội dung thực tế AI đã đọc từ trang PDF. "
        "Nếu phần FULLSCAN phía trên từng ghi 'không có lớp văn bản', đó chỉ là trạng thái text layer của PDF, "
        "KHÔNG có nghĩa file chưa được đọc. Tuyệt đối không yêu cầu người dùng OCR/searchable PDF khi đã có bằng chứng VISION ở đây.",
    ]
    used = sum(len(x) + 1 for x in lines)
    processed = 0

    for score, row, stats in candidates[:VISION_MAX_FILES]:
        text = _extract_with_provider(row)
        if not text.strip():
            continue
        name = str(row.get("name") or "PDF")
        page_count, text_pages, total_chars = stats
        header = (
            f"\n### [PDF-VISION:{name}] sheet={row.get('subtype','')} | hồ sơ={row.get('record_code','')} | "
            f"{page_count} trang | text-layer={text_pages}/{page_count} trang, {total_chars} ký tự | match={score}"
        )
        block = header + "\n" + text[:VISION_FILE_CONTEXT_CHARS]
        remain = VISION_TOTAL_CONTEXT_CHARS - used
        if remain <= 800:
            break
        if len(block) > remain:
            block = block[:remain]
        lines.append(block)
        used += len(block)
        processed += 1

    if processed <= 0:
        return ""
    return "\n".join(lines)


def install_ai_vps_pdf_vision() -> None:
    """Add multimodal scan-PDF reading to the shared project assistant."""
    import qlda.runtime_core.ai_service as ai_service

    cls = ai_service.ProjectContextBuilder
    if getattr(cls, "_qlda_ai_vps_pdf_vision_installed", False):
        return

    original_build = cls.build

    def _build_with_vision(
        self,
        project_id: int,
        question: str = "",
        status_date=None,
        max_tasks: int = 80,
        max_docs: int = 70,
        max_drawings: int = 60,
        max_legal: int = 40,
    ) -> str:
        snapshot = original_build(
            self,
            project_id,
            question,
            status_date,
            max_tasks=max_tasks,
            max_docs=max_docs,
            max_drawings=max_drawings,
            max_legal=max_legal,
        )
        try:
            appendix = _vision_appendix(self, int(project_id), str(question or ""))
        except Exception as exc:
            appendix = f"\n## PDF SCAN/HÌNH ẢNH\nVision scan chưa hoàn tất ở lượt này: {exc}"
        return snapshot + ("\n" + appendix if appendix else "")

    cls.build = _build_with_vision
    cls._qlda_ai_vps_pdf_vision_installed = True
    cls._qlda_ai_vps_pdf_vision_marker = PATCH_MARKER
