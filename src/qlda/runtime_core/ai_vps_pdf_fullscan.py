from __future__ import annotations

"""Full-project PDF retrieval for the shared QLDA assistant.

The VPS stores document attachments in ``qlda_local_files``. This module scans
all current PDFs belonging to the active project/document sheets, extracts every
text page with pypdf, caches extraction by file identity, and appends the most
relevant full-document/page evidence to ``ProjectContextBuilder.build``.

The complete PDF corpus is scanned/indexed for every AI question; only the
question-relevant evidence is injected into the model prompt so large projects
do not exceed model context limits.
"""

import os
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any


PATCH_MARKER = "V7.6 VPS PDF FULLSCAN V1"
MAX_APPEND_CHARS = max(40_000, min(300_000, int(os.environ.get("QLDA_AI_PDF_CONTEXT_CHARS", "180000"))))
MAX_DIRECT_FILE_CHARS = max(20_000, min(180_000, int(os.environ.get("QLDA_AI_PDF_DIRECT_FILE_CHARS", "120000"))))
PAGE_CHUNK_CHARS = max(1500, min(8000, int(os.environ.get("QLDA_AI_PDF_CHUNK_CHARS", "4200"))))
PAGE_CHUNK_OVERLAP = max(100, min(1200, int(os.environ.get("QLDA_AI_PDF_CHUNK_OVERLAP", "500"))))


def _row_value(row: Any, key: str, default: Any = "") -> Any:
    if row is None:
        return default
    try:
        value = row[key]
    except Exception:
        try:
            value = dict(row).get(key, default)
        except Exception:
            return default
    return default if value is None else value


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("đ", "d")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _tokens(value: Any) -> list[str]:
    raw = _norm(value).split()
    out: list[str] = []
    stop = {
        "doc", "file", "pdf", "hay", "cho", "toi", "minh", "duoc", "cac", "cua", "va", "voi", "trong",
        "noi", "dung", "chinh", "doc", "tong", "hop", "kiem", "tra", "phan", "tich", "giup", "ve", "nay",
    }
    for token in raw:
        if token.isdigit():
            token = str(int(token)) if token.strip("0") else "0"
        if len(token) >= 2 or token.isdigit():
            if token not in stop and token not in out:
                out.append(token)
    return out[:32]


def _compact_digits(value: Any) -> str:
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def _project_code(builder, project_id: int) -> str:
    try:
        with builder.connect() as connection:
            row = connection.execute("SELECT code FROM projects WHERE id=?", (int(project_id),)).fetchone()
            code = str(_row_value(row, "code", "") or "").strip()
            if code:
                return code
    except Exception:
        pass
    return str(project_id)


def _pdf_rows(project_code: str) -> list[dict[str, Any]]:
    try:
        from qlda.runtime_core import local_vps_backend as local

        safe_code = local._safe_segment(project_code, "DU_AN")
        local.ensure_schema()
        with local._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT id,project_code,kind,subtype,record_code,name,mime_type,size,sha256,
                              storage_path,uploaded_by,created_at,modified_at
                       FROM qlda_local_files
                       WHERE project_code=%s AND kind='document' AND trashed=FALSE AND history=FALSE
                         AND (LOWER(name) LIKE '%%.pdf' OR LOWER(mime_type)='application/pdf')
                       ORDER BY modified_at DESC, created_at DESC""",
                    (safe_code,),
                )
                return [dict(row) for row in cur.fetchall()]
    except Exception:
        return []


def _local_pdf_path(file_id: str) -> Path | None:
    try:
        from qlda.runtime_core import local_vps_backend as local

        _, path = local.local_file_path(str(file_id))
        return Path(path)
    except Exception:
        return None


@lru_cache(maxsize=96)
def _extract_pdf_cached(path_text: str, size: int, mtime_ns: int, sha256: str) -> tuple[tuple[int, str], ...]:
    del size, mtime_ns, sha256  # values are cache-version keys
    from pypdf import PdfReader

    path = Path(path_text)
    reader = PdfReader(str(path), strict=False)
    if getattr(reader, "is_encrypted", False):
        try:
            reader.decrypt("")
        except Exception:
            pass

    pages: list[tuple[int, str]] = []
    for number, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text(extraction_mode="layout") or ""
        except TypeError:
            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""
        except Exception:
            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""
        text = text.replace("\x00", " ")
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        pages.append((number, text))
    return tuple(pages)


def _extract_pages(row: dict[str, Any]) -> list[tuple[int, str]]:
    path = _local_pdf_path(str(row.get("id") or ""))
    if path is None or not path.exists():
        return []
    try:
        stat = path.stat()
        return list(_extract_pdf_cached(
            str(path),
            int(stat.st_size),
            int(stat.st_mtime_ns),
            str(row.get("sha256") or ""),
        ))
    except Exception:
        return []


def _chunks(text: str) -> list[str]:
    text = str(text or "").strip()
    if not text:
        return []
    if len(text) <= PAGE_CHUNK_CHARS:
        return [text]
    out: list[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + PAGE_CHUNK_CHARS)
        piece = text[start:end]
        if end < len(text):
            split_at = max(piece.rfind("\n"), piece.rfind(". "))
            if split_at > PAGE_CHUNK_CHARS // 2:
                end = start + split_at + 1
                piece = text[start:end]
        out.append(piece.strip())
        if end >= len(text):
            break
        start = max(start + 1, end - PAGE_CHUNK_OVERLAP)
    return [x for x in out if x]


def _metadata_text(row: dict[str, Any]) -> str:
    return " ".join([
        str(row.get("subtype") or ""),
        str(row.get("record_code") or ""),
        str(row.get("name") or ""),
        str(row.get("uploaded_by") or ""),
        str(row.get("created_at") or ""),
        str(row.get("modified_at") or ""),
    ])


def _score_text(text: str, tokens: list[str]) -> int:
    norm = _norm(text)
    if not norm:
        return 0
    words = set(norm.split())
    score = 0
    for token in tokens:
        if token in words:
            score += 4
        elif len(token) >= 4 and token in norm:
            score += 2
    return score


def _metadata_score(row: dict[str, Any], question: str, tokens: list[str]) -> int:
    meta = _metadata_text(row)
    score = _score_text(meta, tokens) * 3
    qnorm = _norm(question)
    subtype = _norm(row.get("subtype") or "")
    if subtype and subtype in qnorm:
        score += 18
    if ("bb hop" in qnorm or "bien ban hop" in qnorm) and str(row.get("subtype") or "").upper() == "BBHOP":
        score += 24

    qdigits = _compact_digits(question)
    mdigits = _compact_digits(meta)
    # A date such as 7/09/2026 should match 07.09.2026 or 07092026 in a file name/code.
    if len(qdigits) >= 6:
        q_nozero = qdigits.lstrip("0")
        if qdigits in mdigits or (q_nozero and q_nozero in mdigits.lstrip("0")):
            score += 30
    return score


def _pdf_appendix(builder, project_id: int, question: str) -> str:
    project_code = _project_code(builder, int(project_id))
    rows = _pdf_rows(project_code)
    if not rows:
        return ""

    tokens = _tokens(question)
    indexed: list[dict[str, Any]] = []
    total_pages = 0
    text_pages = 0
    for row in rows:
        pages = _extract_pages(row)
        total_pages += len(pages)
        text_pages += sum(1 for _, text in pages if text.strip())
        file_score = _metadata_score(row, question, tokens)
        indexed.append({"row": row, "pages": pages, "file_score": file_score})

    lines = [
        "",
        "## KHO PDF HỒ SƠ TRÊN VPS – FULLSCAN",
        f"AI đã quét toàn bộ {len(rows):,} PDF hiện hành của các sheet Quản lý hồ sơ thuộc dự án {project_code}; tổng {total_pages:,} trang, {text_pages:,} trang có lớp văn bản trích xuất được.",
        "Không được kết luận rằng AI chỉ nhìn metadata. Mọi PDF có lớp văn bản đã được đọc toàn bộ để lập chỉ mục; phần dưới là bằng chứng phù hợp nhất với câu hỏi hiện tại.",
        "Khi trích dẫn nội dung PDF, nêu tên file và số trang [PDF:<file>|p.<trang>]. Không suy diễn nội dung từ PDF không có lớp văn bản.",
    ]

    # First prefer files whose name/code/subtype/date match the user's question.
    ranked_files = sorted(indexed, key=lambda item: item["file_score"], reverse=True)
    used_chars = sum(len(x) + 1 for x in lines)
    included_keys: set[tuple[str, int]] = set()

    direct = [item for item in ranked_files if item["file_score"] >= 12 and any(t.strip() for _, t in item["pages"])]
    for item in direct[:2]:
        row = item["row"]
        name = str(row.get("name") or "PDF")
        header = f"\n### PDF KHỚP TRỰC TIẾP: {name} | sheet={row.get('subtype','')} | hồ sơ={row.get('record_code','')}"
        if used_chars + len(header) >= MAX_APPEND_CHARS:
            break
        lines.append(header)
        used_chars += len(header)
        file_chars = 0
        for page_no, text in item["pages"]:
            if not text.strip():
                continue
            block = f"\n[PDF:{name}|p.{page_no}]\n{text.strip()}"
            remaining_file = MAX_DIRECT_FILE_CHARS - file_chars
            remaining_total = MAX_APPEND_CHARS - used_chars
            if remaining_file <= 0 or remaining_total <= 0:
                break
            if len(block) > remaining_file:
                block = block[:remaining_file]
            if len(block) > remaining_total:
                block = block[:remaining_total]
            lines.append(block)
            file_chars += len(block)
            used_chars += len(block)
            included_keys.add((str(row.get("id") or ""), int(page_no)))

    # Search every page/chunk from every PDF, not just the direct file shortlist.
    candidates: list[tuple[int, dict[str, Any], int, str]] = []
    for item in indexed:
        row = item["row"]
        base = int(item["file_score"])
        for page_no, page_text in item["pages"]:
            for chunk in _chunks(page_text):
                score = base + _score_text(chunk, tokens)
                if tokens and score <= 0:
                    continue
                candidates.append((score, row, page_no, chunk))
    candidates.sort(key=lambda x: x[0], reverse=True)

    if candidates and used_chars < MAX_APPEND_CHARS:
        lines.append("\n### TRÍCH ĐOẠN PDF LIÊN QUAN TRÊN TOÀN BỘ KHO")
        used_chars += len(lines[-1])
        added = 0
        for score, row, page_no, chunk in candidates:
            key = (str(row.get("id") or ""), int(page_no))
            if key in included_keys:
                continue
            if tokens and score <= 0:
                continue
            name = str(row.get("name") or "PDF")
            block = f"\n[PDF:{name}|p.{page_no}|score={score}] {chunk}"
            if used_chars + len(block) > MAX_APPEND_CHARS:
                remaining = MAX_APPEND_CHARS - used_chars
                if remaining > 500:
                    lines.append(block[:remaining])
                break
            lines.append(block)
            used_chars += len(block)
            included_keys.add(key)
            added += 1
            if added >= 24:
                break

    no_text = [str(item["row"].get("name") or "") for item in indexed if not any(t.strip() for _, t in item["pages"])]
    if no_text:
        lines.append(
            "\nPDF chưa có lớp văn bản để trích xuất bằng pypdf: " + "; ".join(no_text[:20]) +
            (f"; ... và {len(no_text)-20} file khác" if len(no_text) > 20 else "")
        )

    return "\n".join(lines)


def install_ai_vps_pdf_fullscan() -> None:
    """Append full VPS document-PDF retrieval to the central AI context."""
    import qlda.runtime_core.ai_service as ai_service

    cls = ai_service.ProjectContextBuilder
    if getattr(cls, "_qlda_ai_vps_pdf_fullscan_installed", False):
        return

    original_build = cls.build

    def _build_with_vps_pdfs(self, project_id: int, question: str = "", status_date=None,
                             max_tasks: int = 80, max_docs: int = 70,
                             max_drawings: int = 60, max_legal: int = 40) -> str:
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
            appendix = _pdf_appendix(self, int(project_id), str(question or ""))
        except Exception as exc:
            appendix = f"\n## KHO PDF HỒ SƠ TRÊN VPS\nChưa lập được chỉ mục PDF ở lượt này: {exc}"
        return snapshot + ("\n" + appendix if appendix else "")

    cls.build = _build_with_vps_pdfs
    cls._qlda_ai_vps_pdf_fullscan_installed = True
    cls._qlda_ai_vps_pdf_fullscan_marker = PATCH_MARKER
