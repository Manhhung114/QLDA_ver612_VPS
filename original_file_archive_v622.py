from __future__ import annotations

import mimetypes
import os
from typing import Any

PATCH_MARKER = "V6.22 ORIGINAL FILE ARCHIVE V1"
SOURCE_KIND = "source_upload"


def _is_local_vps() -> bool:
    return str(os.environ.get("QLDA_STORAGE_BACKEND", "drive") or "drive").strip().lower() == "local"


def _project_code(db: Any, project_id: int) -> str:
    row = db.project(int(project_id))
    if not row:
        raise ValueError("Không tìm thấy workspace dự án/nhà thầu để lưu file gốc.")
    try:
        return str(row["code"] or f"PROJECT-{int(project_id)}")
    except Exception:
        data = dict(row)
        return str(data.get("code") or f"PROJECT-{int(project_id)}")


def archive_original_upload(
    db: Any,
    project_id: int,
    session_token: str,
    *,
    subtype: str,
    record_code: str,
    name: str,
    content: bytes,
    upload_purpose: str = "original_source",
    mime_type: str = "",
) -> dict[str, Any] | None:
    if not _is_local_vps():
        return None
    raw = bytes(content or b"")
    if not raw:
        raise ValueError("File gốc đang trống; không thể lưu trên VPS.")
    filename = str(name or "upload.bin").strip() or "upload.bin"
    mime = str(mime_type or mimetypes.guess_type(filename)[0] or "application/octet-stream")
    from local_vps_backend_v622 import save_bytes
    return save_bytes(
        str(session_token or ""),
        project_code=_project_code(db, int(project_id)),
        kind=SOURCE_KIND,
        subtype=str(subtype or "OTHER").strip().upper(),
        record_code=str(record_code or "SOURCE").strip(),
        name=filename,
        content=raw,
        mime_type=mime,
        upload_purpose=str(upload_purpose or "original_source"),
    )


def list_original_uploads(db: Any, project_id: int, *, subtype: str = "", record_code: str = "") -> list[dict[str, Any]]:
    if not _is_local_vps():
        return []
    from local_vps_backend_v622 import _connect
    project_code = _project_code(db, int(project_id))
    where = ["project_code=%s", "kind=%s", "trashed=FALSE", "history=FALSE"]
    params: list[Any] = [project_code, SOURCE_KIND]
    if str(subtype or "").strip():
        where.append("UPPER(subtype)=UPPER(%s)")
        params.append(str(subtype).strip())
    if str(record_code or "").strip():
        where.append("record_code=%s")
        params.append(str(record_code).strip())
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id,project_code,kind,subtype,record_code,name,mime_type,size,sha256,storage_path,upload_purpose,uploaded_by,created_at,modified_at "
                "FROM qlda_local_files WHERE " + " AND ".join(where) + " ORDER BY created_at DESC",
                params,
            )
            return [dict(row) for row in cur.fetchall()]


def original_download_bytes(session_token: str, file_id: str) -> tuple[str, str, bytes]:
    if not _is_local_vps():
        raise ValueError("Tải file gốc qua helper này chỉ áp dụng VPS Local Storage.")
    from local_vps_backend_v622 import download_bytes
    return download_bytes(str(session_token or ""), str(file_id or ""))
