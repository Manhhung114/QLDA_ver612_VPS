from __future__ import annotations

import os
import tempfile
from datetime import date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from ai_service import (
    AIServiceError,
    AISettings,
    GeminiProjectAssistant,
    GeminiSettings,
    OpenAIProjectAssistant,
    gemini_error_to_service_error,
    openai_error_to_service_error,
)
from settings_store import get_ai_runtime_settings


PATCH_MARKER = "V6.22 CONTRACT MANAGEMENT V1 WORKSPACE PRIVATE"
RECORDS_TABLE = "project_contract_records"
FILES_TABLE = "project_contract_record_files"
VISIBLE_APPROVAL_ROLES = {"SITE_MANAGEMENT", "PROJECT_MANAGEMENT"}
RECORD_TYPES = ("Hợp đồng", "Phụ lục")
TZ = ZoneInfo("Asia/Ho_Chi_Minh")
AI_MAX_FILES = 20
AI_MAX_FILE_BYTES = 25 * 1024 * 1024
AI_MAX_TOTAL_BYTES = 80 * 1024 * 1024

CONTRACT_AI_SYSTEM = """Bạn là trợ lý đọc hợp đồng xây dựng trong QLDA.
Chỉ trả lời từ metadata và các file Hợp đồng/Phụ lục được cung cấp của ĐÚNG workspace hiện tại.
Không dùng dữ liệu BOQ, Claim/IPC, VO, tiến độ, hồ sơ, bản vẽ hoặc workspace khác.
Không suy đoán điều khoản không có trong nguồn. Nếu không đủ dữ liệu, nói rõ không tìm thấy.
Khi kết luận, ưu tiên nêu số hồ sơ/tên file và trang hoặc mục nếu tài liệu thể hiện được.
Không tự phê duyệt, sửa đổi hoặc diễn giải thành ý kiến pháp lý chắc chắn khi văn bản không rõ."""


def _now() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")


def _text(value: Any) -> str:
    return str(value or "").strip()


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


def _identity(identity: Any) -> dict[str, str]:
    row = _rowdict(identity)
    return {
        "email": _text(row.get("email")).lower(),
        "name": _text(row.get("name") or row.get("email") or "Người dùng"),
        "role": _text(row.get("role")).lower(),
        "approval_role": _text(row.get("approval_role")).upper(),
    }


def can_access_contract_management(identity: Any) -> bool:
    user = _identity(identity)
    return bool(user["role"] == "admin" or user["approval_role"] in VISIBLE_APPROVAL_ROLES)


def can_edit_contract_management(identity: Any, *, is_admin: bool = False) -> bool:
    user = _identity(identity)
    return bool(
        can_access_contract_management(user)
        and (is_admin or user["role"] in {"admin", "update"})
    )


def ensure_schema_connection(connection) -> None:
    connection.executescript(f"""
    CREATE TABLE IF NOT EXISTS {RECORDS_TABLE}(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        workspace_project_id INTEGER NOT NULL,
        record_type TEXT NOT NULL DEFAULT 'Hợp đồng',
        record_no TEXT NOT NULL,
        title TEXT DEFAULT '',
        signed_date TEXT DEFAULT '',
        amount REAL NOT NULL DEFAULT 0,
        currency TEXT NOT NULL DEFAULT 'VND',
        effective_date TEXT DEFAULT '',
        expiry_date TEXT DEFAULT '',
        note TEXT DEFAULT '',
        current_file_id TEXT DEFAULT '',
        current_file_name TEXT DEFAULT '',
        current_mime_type TEXT DEFAULT '',
        current_file_size INTEGER NOT NULL DEFAULT 0,
        created_by_email TEXT DEFAULT '',
        created_by_name TEXT DEFAULT '',
        created_at TEXT DEFAULT '',
        updated_by_email TEXT DEFAULT '',
        updated_by_name TEXT DEFAULT '',
        updated_at TEXT DEFAULT '',
        FOREIGN KEY(workspace_project_id) REFERENCES projects(id) ON DELETE CASCADE,
        UNIQUE(workspace_project_id, record_type, record_no)
    );
    CREATE INDEX IF NOT EXISTS idx_contract_records_workspace
        ON {RECORDS_TABLE}(workspace_project_id, record_type, signed_date, id);

    CREATE TABLE IF NOT EXISTS {FILES_TABLE}(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        record_id INTEGER NOT NULL,
        workspace_project_id INTEGER NOT NULL,
        file_id TEXT NOT NULL,
        version_no INTEGER NOT NULL DEFAULT 1,
        file_name TEXT DEFAULT '',
        mime_type TEXT DEFAULT '',
        file_size INTEGER NOT NULL DEFAULT 0,
        uploaded_by TEXT DEFAULT '',
        uploaded_at TEXT DEFAULT '',
        is_current INTEGER NOT NULL DEFAULT 1,
        FOREIGN KEY(record_id) REFERENCES {RECORDS_TABLE}(id) ON DELETE CASCADE,
        FOREIGN KEY(workspace_project_id) REFERENCES projects(id) ON DELETE CASCADE,
        UNIQUE(record_id, file_id)
    );
    CREATE INDEX IF NOT EXISTS idx_contract_files_record
        ON {FILES_TABLE}(record_id, is_current, version_no, id);
    CREATE INDEX IF NOT EXISTS idx_contract_files_workspace
        ON {FILES_TABLE}(workspace_project_id, record_id, is_current);
    """)


def ensure_schema(db) -> None:
    with db.connect() as connection:
        ensure_schema_connection(connection)


def _date_text(value: Any) -> str:
    if value in {None, ""}:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = _text(value)
    if not text:
        return ""
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(text[:10], fmt).date().isoformat()
        except Exception:
            pass
    return text[:10]


def _amount(value: Any) -> float:
    try:
        return float(value or 0)
    except Exception:
        return 0.0


def create_contract_record(
    db,
    *,
    workspace_project_id: int,
    record_type: str,
    record_no: str,
    title: str = "",
    signed_date: Any = "",
    amount: float = 0,
    currency: str = "VND",
    effective_date: Any = "",
    expiry_date: Any = "",
    note: str = "",
    actor: Any = None,
) -> dict[str, Any]:
    ensure_schema(db)
    record_type = record_type if record_type in RECORD_TYPES else "Hợp đồng"
    record_no = _text(record_no)
    if not record_no:
        raise ValueError("Số hợp đồng/phụ lục không được để trống.")
    user = _identity(actor)
    stamp = _now()
    with db.connect() as connection:
        cursor = connection.execute(
            f"""INSERT INTO {RECORDS_TABLE}(
                workspace_project_id,record_type,record_no,title,signed_date,amount,currency,
                effective_date,expiry_date,note,created_by_email,created_by_name,created_at,
                updated_by_email,updated_by_name,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                int(workspace_project_id), record_type, record_no, _text(title), _date_text(signed_date),
                _amount(amount), _text(currency) or "VND", _date_text(effective_date), _date_text(expiry_date),
                _text(note), user["email"], user["name"], stamp, user["email"], user["name"], stamp,
            ),
        )
        record_id = int(getattr(cursor, "lastrowid", 0) or 0)
        if record_id <= 0:
            row = connection.execute(
                f"SELECT id FROM {RECORDS_TABLE} WHERE workspace_project_id=? AND record_type=? AND record_no=? ORDER BY id DESC LIMIT 1",
                (int(workspace_project_id), record_type, record_no),
            ).fetchone()
            record_id = int(_rowdict(row).get("id") or (row[0] if row else 0))
        return _rowdict(connection.execute(f"SELECT * FROM {RECORDS_TABLE} WHERE id=?", (record_id,)).fetchone())


def update_contract_record(db, record_id: int, *, workspace_project_id: int, actor: Any = None, **values: Any) -> dict[str, Any]:
    ensure_schema(db)
    current = get_contract_record(db, int(record_id), workspace_project_id=int(workspace_project_id))
    if not current:
        raise ValueError("Không tìm thấy hồ sơ hợp đồng trong workspace hiện tại.")
    record_type = values.get("record_type", current.get("record_type"))
    record_type = record_type if record_type in RECORD_TYPES else "Hợp đồng"
    record_no = _text(values.get("record_no", current.get("record_no")))
    if not record_no:
        raise ValueError("Số hợp đồng/phụ lục không được để trống.")
    user = _identity(actor)
    with db.connect() as connection:
        connection.execute(
            f"""UPDATE {RECORDS_TABLE} SET
                record_type=?,record_no=?,title=?,signed_date=?,amount=?,currency=?,effective_date=?,expiry_date=?,note=?,
                updated_by_email=?,updated_by_name=?,updated_at=?
                WHERE id=? AND workspace_project_id=?""",
            (
                record_type, record_no, _text(values.get("title", current.get("title"))),
                _date_text(values.get("signed_date", current.get("signed_date"))),
                _amount(values.get("amount", current.get("amount"))),
                _text(values.get("currency", current.get("currency"))) or "VND",
                _date_text(values.get("effective_date", current.get("effective_date"))),
                _date_text(values.get("expiry_date", current.get("expiry_date"))),
                _text(values.get("note", current.get("note"))),
                user["email"], user["name"], _now(), int(record_id), int(workspace_project_id),
            ),
        )
    return get_contract_record(db, int(record_id), workspace_project_id=int(workspace_project_id))


def get_contract_record(db, record_id: int, *, workspace_project_id: int) -> dict[str, Any]:
    ensure_schema(db)
    with db.connect() as connection:
        row = connection.execute(
            f"SELECT * FROM {RECORDS_TABLE} WHERE id=? AND workspace_project_id=? LIMIT 1",
            (int(record_id), int(workspace_project_id)),
        ).fetchone()
        return _rowdict(row)


def list_contract_records(db, workspace_project_id: int) -> list[dict[str, Any]]:
    ensure_schema(db)
    with db.connect() as connection:
        rows = connection.execute(
            f"""SELECT * FROM {RECORDS_TABLE}
                WHERE workspace_project_id=?
                ORDER BY CASE record_type WHEN 'Hợp đồng' THEN 0 ELSE 1 END,
                         COALESCE(signed_date,''), id""",
            (int(workspace_project_id),),
        ).fetchall()
        return [_rowdict(row) for row in rows]


def register_contract_file(db, *, record_id: int, workspace_project_id: int, file_item: dict[str, Any], uploaded_by: str = "") -> dict[str, Any]:
    ensure_schema(db)
    record = get_contract_record(db, int(record_id), workspace_project_id=int(workspace_project_id))
    if not record:
        raise ValueError("Không tìm thấy hồ sơ để gắn file.")
    file_id = _text(file_item.get("id"))
    if not file_id:
        raise ValueError("Upload thành công nhưng thiếu file ID.")
    name = _text(file_item.get("name")) or "attachment"
    mime = _text(file_item.get("mime_type")) or "application/octet-stream"
    size = int(file_item.get("size") or 0)
    with db.connect() as connection:
        row = connection.execute(
            f"SELECT COALESCE(MAX(version_no),0) AS n FROM {FILES_TABLE} WHERE record_id=?",
            (int(record_id),),
        ).fetchone()
        version = int(_rowdict(row).get("n") or (row[0] if row else 0)) + 1
        connection.execute(f"UPDATE {FILES_TABLE} SET is_current=0 WHERE record_id=?", (int(record_id),))
        connection.execute(
            f"""INSERT INTO {FILES_TABLE}(
                record_id,workspace_project_id,file_id,version_no,file_name,mime_type,file_size,uploaded_by,uploaded_at,is_current
            ) VALUES(?,?,?,?,?,?,?,?,?,1)""",
            (int(record_id), int(workspace_project_id), file_id, version, name, mime, size, _text(uploaded_by), _now()),
        )
        connection.execute(
            f"""UPDATE {RECORDS_TABLE} SET current_file_id=?,current_file_name=?,current_mime_type=?,current_file_size=?,updated_at=?
                WHERE id=? AND workspace_project_id=?""",
            (file_id, name, mime, size, _now(), int(record_id), int(workspace_project_id)),
        )
    return {"file_id": file_id, "version_no": version, "file_name": name, "mime_type": mime, "file_size": size}


def list_contract_file_versions(db, record_id: int, *, workspace_project_id: int) -> list[dict[str, Any]]:
    ensure_schema(db)
    with db.connect() as connection:
        rows = connection.execute(
            f"""SELECT * FROM {FILES_TABLE}
                WHERE record_id=? AND workspace_project_id=?
                ORDER BY version_no DESC, id DESC""",
            (int(record_id), int(workspace_project_id)),
        ).fetchall()
        return [_rowdict(row) for row in rows]


def delete_contract_record(db, record_id: int, *, workspace_project_id: int) -> list[str]:
    ensure_schema(db)
    with db.connect() as connection:
        file_rows = connection.execute(
            f"SELECT file_id FROM {FILES_TABLE} WHERE record_id=? AND workspace_project_id=?",
            (int(record_id), int(workspace_project_id)),
        ).fetchall()
        file_ids = [_text(_rowdict(row).get("file_id") or (row[0] if row else "")) for row in file_rows]
        connection.execute(
            f"DELETE FROM {RECORDS_TABLE} WHERE id=? AND workspace_project_id=?",
            (int(record_id), int(workspace_project_id)),
        )
    return [x for x in file_ids if x]


def contract_summary(db, workspace_project_id: int) -> dict[str, int]:
    rows = list_contract_records(db, int(workspace_project_id))
    return {
        "total": len(rows),
        "contracts": sum(1 for x in rows if _text(x.get("record_type")) == "Hợp đồng"),
        "appendices": sum(1 for x in rows if _text(x.get("record_type")) == "Phụ lục"),
        "with_file": sum(1 for x in rows if _text(x.get("current_file_id"))),
    }


def _project_code(db, workspace_project_id: int) -> str:
    try:
        project = _rowdict(db.project(int(workspace_project_id)))
        return _text(project.get("code")) or f"WS-{int(workspace_project_id)}"
    except Exception:
        return f"WS-{int(workspace_project_id)}"


def _format_date(value: Any) -> str:
    text = _text(value)
    if not text:
        return "—"
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").strftime("%d/%m/%Y")
    except Exception:
        return text


def _format_amount(value: Any, currency: str = "VND") -> str:
    amount = _amount(value)
    if not amount:
        return "—"
    if _text(currency).upper() == "VND":
        return f"{amount:,.0f} VND".replace(",", ".")
    return f"{amount:,.2f} {_text(currency)}"


def _upload_record_file(st, db, gateway, session_token: str, workspace_project_id: int, record: dict[str, Any], uploaded_file, actor: Any) -> dict[str, Any] | None:
    if uploaded_file is None:
        return None
    if gateway is None or not session_token:
        raise RuntimeError("Chưa có phiên lưu trữ VPS để tải file.")
    data = uploaded_file.getvalue()
    item = gateway.upload_bytes(
        session_token,
        project_code=_project_code(db, workspace_project_id),
        kind="contract",
        subtype="Hop_dong" if _text(record.get("record_type")) == "Hợp đồng" else "Phu_luc",
        record_code=f"CONTRACT-{int(record['id']):06d}",
        name=_text(uploaded_file.name) or "attachment",
        content=data,
        mime_type=_text(getattr(uploaded_file, "type", "")),
        upload_purpose=f"{_text(record.get('record_type'))} {_text(record.get('record_no'))}",
    )
    user = _identity(actor)
    register_contract_file(
        db,
        record_id=int(record["id"]),
        workspace_project_id=int(workspace_project_id),
        file_item=item,
        uploaded_by=user["email"],
    )
    return item


def _record_metadata_lines(records: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for row in records:
        lines.append(
            f"[HĐ:{row.get('id')}|{_text(row.get('record_no'))}|{_text(row.get('current_file_name')) or 'không-file'}] "
            f"loại={_text(row.get('record_type'))}; tiêu đề={_text(row.get('title'))}; "
            f"ngày ký={_text(row.get('signed_date'))}; giá trị={_amount(row.get('amount'))} {_text(row.get('currency')) or 'VND'}; "
            f"hiệu lực={_text(row.get('effective_date'))}; hết hiệu lực={_text(row.get('expiry_date'))}; ghi chú={_text(row.get('note'))}"
        )
    return lines


def build_contract_ai_prompt(records: list[dict[str, Any]], question: str, attached_names: list[str], skipped_names: list[str]) -> str:
    metadata = "\n".join(_record_metadata_lines(records)) or "Chưa có metadata hợp đồng/phụ lục."
    attachment_note = ", ".join(attached_names) if attached_names else "Không có file được đính kèm cho AI."
    skipped_note = ", ".join(skipped_names) if skipped_names else "Không có."
    return f"""WORKSPACE HỢP ĐỒNG HIỆN TẠI - METADATA:\n{metadata}\n\nFILE ĐÃ GỬI AI: {attachment_note}\nFILE KHÔNG GỬI DO GIỚI HẠN/KHÔNG ĐỌC ĐƯỢC: {skipped_note}\n\nCÂU HỎI:\n{_text(question)}\n\nHãy trả lời trực tiếp bằng tiếng Việt. Mỗi kết luận quan trọng phải chỉ ra hồ sơ/file nguồn dạng [HĐ:id|số hồ sơ|tên file]. Nếu xác định được trang/mục/điều thì nêu thêm. Không dùng dữ liệu ngoài workspace này."""


def _collect_ai_files(gateway, session_token: str, records: list[dict[str, Any]]) -> tuple[list[tuple[str, bytes]], list[str]]:
    files: list[tuple[str, bytes]] = []
    skipped: list[str] = []
    total = 0
    for row in records:
        file_id = _text(row.get("current_file_id"))
        name = _text(row.get("current_file_name")) or f"record-{row.get('id')}.bin"
        if not file_id:
            continue
        if len(files) >= AI_MAX_FILES:
            skipped.append(name)
            continue
        try:
            downloaded_name, _mime, content = gateway.download_bytes(session_token, file_id)
            content = bytes(content)
        except Exception:
            skipped.append(name)
            continue
        if not content or len(content) > AI_MAX_FILE_BYTES or total + len(content) > AI_MAX_TOTAL_BYTES:
            skipped.append(downloaded_name or name)
            continue
        files.append((downloaded_name or name, content))
        total += len(content)
    return files, skipped


def _ask_openai(settings: dict[str, Any], prompt: str, files: list[tuple[str, bytes]]) -> str:
    assistant = OpenAIProjectAssistant(
        Path("."),
        AISettings(api_key=_text(settings.get("api_key")), model=_text(settings.get("model")) or "gpt-5-mini", use_web=False),
    )
    client = assistant._client()
    uploaded_ids: list[str] = []
    temp_paths: list[str] = []
    try:
        content: list[dict[str, str]] = []
        for name, data in files:
            suffix = Path(name).suffix or ".bin"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(data)
                temp_paths.append(tmp.name)
            with open(temp_paths[-1], "rb") as fh:
                uploaded = client.files.create(file=fh, purpose="user_data")
            uploaded_ids.append(uploaded.id)
            content.append({"type": "input_file", "file_id": uploaded.id})
        content.append({"type": "input_text", "text": prompt})
        response = client.responses.create(
            model=assistant.model,
            store=False,
            input=[
                {"role": "developer", "content": CONTRACT_AI_SYSTEM},
                {"role": "user", "content": content},
            ],
        )
        return (getattr(response, "output_text", "") or "AI không trả về nội dung.").strip()
    except AIServiceError:
        raise
    except Exception as exc:
        raise openai_error_to_service_error(exc) from exc
    finally:
        for file_id in uploaded_ids:
            try:
                client.files.delete(file_id)
            except Exception:
                pass
        for path in temp_paths:
            try:
                Path(path).unlink(missing_ok=True)
            except Exception:
                pass


def _ask_gemini(settings: dict[str, Any], prompt: str, files: list[tuple[str, bytes]]) -> str:
    assistant = GeminiProjectAssistant(
        Path("."),
        GeminiSettings(api_key=_text(settings.get("api_key")), model=_text(settings.get("model")) or "auto", use_web=False),
    )
    client = assistant._client()
    uploaded = []
    temp_paths: list[str] = []
    try:
        from google.genai import types
        for name, data in files:
            suffix = Path(name).suffix or ".bin"
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(data)
                temp_paths.append(tmp.name)
            uploaded.append(client.files.upload(file=temp_paths[-1]))
        response = assistant._generate_content_with_fallback(
            client,
            contents=[prompt, *uploaded],
            config=types.GenerateContentConfig(system_instruction=CONTRACT_AI_SYSTEM),
        )
        return (getattr(response, "text", "") or "AI không trả về nội dung.").strip()
    except AIServiceError:
        raise
    except Exception as exc:
        raise gemini_error_to_service_error(exc) from exc
    finally:
        for item in uploaded:
            try:
                client.files.delete(name=item.name)
            except Exception:
                pass
        for path in temp_paths:
            try:
                Path(path).unlink(missing_ok=True)
            except Exception:
                pass
        try:
            client.close()
        except Exception:
            pass


def ask_contract_ai(db, gateway, session_token: str, *, workspace_project_id: int, question: str) -> str:
    question = _text(question)
    if not question:
        raise ValueError("Hãy nhập câu hỏi về hợp đồng/phụ lục.")
    records = list_contract_records(db, int(workspace_project_id))
    if not records:
        raise ValueError("Workspace hiện chưa có hợp đồng/phụ lục để AI tra cứu.")
    if gateway is None or not session_token:
        raise RuntimeError("Chưa có phiên lưu trữ để AI đọc file hợp đồng.")
    files, skipped = _collect_ai_files(gateway, session_token, records)
    prompt = build_contract_ai_prompt(records, question, [name for name, _ in files], skipped)
    settings = get_ai_runtime_settings()
    if not _text(settings.get("api_key")):
        raise AIServiceError("Chưa cấu hình API key cho AI trên máy chủ.")
    provider = _text(settings.get("provider")).lower()
    if provider == "gemini":
        return _ask_gemini(settings, prompt, files)
    return _ask_openai(settings, prompt, files)


def _date_input_value(value: Any):
    text = _text(value)
    if not text:
        return None
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def _render_file_links(st, gateway, session_token: str, file_id: str, *, prefix: str) -> None:
    if not gateway or not session_token or not file_id:
        st.caption("Chưa có file.")
        return
    try:
        item = gateway.file_info(session_token, file_id)
    except Exception as exc:
        st.warning(f"Không đọc được thông tin file: {exc}")
        return
    view_url = _text(item.get("webViewLink") or item.get("url"))
    download_url = _text(item.get("download_url"))
    c1, c2 = st.columns(2)
    if view_url:
        c1.link_button("📎 Mở file", view_url, use_container_width=True)
    else:
        c1.caption("Không có link xem trực tiếp")
    if download_url:
        c2.link_button("⬇️ Tải file", download_url, use_container_width=True)
    else:
        try:
            name, mime, data = gateway.download_bytes(session_token, file_id)
            c2.download_button("⬇️ Tải file", data=data, file_name=name, mime=mime, key=f"{prefix}_download_{file_id}", use_container_width=True)
        except Exception as exc:
            c2.caption(f"Không tải được: {exc}")


def render_contract_management_v622(
    st,
    db,
    workspace_project_id: int,
    *,
    identity: Any,
    is_admin: bool = False,
    gateway=None,
    session_token: str = "",
) -> None:
    if not can_access_contract_management(identity):
        st.warning("📑 Quản lý hợp đồng chỉ dành cho Admin, Ban điều hành và Ban quản lý dự án.")
        return

    ensure_schema(db)
    user = _identity(identity)
    can_edit = can_edit_contract_management(identity, is_admin=is_admin)
    rows = list_contract_records(db, int(workspace_project_id))
    summary = contract_summary(db, int(workspace_project_id))

    st.subheader("📑 Quản lý hợp đồng")
    st.caption("Dữ liệu độc lập theo workspace nhà thầu. Hợp đồng và Phụ lục được quản lý chung; không đồng bộ BOQ, Claim/IPC, VO hoặc các sheet khác.")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Tổng hồ sơ", summary["total"])
    c2.metric("Hợp đồng", summary["contracts"])
    c3.metric("Phụ lục", summary["appendices"])
    c4.metric("Có file", summary["with_file"])

    with st.expander("🤖 Hỏi AI về hợp đồng / phụ lục", expanded=False):
        st.caption("AI chỉ quét file và metadata của workspace đang mở; không dùng dữ liệu từ sheet hoặc workspace khác.")
        ai_key = f"contract_ai_history_{int(workspace_project_id)}"
        history = list(st.session_state.get(ai_key) or [])
        question = st.text_area("Câu hỏi", key=f"contract_ai_question_{workspace_project_id}", placeholder="Ví dụ: Điều khoản thanh toán của hợp đồng quy định như thế nào?")
        if st.button("🤖 Quét và trả lời", key=f"contract_ai_ask_{workspace_project_id}", type="primary"):
            try:
                with st.spinner("AI đang đọc hợp đồng và phụ lục của workspace hiện tại..."):
                    answer = ask_contract_ai(db, gateway, session_token, workspace_project_id=int(workspace_project_id), question=question)
                history.append({"q": question, "a": answer, "at": _now()})
                st.session_state[ai_key] = history[-6:]
            except Exception as exc:
                st.error(str(exc))
        for item in reversed(list(st.session_state.get(ai_key) or [])[-3:]):
            st.markdown(f"**Hỏi:** {_text(item.get('q'))}")
            st.markdown(_text(item.get("a")))
            st.divider()

    if can_edit:
        with st.expander("➕ Thêm hợp đồng / phụ lục", expanded=not bool(rows)):
            with st.form(f"contract_add_{workspace_project_id}", clear_on_submit=True):
                a1, a2 = st.columns(2)
                record_type = a1.selectbox("Loại hồ sơ", RECORD_TYPES)
                record_no = a2.text_input("Số hợp đồng / phụ lục *")
                title = st.text_input("Tên / nội dung")
                b1, b2, b3 = st.columns(3)
                signed_date = b1.date_input("Ngày ký", value=None)
                effective_date = b2.date_input("Ngày hiệu lực", value=None)
                expiry_date = b3.date_input("Ngày hết hiệu lực", value=None)
                c1, c2 = st.columns([2, 1])
                amount = c1.number_input("Giá trị", min_value=0.0, value=0.0, step=1000000.0, format="%.0f")
                currency = c2.selectbox("Tiền tệ", ["VND", "USD", "EUR"])
                note = st.text_area("Ghi chú")
                upload = st.file_uploader("File hợp đồng / phụ lục", type=["pdf", "doc", "docx", "xls", "xlsx", "txt", "csv"], key=f"contract_add_file_{workspace_project_id}")
                submitted = st.form_submit_button("💾 Lưu hồ sơ", type="primary")
            if submitted:
                try:
                    record = create_contract_record(
                        db,
                        workspace_project_id=int(workspace_project_id),
                        record_type=record_type,
                        record_no=record_no,
                        title=title,
                        signed_date=signed_date,
                        amount=amount,
                        currency=currency,
                        effective_date=effective_date,
                        expiry_date=expiry_date,
                        note=note,
                        actor=user,
                    )
                    if upload is not None:
                        _upload_record_file(st, db, gateway, session_token, int(workspace_project_id), record, upload, user)
                    st.success("Đã lưu hồ sơ hợp đồng.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Không lưu được hồ sơ: {exc}")
    else:
        st.info("Tài khoản hiện có quyền xem Quản lý hợp đồng nhưng không có quyền cập nhật dữ liệu.")

    rows = list_contract_records(db, int(workspace_project_id))
    search = st.text_input("🔍 Tìm hợp đồng / phụ lục", key=f"contract_search_{workspace_project_id}").strip().lower()
    type_filter = st.selectbox("Loại hồ sơ", ["Tất cả", *RECORD_TYPES], key=f"contract_type_filter_{workspace_project_id}")
    filtered = []
    for row in rows:
        if type_filter != "Tất cả" and _text(row.get("record_type")) != type_filter:
            continue
        haystack = " ".join(_text(row.get(k)) for k in ("record_type", "record_no", "title", "note", "current_file_name")).lower()
        if search and search not in haystack:
            continue
        filtered.append(row)

    table_rows = [
        {
            "Loại": _text(r.get("record_type")),
            "Số hồ sơ": _text(r.get("record_no")),
            "Tên / nội dung": _text(r.get("title")),
            "Ngày ký": _format_date(r.get("signed_date")),
            "Giá trị": _format_amount(r.get("amount"), r.get("currency")),
            "File": _text(r.get("current_file_name")) or "—",
            "Cập nhật": _text(r.get("updated_at")),
        }
        for r in filtered
    ]
    st.dataframe(table_rows, use_container_width=True, hide_index=True)
    if not filtered:
        st.info("Chưa có hồ sơ phù hợp.")
        return

    labels = {
        int(r["id"]): f"{_text(r.get('record_type'))} · {_text(r.get('record_no'))} · {_text(r.get('title')) or 'Không tiêu đề'}"
        for r in filtered
    }
    selected_id = st.selectbox(
        "Mở hồ sơ",
        list(labels),
        format_func=lambda x: labels.get(int(x), str(x)),
        key=f"contract_selected_{workspace_project_id}",
    )
    record = get_contract_record(db, int(selected_id), workspace_project_id=int(workspace_project_id))
    if not record:
        return

    st.markdown(f"### {_text(record.get('record_type'))} · {_text(record.get('record_no'))}")
    d1, d2, d3 = st.columns(3)
    d1.write(f"**Ngày ký:** {_format_date(record.get('signed_date'))}")
    d2.write(f"**Hiệu lực:** {_format_date(record.get('effective_date'))}")
    d3.write(f"**Hết hiệu lực:** {_format_date(record.get('expiry_date'))}")
    st.write(f"**Tên / nội dung:** {_text(record.get('title')) or '—'}")
    st.write(f"**Giá trị:** {_format_amount(record.get('amount'), record.get('currency'))}")
    if _text(record.get("note")):
        st.write(f"**Ghi chú:** {_text(record.get('note'))}")

    st.markdown("#### 📎 File hiện tại")
    if _text(record.get("current_file_id")):
        st.write(f"**{_text(record.get('current_file_name'))}**")
        _render_file_links(st, gateway, session_token, _text(record.get("current_file_id")), prefix=f"contract_{record['id']}")
    else:
        st.info("Hồ sơ này chưa có file đính kèm.")

    versions = list_contract_file_versions(db, int(record["id"]), workspace_project_id=int(workspace_project_id))
    if versions:
        with st.expander(f"🕘 Lịch sử file ({len(versions)} phiên bản)", expanded=False):
            for version in versions:
                current_mark = " · hiện tại" if int(version.get("is_current") or 0) else ""
                st.write(f"**V{int(version.get('version_no') or 0)}{current_mark}** — {_text(version.get('file_name'))} — {_text(version.get('uploaded_at'))}")
                _render_file_links(st, gateway, session_token, _text(version.get("file_id")), prefix=f"contract_ver_{version['id']}")

    if can_edit:
        with st.expander("✏️ Cập nhật hồ sơ / thay file", expanded=False):
            with st.form(f"contract_edit_{record['id']}"):
                e1, e2 = st.columns(2)
                edit_type = e1.selectbox("Loại hồ sơ", RECORD_TYPES, index=RECORD_TYPES.index(_text(record.get("record_type"))) if _text(record.get("record_type")) in RECORD_TYPES else 0)
                edit_no = e2.text_input("Số hợp đồng / phụ lục *", value=_text(record.get("record_no")))
                edit_title = st.text_input("Tên / nội dung", value=_text(record.get("title")))
                f1, f2, f3 = st.columns(3)
                edit_signed = f1.date_input("Ngày ký", value=_date_input_value(record.get("signed_date")))
                edit_effective = f2.date_input("Ngày hiệu lực", value=_date_input_value(record.get("effective_date")))
                edit_expiry = f3.date_input("Ngày hết hiệu lực", value=_date_input_value(record.get("expiry_date")))
                g1, g2 = st.columns([2, 1])
                edit_amount = g1.number_input("Giá trị", min_value=0.0, value=_amount(record.get("amount")), step=1000000.0, format="%.0f")
                currencies = ["VND", "USD", "EUR"]
                current_currency = _text(record.get("currency")) or "VND"
                edit_currency = g2.selectbox("Tiền tệ", currencies, index=currencies.index(current_currency) if current_currency in currencies else 0)
                edit_note = st.text_area("Ghi chú", value=_text(record.get("note")))
                replacement = st.file_uploader("🔄 File mới (nếu cần thay/cập nhật)", type=["pdf", "doc", "docx", "xls", "xlsx", "txt", "csv"], key=f"contract_replace_{record['id']}")
                saved = st.form_submit_button("💾 Cập nhật", type="primary")
            if saved:
                try:
                    updated = update_contract_record(
                        db,
                        int(record["id"]),
                        workspace_project_id=int(workspace_project_id),
                        actor=user,
                        record_type=edit_type,
                        record_no=edit_no,
                        title=edit_title,
                        signed_date=edit_signed,
                        amount=edit_amount,
                        currency=edit_currency,
                        effective_date=edit_effective,
                        expiry_date=edit_expiry,
                        note=edit_note,
                    )
                    if replacement is not None:
                        _upload_record_file(st, db, gateway, session_token, int(workspace_project_id), updated, replacement, user)
                    st.success("Đã cập nhật hồ sơ.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Không cập nhật được hồ sơ: {exc}")

    if is_admin:
        with st.expander("🗑 Xóa hồ sơ · Admin", expanded=False):
            confirm = st.checkbox("Tôi xác nhận xóa hồ sơ này và đưa toàn bộ file của hồ sơ vào thùng rác VPS.", key=f"contract_delete_confirm_{record['id']}")
            typed = st.text_input("Nhập lại số hồ sơ để xác nhận", key=f"contract_delete_code_{record['id']}")
            if st.button("🗑 Xóa hồ sơ", key=f"contract_delete_{record['id']}", disabled=not (confirm and typed.strip() == _text(record.get("record_no")))):
                try:
                    file_ids = [x.get("file_id") for x in versions if _text(x.get("file_id"))]
                    if gateway is not None and session_token:
                        for file_id in file_ids:
                            gateway.trash_file(session_token, _text(file_id))
                    delete_contract_record(db, int(record["id"]), workspace_project_id=int(workspace_project_id))
                    st.success("Đã xóa hồ sơ hợp đồng.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Không xóa được hồ sơ: {exc}")
