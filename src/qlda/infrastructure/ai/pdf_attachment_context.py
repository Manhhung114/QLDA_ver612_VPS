from __future__ import annotations

"""Tenant-scoped PDF attachment context for native project chat.

Current QLDA production stores uploads on the VPS in ``qlda_local_files``.  The
business rows (documents/drawings/etc.) contain metadata, while this module reads
the actual PDF bytes from the authorized workspace's local storage and exposes
page-level text to the LLM.  Image-only/scanned PDFs are marked explicitly so the
assistant cannot pretend metadata is the file's contents.
"""

import os
import re
import unicodedata
from pathlib import Path
from typing import Any, Sequence


_STOPWORDS = {
    "cho", "toi", "hay", "kiem", "tra", "du", "lieu", "cua", "va", "voi", "theo",
    "trong", "tren", "cac", "nhung", "bao", "nhieu", "tong", "hop", "duoc", "hien", "tai",
    "du", "an", "nay", "gan", "day", "nhat", "moi", "ve", "thong", "tin", "chi", "tiet",
    "ghi", "ro", "noi", "dung", "file", "pdf", "tep", "dinh", "kem",
}

_DOC_TYPE_PHRASES = {
    "BBHT": ("bien ban hien truong", "bbht"),
    "BBHOP": ("bien ban hop", "cuoc hop", "bbhop"),
    "NCR": ("ncr", "khong phu hop"),
    "RFI": ("rfi", "request for information"),
    "RFA": ("rfa", "request for approval"),
    "NTCV": ("nghiem thu cong viec", "ntcv"),
    "NTVL": ("nghiem thu vat lieu", "ntvl"),
    "KDVT": ("kiem dinh vat tu", "kdvt"),
    "NKCT": ("nhat ky cong truong", "nkct"),
}


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("đ", "d")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _terms(question: str) -> list[str]:
    out: list[str] = []
    for token in _norm(question).split():
        if len(token) >= 2 and token not in _STOPWORDS and token not in out:
            out.append(token)
    return out[:18]


def _rowdict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    try:
        return {str(k): row[k] for k in row.keys()}
    except Exception:
        try:
            return dict(row)
        except Exception:
            return {}


def _table_exists(connection, table: str) -> bool:
    try:
        row = connection.execute(
            """SELECT EXISTS(
                SELECT 1 FROM information_schema.tables
                WHERE table_schema=current_schema() AND table_name=%s
            ) AS ok""",
            (str(table),),
        ).fetchone()
        return bool(row and _rowdict(row).get("ok"))
    except Exception:
        return False


def _fetch(connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    try:
        return [_rowdict(row) for row in connection.execute(sql, params).fetchall()]
    except Exception:
        return []


def _latest_key(row: dict[str, Any]) -> tuple[str, int]:
    stamp = str(
        row.get("issue_date")
        or row.get("received_date")
        or row.get("modified_at")
        or row.get("updated_at")
        or row.get("created_at")
        or ""
    )
    try:
        rid = int(row.get("id") or 0)
    except Exception:
        rid = 0
    return stamp, rid


def _wanted_doc_types(question: str) -> set[str]:
    qnorm = _norm(question)
    return {
        dtype
        for dtype, phrases in _DOC_TYPE_PHRASES.items()
        if any(_norm(phrase) in qnorm for phrase in phrases)
    }


def _latest_intent(question: str) -> bool:
    qnorm = _norm(question)
    return any(phrase in qnorm for phrase in ("gan day nhat", "moi nhat", "gan nhat", "latest"))


def _pdf_intent(question: str) -> bool:
    qnorm = _norm(question)
    return any(
        phrase in qnorm
        for phrase in (
            "pdf", "file", "tep", "dinh kem", "noi dung", "chi tiet", "bien ban", "ban ve",
            "ncr", "rfi", "rfa", "nghiem thu", "kiem dinh", "nhat ky", "hop dong",
        )
    )


def _score(row: dict[str, Any], terms: Sequence[str], keys: Sequence[str]) -> int:
    haystack = _norm(" ".join(str(row.get(key) or "") for key in keys))
    return sum(1 for term in terms if term and term in haystack)


def _select_business_records(connection, workspace_ids: list[int], question: str) -> list[dict[str, Any]]:
    ids = [int(x) for x in workspace_ids if int(x or 0) > 0]
    if not ids:
        return []
    qnorm = _norm(question)
    terms = _terms(question)
    latest = _latest_intent(question)
    wanted_types = _wanted_doc_types(question)
    selected: list[dict[str, Any]] = []

    if _table_exists(connection, "documents"):
        rows = _fetch(
            connection,
            """SELECT id,project_id,doc_type,code,subject,description,issue_date,updated_at,created_at
               FROM documents WHERE project_id=ANY(%s) ORDER BY id DESC LIMIT 2500""",
            (ids,),
        )
        rows.sort(key=_latest_key, reverse=True)
        if wanted_types:
            rows = [row for row in rows if str(row.get("doc_type") or "").upper() in wanted_types]
        if rows:
            if latest and (wanted_types or "bien ban" in qnorm or "ho so" in qnorm):
                rows = rows[:1]
            else:
                scored = [
                    (_score(row, terms, ("doc_type", "code", "subject", "description")), row)
                    for row in rows
                ]
                matches = [row for score, row in scored if score > 0]
                rows = (matches or rows)[:4]
            for row in rows:
                item = dict(row)
                item["kind"] = "document"
                item["subtype"] = str(row.get("doc_type") or "")
                item["record_code"] = str(row.get("code") or "")
                item["record_title"] = str(row.get("subject") or "")
                selected.append(item)

    drawing_intent = any(phrase in qnorm for phrase in ("ban ve", "drawing", "shopdrawing", "shop drawing", "hoan cong", "as built"))
    if drawing_intent and _table_exists(connection, "drawings"):
        rows = _fetch(
            connection,
            """SELECT id,project_id,drawing_type,drawing_no,title,description,received_date,issue_date,updated_at,created_at
               FROM drawings WHERE project_id=ANY(%s) ORDER BY id DESC LIMIT 2500""",
            (ids,),
        )
        rows.sort(key=_latest_key, reverse=True)
        if latest:
            rows = rows[:1]
        else:
            scored = [
                (_score(row, terms, ("drawing_type", "drawing_no", "title", "description")), row)
                for row in rows
            ]
            rows = ([row for score, row in scored if score > 0] or rows)[:4]
        for row in rows:
            item = dict(row)
            item["kind"] = "drawing"
            item["subtype"] = str(row.get("drawing_type") or "")
            item["record_code"] = str(row.get("drawing_no") or "")
            item["record_title"] = str(row.get("title") or "")
            selected.append(item)

    return selected[:6]


def _storage_root() -> Path:
    return Path(os.environ.get("QLDA_LOCAL_STORAGE_ROOT", "/opt/qlda/data")).expanduser().resolve()


def _safe_path(storage_path: str) -> Path | None:
    try:
        root = _storage_root()
        path = (root / str(storage_path or "")).resolve()
        path.relative_to(root)
        return path
    except Exception:
        return None


def _extract_pdf_pages(path: Path, *, max_pages: int = 120, max_chars: int = 15000) -> dict[str, Any]:
    """Return page-labelled text. No OCR is attempted for image-only pages."""
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        if bool(getattr(reader, "is_encrypted", False)):
            try:
                unlocked = reader.decrypt("")
            except Exception:
                unlocked = 0
            if not unlocked:
                return {"status": "encrypted", "page_count": len(reader.pages), "pages": [], "blank_pages": 0}
        page_count = len(reader.pages)
        pages: list[tuple[int, str]] = []
        blank_pages = 0
        used = 0
        for page_no, page in enumerate(reader.pages[: max(1, int(max_pages))], start=1):
            try:
                text = page.extract_text() or ""
            except Exception:
                text = ""
            text = re.sub(r"[ \t]+", " ", text)
            text = re.sub(r"\n{3,}", "\n\n", text).strip()
            if not text:
                blank_pages += 1
                continue
            room = max_chars - used
            if room <= 0:
                break
            if len(text) > room:
                text = text[:room].rstrip() + "…"
            pages.append((page_no, text))
            used += len(text)
        return {
            "status": "ok" if pages else "no_text",
            "page_count": page_count,
            "pages": pages,
            "blank_pages": blank_pages,
            "truncated": page_count > max_pages or used >= max_chars,
        }
    except Exception as exc:
        return {
            "status": "error",
            "page_count": 0,
            "pages": [],
            "blank_pages": 0,
            "error": exc.__class__.__name__,
        }


def _clip_context(lines: list[str], max_chars: int) -> str:
    out: list[str] = []
    used = 0
    for line in lines:
        value = str(line or "").strip()
        if not value:
            continue
        room = max_chars - used
        if room <= 0:
            break
        if len(value) > room:
            value = value[:room].rstrip() + "…"
        out.append(value)
        used += len(value) + 1
    return "\n".join(out)


def build_pdf_attachment_context(
    connection,
    workspace_ids: Sequence[int],
    question: str,
    *,
    max_files: int = 4,
    max_chars: int = 18000,
) -> str:
    """Read actual local PDF contents for records inside the authorized workspace."""
    ids = [int(x) for x in workspace_ids if int(x or 0) > 0]
    if not ids or not _pdf_intent(question) or not _table_exists(connection, "qlda_local_files"):
        return ""

    projects = _fetch(connection, "SELECT id,code FROM projects WHERE id=ANY(%s)", (ids,))
    project_codes = {int(row.get("id") or 0): str(row.get("code") or "") for row in projects}
    allowed_codes = [code for code in project_codes.values() if code]
    if not allowed_codes:
        return ""

    records = _select_business_records(connection, ids, question)
    record_keys = {
        (
            str(project_codes.get(int(row.get("project_id") or 0), "")),
            str(row.get("kind") or "").lower(),
            str(row.get("subtype") or "").upper(),
            str(row.get("record_code") or ""),
        ): row
        for row in records
        if str(row.get("record_code") or "")
    }

    files = _fetch(
        connection,
        """SELECT id,project_code,kind,subtype,record_code,name,mime_type,size,sha256,storage_path,
                  upload_purpose,created_at,modified_at
           FROM qlda_local_files
           WHERE project_code=ANY(%s) AND trashed=FALSE AND history=FALSE
             AND (LOWER(name) LIKE '%%.pdf' OR LOWER(mime_type)='application/pdf')
           ORDER BY created_at DESC LIMIT 800""",
        (allowed_codes,),
    )
    if not files:
        return ""

    chosen: list[tuple[dict[str, Any], dict[str, Any] | None]] = []
    if record_keys:
        for file_row in files:
            key = (
                str(file_row.get("project_code") or ""),
                str(file_row.get("kind") or "").lower(),
                str(file_row.get("subtype") or "").upper(),
                str(file_row.get("record_code") or ""),
            )
            record = record_keys.get(key)
            if record is not None:
                chosen.append((file_row, record))
                if len(chosen) >= max_files:
                    break

    if not chosen:
        # Generic PDF/file questions can still inspect authorized attachments even
        # when there is no document/drawing row matching the wording.
        terms = _terms(question)
        scored = [
            (
                _score(file_row, terms, ("kind", "subtype", "record_code", "name", "upload_purpose")),
                file_row,
            )
            for file_row in files
        ]
        matched = [row for score, row in scored if score > 0]
        fallback = matched or files
        chosen = [(row, None) for row in fallback[:max_files]]

    lines = ["## NỘI DUNG PDF ĐÍNH KÈM LIVE"]
    for file_row, record in chosen:
        file_id = str(file_row.get("id") or "")
        name = str(file_row.get("name") or "attachment.pdf")
        record_code = str(file_row.get("record_code") or (record or {}).get("record_code") or "")
        title = str((record or {}).get("record_title") or "")
        path = _safe_path(str(file_row.get("storage_path") or ""))
        if path is None or not path.exists() or not path.is_file():
            lines.append(
                f"[PDF-MISSING:{file_id}] hồ sơ={record_code} | file={name} | file vật lý không khả dụng trên VPS"
            )
            continue

        extracted = _extract_pdf_pages(path, max_chars=max(2500, max_chars // max(1, len(chosen))))
        status = str(extracted.get("status") or "error")
        pages = list(extracted.get("pages") or [])
        page_count = int(extracted.get("page_count") or 0)
        lines.append(
            f"[PDF-FILE:{file_id}] hồ sơ={record_code} | tiêu đề={title} | file={name} | số trang={page_count}"
        )
        if status == "encrypted":
            lines.append(
                f"[PDF-ENCRYPTED:{file_id}] file={name} có mật khẩu/mã hóa; AI chưa đọc được nội dung."
            )
            continue
        if status == "no_text":
            lines.append(
                f"[PDF-SCAN-NO-TEXT:{file_id}] file={name} không có lớp text trích xuất được; "
                "có thể là PDF scan/ảnh. Không được suy nội dung từ tên file hoặc metadata; cần OCR/vision để đọc."
            )
            continue
        if status == "error":
            lines.append(
                f"[PDF-READ-ERROR:{file_id}] file={name} không trích xuất được text ({extracted.get('error','PDFError')})."
            )
            continue

        for page_no, text in pages:
            lines.append(
                f"[PDF:{file_id}:P{int(page_no)}] hồ sơ={record_code} | file={name} | trang={int(page_no)}\n{text}"
            )
        blank_pages = int(extracted.get("blank_pages") or 0)
        if blank_pages:
            lines.append(
                f"[PDF-PARTIAL-NO-TEXT:{file_id}] có {blank_pages} trang không trích xuất được lớp text."
            )
        if extracted.get("truncated"):
            lines.append(
                f"[PDF-TRUNCATED:{file_id}] nội dung đưa vào prompt đã giới hạn dung lượng; không đồng nghĩa file chỉ có phần trên."
            )

    return _clip_context(lines, max_chars)


__all__ = ["build_pdf_attachment_context"]
