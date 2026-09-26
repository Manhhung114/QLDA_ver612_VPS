from __future__ import annotations

"""Tenant-scoped PDF attachment context for native project chat.

QLDA stores uploads on the VPS in ``qlda_local_files``. Business rows contain
metadata, while this module reads the actual authorized PDF bytes and exposes
page-level content to the LLM. Text PDFs are parsed locally with pypdf. Image-only
or partially scanned PDFs are rasterized page-by-page with PyMuPDF and then sent
to the configured native AI Vision provider. Embedded-image extraction remains a
fallback only. Successful OCR is cached by PDF SHA.
"""

import hashlib
import io
import json
import os
import re
import unicodedata
from pathlib import Path
from typing import Any, Sequence

from qlda.infrastructure.ai.provider_gateway import NativeProviderGateway


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


def _env_bool(name: str, default: bool) -> bool:
    raw = str(os.environ.get(name, "") or "").strip().lower()
    if not raw:
        return bool(default)
    return raw not in {"0", "false", "no", "off"}


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(str(os.environ.get(name, default) or default).strip())
    except Exception:
        value = int(default)
    return max(int(minimum), min(int(maximum), value))


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
                rows = ([row for score, row in scored if score > 0] or rows)[:4]
            for row in rows:
                item = dict(row)
                item["kind"] = "document"
                item["subtype"] = str(row.get("doc_type") or "")
                item["record_code"] = str(row.get("code") or "")
                item["record_title"] = str(row.get("subject") or "")
                selected.append(item)

    drawing_intent = any(
        phrase in qnorm
        for phrase in ("ban ve", "drawing", "shopdrawing", "shop drawing", "hoan cong", "as built")
    )
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


def _clean_text(value: Any) -> str:
    text = str(value or "").replace("\x00", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _extract_pdf_pages(path: Path, *, max_pages: int = 120, max_chars: int = 15000) -> dict[str, Any]:
    """Return native page text and page numbers that require OCR."""
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        if bool(getattr(reader, "is_encrypted", False)):
            try:
                unlocked = reader.decrypt("")
            except Exception:
                unlocked = 0
            if not unlocked:
                return {
                    "status": "encrypted",
                    "page_count": len(reader.pages),
                    "pages": [],
                    "blank_pages": 0,
                    "blank_page_numbers": [],
                }
        page_count = len(reader.pages)
        pages: list[tuple[int, str]] = []
        blank_page_numbers: list[int] = []
        used = 0
        limit = min(page_count, max(1, int(max_pages)))
        for page_no, page in enumerate(reader.pages[:limit], start=1):
            try:
                text = _clean_text(page.extract_text() or "")
            except Exception:
                text = ""
            if not text:
                blank_page_numbers.append(page_no)
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
            "blank_pages": len(blank_page_numbers),
            "blank_page_numbers": blank_page_numbers,
            "truncated": page_count > max_pages or used >= max_chars,
        }
    except Exception as exc:
        return {
            "status": "error",
            "page_count": 0,
            "pages": [],
            "blank_pages": 0,
            "blank_page_numbers": [],
            "error": exc.__class__.__name__,
        }


def _pdf_sha(path: Path, hinted_sha: str = "") -> str:
    hinted = re.sub(r"[^a-fA-F0-9]", "", str(hinted_sha or ""))
    if len(hinted) >= 32:
        return hinted.lower()
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _ocr_cache_path(pdf_sha: str) -> Path:
    digest = hashlib.sha256(("qlda-pdf-ocr-v2:" + str(pdf_sha)).encode("utf-8")).hexdigest()
    return _storage_root() / ".qlda-ai" / "pdf_ocr" / f"{digest}.json"


def _read_ocr_cache(pdf_sha: str) -> dict[int, str]:
    if not _env_bool("QLDA_AI_PDF_OCR_CACHE", True):
        return {}
    path = _ocr_cache_path(pdf_sha)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if int(data.get("version") or 0) != 2 or str(data.get("sha256") or "") != str(pdf_sha):
            return {}
        pages = data.get("pages") or {}
        return {
            int(page_no): _clean_text(text)
            for page_no, text in dict(pages).items()
            if str(page_no).isdigit() and _clean_text(text)
        }
    except Exception:
        return {}


def _write_ocr_cache(pdf_sha: str, pages: dict[int, str]) -> None:
    if not pages or not _env_bool("QLDA_AI_PDF_OCR_CACHE", True):
        return
    path = _ocr_cache_path(pdf_sha)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(path.parent, 0o700)
        except Exception:
            pass
        payload = {
            "version": 2,
            "sha256": str(pdf_sha),
            "pages": {str(int(k)): str(v) for k, v in sorted(pages.items()) if str(v).strip()},
        }
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except Exception:
            pass
        tmp.replace(path)
    except Exception:
        return


def _vision_ready_image(data: bytes) -> tuple[bytes, str]:
    """Normalize arbitrary page/image bytes to a provider-safe PNG."""
    try:
        from PIL import Image

        with Image.open(io.BytesIO(bytes(data))) as image:
            image.load()
            if image.mode not in {"RGB", "L"}:
                image = image.convert("RGB")
            max_dim = _env_int("QLDA_AI_PDF_OCR_MAX_IMAGE_DIM", 3200, 1200, 5000)
            if max(image.size or (0, 0)) > max_dim:
                image.thumbnail((max_dim, max_dim))
            out = io.BytesIO()
            image.save(out, format="PNG", optimize=True)
            return out.getvalue(), "image/png"
    except Exception:
        return bytes(data), "image/jpeg"


def _render_pdf_page(path: Path, page_no: int) -> tuple[bytes, str] | None:
    """Rasterize the complete PDF page, independent of pypdf ``page.images``.

    A scanned page can be encoded as a page content stream, tiled images, masks or
    other PDF objects that pypdf does not expose as ``page.images``. Rendering the
    page is therefore the primary OCR input and is the key production safeguard.
    """
    if int(page_no or 0) <= 0:
        return None
    document = None
    try:
        import pymupdf

        document = pymupdf.open(str(path))
        index = int(page_no) - 1
        if index < 0 or index >= int(document.page_count):
            return None
        dpi = _env_int("QLDA_AI_PDF_OCR_RENDER_DPI", 180, 96, 300)
        page = document.load_page(index)
        pixmap = page.get_pixmap(dpi=dpi, alpha=False)
        raw = bytes(pixmap.tobytes("png") or b"")
        if len(raw) < 512:
            return None
        return _vision_ready_image(raw)
    except Exception:
        return None
    finally:
        if document is not None:
            try:
                document.close()
            except Exception:
                pass


def _page_images(page: Any) -> list[bytes]:
    """Legacy/fallback extraction when full-page rasterization is unavailable."""
    found: list[bytes] = []
    try:
        for image in list(page.images):
            raw = bytes(getattr(image, "data", b"") or b"")
            if len(raw) >= 2048:
                found.append(raw)
    except Exception:
        return []
    found.sort(key=len, reverse=True)
    limit = _env_int("QLDA_AI_PDF_OCR_MAX_IMAGES_PER_PAGE", 3, 1, 6)
    return found[:limit]


def _looks_like_no_text(value: str) -> bool:
    norm = _norm(value)
    return not norm or norm in {"no text", "khong co chu", "khong co van ban", "blank", "empty"}


def _ocr_scanned_pdf(
    path: Path,
    *,
    workspace_scope: int,
    page_numbers: Sequence[int],
    file_sha: str = "",
    max_chars: int = 15000,
) -> dict[str, Any]:
    """OCR scan pages through full-page rasterization + native AI Vision.

    The complete authorized local PDF page is rendered first. Embedded-image
    extraction is used only when page rendering fails. Successful text is cached
    by immutable PDF SHA so subsequent chats do not repeat provider calls.
    """
    requested = sorted({int(x) for x in page_numbers if int(x or 0) > 0})
    if not requested:
        return {"status": "not_needed", "pages": [], "missed_pages": [], "from_cache": True}
    if not _env_bool("QLDA_AI_PDF_OCR_ENABLED", True):
        return {
            "status": "disabled",
            "reason": "ocr_disabled",
            "pages": [],
            "missed_pages": requested,
            "from_cache": False,
        }

    max_pages = _env_int("QLDA_AI_PDF_OCR_MAX_PAGES", 40, 1, 120)
    requested = requested[:max_pages]
    try:
        digest = _pdf_sha(path, file_sha)
    except Exception:
        digest = hashlib.sha256(str(path).encode("utf-8")).hexdigest()

    cached = _read_ocr_cache(digest)
    pages: dict[int, str] = {page_no: cached[page_no] for page_no in requested if page_no in cached}
    missing = [page_no for page_no in requested if page_no not in pages]
    if not missing:
        return {
            "status": "ok",
            "reason": "cache_hit",
            "pages": sorted(pages.items()),
            "missed_pages": [],
            "from_cache": True,
            "sha256": digest,
            "rendered_pages": [],
            "embedded_fallback_pages": [],
        }

    errors: list[str] = []
    no_image_pages: list[int] = []
    rendered_pages: list[int] = []
    embedded_fallback_pages: list[int] = []
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        if bool(getattr(reader, "is_encrypted", False)):
            try:
                unlocked = reader.decrypt("")
            except Exception:
                unlocked = 0
            if not unlocked:
                return {
                    "status": "encrypted",
                    "reason": "encrypted_pdf",
                    "pages": sorted(pages.items()),
                    "missed_pages": missing,
                    "from_cache": bool(pages),
                    "sha256": digest,
                }

        used = sum(len(value) for value in pages.values())
        for page_no in missing:
            if page_no > len(reader.pages) or used >= max_chars:
                continue

            inputs: list[tuple[bytes, str]] = []
            rendered = _render_pdf_page(path, page_no)
            if rendered is not None:
                inputs.append(rendered)
                rendered_pages.append(page_no)
            else:
                for raw in _page_images(reader.pages[page_no - 1]):
                    inputs.append(_vision_ready_image(raw))
                if inputs:
                    embedded_fallback_pages.append(page_no)

            if not inputs:
                no_image_pages.append(page_no)
                continue

            chunks: list[str] = []
            seen: set[str] = set()
            for image_index, (image_bytes, mime) in enumerate(inputs, start=1):
                if used >= max_chars:
                    break
                source_note = "toàn bộ trang đã raster hóa" if page_no in rendered_pages else f"ảnh nhúng {image_index}"
                prompt = (
                    f"OCR trang {page_no} ({source_note}) của một hồ sơ quản lý dự án xây dựng. "
                    "Hãy chép lại nguyên văn toàn bộ chữ nhìn thấy, ưu tiên tiếng Việt; giữ số liệu, ngày tháng, "
                    "mã hồ sơ, đầu mục và nội dung bảng theo thứ tự đọc. Không tóm tắt, không diễn giải, "
                    "không suy đoán chữ không nhìn thấy. Nếu trang thật sự không có chữ, chỉ trả 'NO_TEXT'."
                )
                try:
                    text = _clean_text(
                        NativeProviderGateway.vision_text(
                            int(workspace_scope),
                            data=image_bytes,
                            mime_type=mime,
                            prompt=prompt,
                        )
                    )
                except Exception as exc:
                    errors.append(f"P{page_no}:{exc.__class__.__name__}")
                    continue
                if _looks_like_no_text(text):
                    continue
                key = hashlib.sha256(text.encode("utf-8")).hexdigest()
                if key in seen:
                    continue
                seen.add(key)
                room = max_chars - used
                if room <= 0:
                    break
                if len(text) > room:
                    text = text[:room].rstrip() + "…"
                chunks.append(text)
                used += len(text)
            if chunks:
                pages[page_no] = "\n".join(chunks)
    except Exception as exc:
        errors.append(exc.__class__.__name__)

    if pages:
        _write_ocr_cache(digest, pages)
    missed = [page_no for page_no in requested if page_no not in pages]
    if not pages:
        if no_image_pages and len(no_image_pages) >= len(missing):
            reason = "page_render_and_embedded_image_unavailable"
        elif errors:
            reason = "vision_provider_error"
        else:
            reason = "no_visible_text"
    elif missed:
        reason = "partial_ocr"
    else:
        reason = "ok"
    return {
        "status": "ok" if pages else "unavailable",
        "reason": reason,
        "pages": sorted(pages.items()),
        "missed_pages": missed,
        "no_image_pages": no_image_pages,
        "rendered_pages": rendered_pages,
        "embedded_fallback_pages": embedded_fallback_pages,
        "errors": errors,
        "from_cache": not missing or all(page_no in cached for page_no in pages),
        "sha256": digest,
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
        terms = _terms(question)
        scored = [
            (
                _score(file_row, terms, ("kind", "subtype", "record_code", "name", "upload_purpose")),
                file_row,
            )
            for file_row in files
        ]
        matched = [row for score, row in scored if score > 0]
        chosen = [(row, None) for row in (matched or files)[:max_files]]

    lines = ["## NỘI DUNG PDF ĐÍNH KÈM LIVE"]
    per_file_chars = max(2500, max_chars // max(1, len(chosen)))
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

        extracted = _extract_pdf_pages(path, max_chars=per_file_chars)
        status = str(extracted.get("status") or "error")
        native_pages = {int(page_no): str(text) for page_no, text in list(extracted.get("pages") or [])}
        page_count = int(extracted.get("page_count") or 0)
        blank_numbers = [int(x) for x in list(extracted.get("blank_page_numbers") or []) if int(x or 0) > 0]
        lines.append(
            f"[PDF-FILE:{file_id}] hồ sơ={record_code} | tiêu đề={title} | file={name} | số trang={page_count}"
        )

        if status == "encrypted":
            lines.append(f"[PDF-ENCRYPTED:{file_id}] file={name} có mật khẩu/mã hóa; AI chưa đọc được nội dung.")
            continue
        if status == "error":
            lines.append(
                f"[PDF-READ-ERROR:{file_id}] file={name} không trích xuất được text ({extracted.get('error','PDFError')})."
            )
            continue

        ocr_pages: dict[int, str] = {}
        ocr_result: dict[str, Any] = {}
        pages_needing_ocr = blank_numbers
        if status == "no_text" and not pages_needing_ocr and page_count > 0:
            pages_needing_ocr = list(range(1, min(page_count, 120) + 1))
        if pages_needing_ocr:
            workspace_scope = int((record or {}).get("project_id") or ids[0])
            ocr_result = _ocr_scanned_pdf(
                path,
                workspace_scope=workspace_scope,
                page_numbers=pages_needing_ocr,
                file_sha=str(file_row.get("sha256") or ""),
                max_chars=per_file_chars,
            )
            ocr_pages = {int(page_no): str(text) for page_no, text in list(ocr_result.get("pages") or [])}

        all_page_numbers = sorted(set(native_pages) | set(ocr_pages))
        for page_no in all_page_numbers:
            if page_no in native_pages:
                lines.append(f"[PDF:{file_id}:P{page_no}] {native_pages[page_no]}")
            elif page_no in ocr_pages:
                lines.append(f"[PDF-OCR:{file_id}:P{page_no}] {ocr_pages[page_no]}")

        missed_pages = [
            int(x) for x in list(ocr_result.get("missed_pages") or [])
            if int(x or 0) > 0 and int(x) not in native_pages
        ]
        if status == "no_text" and not ocr_pages:
            ocr_status = str(ocr_result.get("status") or "unavailable")
            reason = str(ocr_result.get("reason") or "unknown")
            lines.append(
                f"[PDF-SCAN-NO-TEXT:{file_id}] file={name} là PDF scan/ảnh và OCR/Vision chưa lấy được chữ "
                f"(trạng thái={ocr_status}; lý_do={reason}). Không được suy nội dung từ tên file hoặc metadata."
            )
        elif missed_pages:
            shown = ",".join(str(x) for x in missed_pages[:20])
            reason = str(ocr_result.get("reason") or "partial_ocr")
            lines.append(
                f"[PDF-PARTIAL-NO-TEXT:{file_id}] OCR chưa đọc được trang {shown} (lý_do={reason}); "
                "chỉ kết luận từ các trang PDF/PDF-OCR có nguồn."
            )
        elif blank_numbers and ocr_pages:
            source = "cache" if bool(ocr_result.get("from_cache")) else "vision-page-render"
            lines.append(
                f"[PDF-OCR-COMPLETE:{file_id}] các trang scan đã được OCR bằng {source}; "
                "không dùng metadata thay cho nội dung."
            )

        if bool(extracted.get("truncated")):
            lines.append(f"[PDF-TRUNCATED:{file_id}] nội dung PDF dài; context chỉ chứa phần đầu trong giới hạn an toàn.")

    return _clip_context(lines, max_chars)


__all__ = ["build_pdf_attachment_context"]
