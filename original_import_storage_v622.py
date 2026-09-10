from __future__ import annotations

import mimetypes
import os
from typing import Any


PATCH_VERSION = "V6.22 ORIGINAL IMPORT STORAGE V1"
KIND = "source"


def local_storage_enabled() -> bool:
    return str(os.environ.get("QLDA_STORAGE_BACKEND", "drive") or "drive").strip().lower() == "local"


def _row_value(row: Any, key: str, default: Any = "") -> Any:
    if row is None:
        return default
    try:
        return row[key]
    except Exception:
        try:
            return dict(row).get(key, default)
        except Exception:
            return default


def _project_code(db, project_id: int) -> str:
    row = db.project(int(project_id))
    code = str(_row_value(row, "code", "") or "").strip()
    if not code:
        code = f"PROJECT-{int(project_id)}"
    return code


def _mime(filename: str, supplied: str = "") -> str:
    value = str(supplied or "").strip()
    if value:
        return value
    return mimetypes.guess_type(str(filename or ""))[0] or "application/octet-stream"


def archive_original_upload(
    db,
    project_id: int,
    session_token: str,
    source_type: str,
    record_code: str,
    filename: str,
    data: bytes,
    mime_type: str = "",
) -> dict[str, Any]:
    """Persist the exact uploaded bytes on VPS before business data is saved.

    Attachment/document/drawing uploads already use qlda_local_files. Import
    sources use the same physical store, under kind=source, so backup/delete/
    signed-download behavior is identical across the application.
    """
    raw = bytes(data or b"")
    if not raw:
        raise ValueError("File nguồn đang trống; không thể lưu bản gốc trên VPS.")
    if not local_storage_enabled():
        return {"ok": True, "stored": False, "backend": "non-local"}

    from local_vps_backend_v622 import save_bytes

    item = save_bytes(
        str(session_token or ""),
        project_code=_project_code(db, int(project_id)),
        kind=KIND,
        subtype=str(source_type or "IMPORT").strip().upper() or "IMPORT",
        record_code=str(record_code or "SOURCE").strip().upper() or "SOURCE",
        name=str(filename or "source.bin").strip() or "source.bin",
        content=raw,
        mime_type=_mime(filename, mime_type),
        upload_purpose="ORIGINAL_IMPORT",
    )
    out = dict(item or {})
    out.update({"ok": True, "stored": True, "backend": "local-vps"})
    return out


def list_original_uploads(
    db,
    project_id: int,
    session_token: str,
    source_type: str,
    record_code: str,
    *,
    include_history: bool = True,
) -> list[dict[str, Any]]:
    if not local_storage_enabled() or not str(session_token or "").strip():
        return []
    from local_vps_backend_v622 import list_record_files

    payload = list_record_files(
        str(session_token),
        project_code=_project_code(db, int(project_id)),
        kind=KIND,
        subtype=str(source_type or "IMPORT").strip().upper() or "IMPORT",
        record_code=str(record_code or "SOURCE").strip().upper() or "SOURCE",
        include_history=bool(include_history),
    )
    if isinstance(payload, dict):
        rows = payload.get("files") or payload.get("items") or payload.get("data") or []
    elif isinstance(payload, list):
        rows = payload
    else:
        rows = []
    out: list[dict[str, Any]] = []
    for row in rows:
        try:
            out.append(dict(row))
        except Exception:
            pass
    return out


def latest_original_upload(db, project_id: int, session_token: str, source_type: str, record_code: str) -> dict[str, Any] | None:
    rows = list_original_uploads(
        db, project_id, session_token, source_type, record_code, include_history=True
    )
    current = [row for row in rows if not bool(row.get("history"))]
    pool = current or rows
    if not pool:
        return None
    pool.sort(key=lambda row: str(row.get("modified_time") or row.get("created_at") or ""), reverse=True)
    return pool[0]


def _download_url(item: dict[str, Any] | None) -> str:
    if not item:
        return ""
    return str(item.get("download_url") or item.get("url") or item.get("webViewLink") or "").strip()


def render_original_link(st, db, project_id: int, session_token: str, source_type: str, record_code: str, label: str) -> None:
    try:
        item = latest_original_upload(db, project_id, session_token, source_type, record_code)
        url = _download_url(item)
        if not url:
            return
        name = str((item or {}).get("name") or "").strip()
        st.link_button(label + (f" · {name}" if name else ""), url, use_container_width=True)
    except Exception:
        # A missing historical source must never break the business sheet.
        return


def render_ipc_claim_ui_with_original(db, project_id: int, *, can_update: bool = True, session_token: str = "") -> None:
    """Wrap IPC UI so every saved Claim revision archives the exact Excel bytes."""
    import streamlit as st
    import ipc_claim_v622 as ipc

    captured: dict[str, Any] = {}
    original_uploader = st.file_uploader
    original_save = ipc.save_ipc_claim

    def uploader(*args, **kwargs):
        obj = original_uploader(*args, **kwargs)
        key = str(kwargs.get("key") or "")
        if obj is not None and key.startswith("ipc_claim_upload_"):
            try:
                captured["name"] = str(obj.name)
                captured["data"] = obj.getvalue()
                captured["mime"] = str(getattr(obj, "type", "") or "")
            except Exception:
                pass
        return obj

    def save_with_source(db_obj, pid, result):
        if captured.get("data"):
            archive_original_upload(
                db_obj,
                int(pid),
                session_token,
                "IPC",
                str(result.get("claim_code") or result.get("claim_no") or "IPC"),
                str(captured.get("name") or result.get("filename") or "IPC.xlsx"),
                bytes(captured.get("data") or b""),
                str(captured.get("mime") or ""),
            )
        return original_save(db_obj, pid, result)

    st.file_uploader = uploader
    ipc.save_ipc_claim = save_with_source
    try:
        ipc.render_ipc_claim_ui(db, int(project_id), can_update=bool(can_update))
    finally:
        ipc.save_ipc_claim = original_save
        st.file_uploader = original_uploader

    if local_storage_enabled():
        claims = ipc.list_ipc_claims(db, int(project_id))
        available: list[tuple[str, dict[str, Any]]] = []
        for claim in claims:
            code = str(claim.get("claim_code") or claim.get("claim_no") or "IPC")
            item = latest_original_upload(db, int(project_id), session_token, "IPC", code)
            if item and _download_url(item):
                available.append((code, item))
        if available:
            with st.expander("📦 File IPC/Claim gốc đã lưu trên VPS", expanded=False):
                for code, item in available:
                    st.link_button(
                        f"⬇️ {code} · {item.get('name') or 'file gốc'}",
                        _download_url(item),
                        key=f"ipc_original_{project_id}_{code}_{item.get('id','')}",
                        use_container_width=True,
                    )


def render_vo_ui_with_original(db, project_id: int, *, can_update: bool = True, session_token: str = "") -> None:
    """Wrap VO UI so every saved VO revision archives the exact Excel bytes."""
    import streamlit as st
    import vo_independent_v622 as vo

    captured: dict[str, Any] = {}
    original_uploader = st.file_uploader
    original_save = vo.core.save_vo

    def uploader(*args, **kwargs):
        obj = original_uploader(*args, **kwargs)
        key = str(kwargs.get("key") or "")
        if obj is not None and key.startswith("vo_independent_upload_"):
            try:
                captured["name"] = str(obj.name)
                captured["data"] = obj.getvalue()
                captured["mime"] = str(getattr(obj, "type", "") or "")
            except Exception:
                pass
        return obj

    def save_with_source(db_obj, pid, result):
        if captured.get("data"):
            archive_original_upload(
                db_obj,
                int(pid),
                session_token,
                "VO",
                str(result.get("vo_code") or result.get("vo_no") or "VO"),
                str(captured.get("name") or result.get("filename") or "VO.xlsx"),
                bytes(captured.get("data") or b""),
                str(captured.get("mime") or ""),
            )
        return original_save(db_obj, pid, result)

    st.file_uploader = uploader
    vo.core.save_vo = save_with_source
    try:
        vo.render_vo_ui(db, int(project_id), can_update=bool(can_update))
    finally:
        vo.core.save_vo = original_save
        st.file_uploader = original_uploader

    if local_storage_enabled():
        orders = vo.core.list_vos(db, int(project_id))
        available: list[tuple[str, dict[str, Any]]] = []
        for order in orders:
            code = str(order.get("vo_code") or order.get("vo_no") or "VO")
            item = latest_original_upload(db, int(project_id), session_token, "VO", code)
            if item and _download_url(item):
                available.append((code, item))
        if available:
            with st.expander("📦 File VO gốc đã lưu trên VPS", expanded=False):
                for code, item in available:
                    st.link_button(
                        f"⬇️ {code} · {item.get('name') or 'file gốc'}",
                        _download_url(item),
                        key=f"vo_original_{project_id}_{code}_{item.get('id','')}",
                        use_container_width=True,
                    )
