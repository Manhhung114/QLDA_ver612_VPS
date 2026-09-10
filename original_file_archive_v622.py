from __future__ import annotations

import hashlib
import inspect
import mimetypes
import os
from typing import Any

PATCH_MARKER = "V6.22 ORIGINAL FILE ARCHIVE V3"
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
    """Store exact uploaded bytes on VPS before any parser/AI consumes them."""
    if not _is_local_vps():
        return None
    raw = bytes(content or b"")
    if not raw:
        raise ValueError("File gốc đang trống; không thể lưu trên VPS.")
    filename = str(name or "upload.bin").strip() or "upload.bin"
    subtype_text = str(subtype or "OTHER").strip().upper()
    record_text = str(record_code or "SOURCE").strip()
    project_code = _project_code(db, int(project_id))
    digest = hashlib.sha256(raw).hexdigest()

    from local_vps_backend_v622 import _connect, _file_public, save_bytes
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT * FROM qlda_local_files
                   WHERE project_code=%s AND kind=%s AND UPPER(subtype)=UPPER(%s)
                     AND record_code=%s AND sha256=%s AND trashed=FALSE
                   ORDER BY created_at DESC LIMIT 1""",
                (project_code, SOURCE_KIND, subtype_text, record_text, digest),
            )
            existing = cur.fetchone()
            if existing:
                item = dict(existing)
                public = _file_public(item)
                public.update({"subtype": item.get("subtype"), "record_code": item.get("record_code"), "sha256": item.get("sha256")})
                return public

    mime = str(mime_type or mimetypes.guess_type(filename)[0] or "application/octet-stream")
    return save_bytes(
        str(session_token or ""),
        project_code=project_code,
        kind=SOURCE_KIND,
        subtype=subtype_text,
        record_code=record_text,
        name=filename,
        content=raw,
        mime_type=mime,
        upload_purpose=str(upload_purpose or "original_source"),
    )


def list_original_uploads(db: Any, project_id: int, *, subtype: str = "", record_code: str = "") -> list[dict[str, Any]]:
    if not _is_local_vps():
        return []
    from local_vps_backend_v622 import _connect, _file_public
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
            cur.execute("SELECT * FROM qlda_local_files WHERE " + " AND ".join(where) + " ORDER BY created_at DESC", params)
            rows = [dict(row) for row in cur.fetchall()]
    out: list[dict[str, Any]] = []
    for row in rows:
        public = _file_public(row)
        public.update({
            "subtype": row.get("subtype"), "record_code": row.get("record_code"),
            "sha256": row.get("sha256"), "storage_path": row.get("storage_path"),
            "uploaded_by": row.get("uploaded_by"), "created_at": row.get("created_at"),
        })
        out.append(public)
    return out


def original_download_bytes(session_token: str, file_id: str) -> tuple[str, str, bytes]:
    if not _is_local_vps():
        raise ValueError("Tải file gốc qua helper này chỉ áp dụng VPS Local Storage.")
    from local_vps_backend_v622 import download_bytes
    return download_bytes(str(session_token or ""), str(file_id or ""))


def install_ipc_original_archive() -> None:
    """Patch IPC UI after due-date patch: archive exact workbook before parse."""
    import ipc_claim_v622 as ipc
    if getattr(ipc, "_qlda_original_archive_installed", False):
        return

    source = inspect.getsource(ipc.render_ipc_claim_ui)
    old_sig = "def render_ipc_claim_ui(db, project_id: int, *, can_update: bool = True):"
    new_sig = "def render_ipc_claim_ui(db, project_id: int, *, can_update: bool = True, session_token: str = ''):"
    if old_sig in source:
        source = source.replace(old_sig, new_sig, 1)
    elif "session_token:" not in source.split("\n", 1)[0]:
        raise RuntimeError("Không nhận diện được signature IPC để chuẩn hóa lưu file gốc.")

    uploader_old = '''        upload = st.file_uploader(\n            "Chọn file IPC/Claim (.xlsx / .xlsm)",\n            type=["xlsx", "xlsm"],\n            key=f"ipc_claim_upload_{pid}",\n        )\n        parsed = None\n'''
    uploader_new = '''        upload = st.file_uploader(\n            "Chọn file IPC/Claim (.xlsx / .xlsm)",\n            type=["xlsx", "xlsm"],\n            key=f"ipc_claim_upload_{pid}",\n        )\n        try:\n            _ipc_originals = _v622_list_original_uploads(db, pid, subtype="IPC", record_code="IPC")\n            if _ipc_originals:\n                _ipc_latest = _ipc_originals[0]\n                if _ipc_latest.get("download_url"):\n                    st.link_button(f"⬇️ Tải file IPC gốc: {_ipc_latest.get('name','IPC.xlsx')}", _ipc_latest["download_url"], width="stretch")\n        except Exception:\n            pass\n        parsed = None\n'''
    if uploader_old not in source:
        raise RuntimeError("Không tìm thấy uploader IPC để bổ sung tải file gốc.")
    source = source.replace(uploader_old, uploader_new, 1)

    old_parse = """        if upload is not None:\n            try:\n                parsed = parse_ipc_workbook(upload.getvalue(), upload.name)\n"""
    new_parse = """        if upload is not None:\n            try:\n                _ipc_raw = upload.getvalue()\n                _v622_archive_original_upload(\n                    db, pid, session_token, subtype=\"IPC\", record_code=\"IPC\",\n                    name=upload.name, content=_ipc_raw, upload_purpose=\"ipc_original\",\n                )\n                parsed = parse_ipc_workbook(_ipc_raw, upload.name)\n"""
    if old_parse not in source:
        raise RuntimeError("Không tìm thấy điểm parse IPC để lưu file gốc trước khi đọc.")
    source = source.replace(old_parse, new_parse, 1)

    ipc.hashlib = hashlib
    ipc._v622_archive_original_upload = archive_original_upload
    ipc._v622_list_original_uploads = list_original_uploads
    compile(source, "ipc_claim_v622_original_archive.py", "exec")
    exec(source, ipc.__dict__, ipc.__dict__)
    ipc._qlda_original_archive_installed = True
    ipc._qlda_original_archive_marker = PATCH_MARKER
