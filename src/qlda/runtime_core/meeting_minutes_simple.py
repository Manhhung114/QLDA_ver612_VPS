from __future__ import annotations

import inspect
from datetime import date, datetime
from typing import Any


DOC_TYPE = "BBHOP"
DOC_LABEL = "Biên bản họp"
_PATCH_FLAG = "_qlda_meeting_minutes_simple_renderer"


def _find_app_globals() -> dict[str, Any] | None:
    frame = inspect.currentframe()
    try:
        frame = frame.f_back if frame else None
        while frame is not None:
            glob = frame.f_globals
            cfg = glob.get("DOC_CONFIG")
            if isinstance(cfg, dict) and "NCR" in cfg and "BBHT" in cfg:
                return glob
            frame = frame.f_back
    finally:
        del frame
    return None


def _date_value(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except Exception:
            pass
    return date.today()


def _row(row: Any, key: str, default: Any = "") -> Any:
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


def _next_code(rows: list[Any]) -> str:
    stamp = date.today().strftime("%Y%m%d")
    prefix = f"BBH-{stamp}-"
    used = {str(_row(r, "code", "")).strip() for r in rows}
    idx = 1
    while f"{prefix}{idx:03d}" in used:
        idx += 1
    return f"{prefix}{idx:03d}"


def _record_payload(record: Any, **changes: Any) -> dict[str, Any]:
    payload = {
        "code": _row(record, "code", ""),
        "subject": _row(record, "subject", ""),
        "discipline": _row(record, "discipline", ""),
        "contractor": _row(record, "contractor", ""),
        "issuer": _row(record, "issuer", ""),
        "assignee": _row(record, "assignee", ""),
        "issue_date": _row(record, "issue_date", ""),
        "due_date": "",
        "closed_date": "",
        "status": "",
        "priority": "",
        "related_wbs": "",
        "description": _row(record, "description", ""),
        "response": _row(record, "response", ""),
        "note": _row(record, "note", ""),
        "cost_impact": 0,
        "time_impact_days": 0,
    }
    payload.update(changes)
    return payload


def _normalize_existing_rows(db, pid: int, rows: list[Any]) -> list[Any]:
    changed = False
    for item in rows:
        if any(str(_row(item, key, "") or "").strip() for key in ("status", "due_date", "closed_date", "priority", "related_wbs")):
            db.save_document(pid, DOC_TYPE, _record_payload(item), int(_row(item, "id", 0)))
            changed = True
    return list(db.documents(pid, DOC_TYPE)) if changed else rows


def _build_ai(app_globals: dict[str, Any]):
    settings_fn = app_globals.get("_runtime_app_settings")
    if not callable(settings_fn):
        raise RuntimeError("Không đọc được cấu hình AI của ứng dụng.")
    cfg = settings_fn() or {}
    provider = str(cfg.get("ai_provider") or "openai").lower()
    db_path = app_globals.get("DB_PATH")

    if provider == "gemini":
        settings_cls = app_globals.get("GeminiSettings")
        assistant_cls = app_globals.get("GeminiProjectAssistant")
        if settings_cls is None or assistant_cls is None:
            raise RuntimeError("Gemini chưa sẵn sàng trong runtime.")
        settings = settings_cls(
            api_key=str(cfg.get("gemini_api_key") or "").strip(),
            model=str(cfg.get("gemini_model") or "auto").strip() or "auto",
            use_web=False,
        )
        if not settings.api_key:
            raise RuntimeError("Chưa cấu hình GEMINI_API_KEY.")
        return assistant_cls(db_path, settings), "Gemini"

    settings_cls = app_globals.get("AISettings")
    assistant_cls = app_globals.get("OpenAIProjectAssistant")
    if settings_cls is None or assistant_cls is None:
        raise RuntimeError("OpenAI chưa sẵn sàng trong runtime.")
    settings = settings_cls(
        api_key=str(cfg.get("openai_api_key") or "").strip(),
        model=str(cfg.get("openai_model") or "gpt-5-mini").strip() or "gpt-5-mini",
        use_web=False,
    )
    if not settings.api_key:
        raise RuntimeError("Chưa cấu hình OPENAI_API_KEY.")
    return assistant_cls(db_path, settings), "OpenAI"


def _review_prompt(record: Any) -> str:
    return f"""Rà soát BIÊN BẢN HỌP dưới đây theo góc nhìn quản lý dự án xây dựng.
Chỉ dựa trên nội dung biên bản; không tự bịa thêm dữ kiện. Hãy:
1. Chỉ ra chỗ mơ hồ, mâu thuẫn, thiếu thông tin hoặc câu chữ dễ hiểu sai.
2. Kiểm tra các kết luận/đầu việc có nêu rõ người chịu trách nhiệm, mốc thời gian, phạm vi và kết quả cần bàn giao hay chưa.
3. Đánh dấu các số liệu, ngày tháng, tên đơn vị hoặc quyết định cần người dùng xác nhận lại.
4. Đề xuất cách viết lại ngắn gọn, rõ nghĩa nhưng không làm thay đổi nội dung đã thống nhất.
5. Cuối cùng lập bảng ACTION ITEMS gồm: Nội dung | Phụ trách | Thời hạn | Ghi chú. Nếu biên bản không nêu thì ghi 'Chưa xác định'.

Mã biên bản: {_row(record, 'code', '')}
Ngày họp: {_row(record, 'issue_date', '')}
Tên cuộc họp: {_row(record, 'subject', '')}
Đơn vị/chủ trì: {_row(record, 'contractor', '')}
Người lập: {_row(record, 'issuer', '')}
Thành phần tham dự: {_row(record, 'assignee', '')}

NỘI DUNG BIÊN BẢN:
{_row(record, 'description', '')}

KẾT LUẬN / CÔNG VIỆC SAU HỌP:
{_row(record, 'response', '')}

GHI CHÚ:
{_row(record, 'note', '')}
"""


def render_meeting_minutes_simple(st, app_globals: dict[str, Any], pid: int) -> None:
    db = app_globals.get("db")
    if db is None:
        st.error("Không truy cập được cơ sở dữ liệu Biên bản họp.")
        return

    can_update_fn = app_globals.get("_can_update")
    is_admin_fn = app_globals.get("_is_admin")
    can_update = bool(can_update_fn()) if callable(can_update_fn) else False
    is_admin = bool(is_admin_fn()) if callable(is_admin_fn) else False

    rows = list(db.documents(pid, DOC_TYPE) or [])
    try:
        rows = _normalize_existing_rows(db, pid, rows)
    except Exception:
        pass

    st.caption("Biên bản họp chỉ dùng để lưu trữ và rà soát nội dung khi cần. Không có trạng thái, luồng duyệt hay theo dõi hạn.")
    st.metric("Số biên bản đã lưu", len(rows))

    options = [None] + [int(_row(r, "id", 0)) for r in rows]
    select_key = f"meeting_minutes_select_{pid}"
    selected = st.selectbox(
        "Chọn biên bản để xem / chỉnh sửa",
        options,
        format_func=lambda value: "➕ Thêm biên bản mới" if value is None else f"#{value} - {next(str(_row(r, 'code', '')) for r in rows if int(_row(r, 'id', 0)) == value)} · {next(str(_row(r, 'subject', '')) for r in rows if int(_row(r, 'id', 0)) == value)}",
        key=select_key,
    )
    record = db.document(selected) if selected else None

    with st.expander("📝 Thêm / sửa biên bản họp", expanded=(selected is None)):
        with st.form(f"meeting_minutes_form_{pid}_{selected or 'new'}"):
            c1, c2, c3 = st.columns([1.0, 2.2, 1.15])
            code = c1.text_input("Mã biên bản", value=str(_row(record, "code", "")) if record else _next_code(rows))
            subject = c2.text_input("Tên cuộc họp / Nội dung chính *", value=str(_row(record, "subject", "")))
            meeting_date = c3.date_input("Ngày họp", value=_date_value(_row(record, "issue_date", "")) if record else date.today())

            c1, c2 = st.columns(2)
            contractor = c1.text_input("Đơn vị / Chủ trì", value=str(_row(record, "contractor", "")))
            issuer = c2.text_input("Người lập biên bản", value=str(_row(record, "issuer", "")))

            attendees = st.text_area("Thành phần tham dự", value=str(_row(record, "assignee", "")), height=90)
            description = st.text_area("Nội dung biên bản *", value=str(_row(record, "description", "")), height=260)
            response = st.text_area("Kết luận / Công việc sau họp", value=str(_row(record, "response", "")), height=180)
            note = st.text_area("Ghi chú", value=str(_row(record, "note", "")), height=90)

            attach_clicked = st.form_submit_button(
                "📎 Đính kèm file",
                disabled=not can_update,
                width="stretch",
            )
            if attach_clicked:
                if not subject.strip() or not description.strip():
                    st.error("Tên cuộc họp và Nội dung biên bản là bắt buộc.")
                else:
                    try:
                        effective_code = code.strip() or _next_code(rows)
                        saved_id = db.save_document(
                            pid,
                            DOC_TYPE,
                            {
                                "code": effective_code,
                                "subject": subject.strip(),
                                "discipline": "",
                                "contractor": contractor.strip(),
                                "issuer": issuer.strip(),
                                "assignee": attendees.strip(),
                                "issue_date": meeting_date.isoformat(),
                                "due_date": "",
                                "closed_date": "",
                                "status": "",
                                "priority": "",
                                "related_wbs": "",
                                "description": description.strip(),
                                "response": response.strip(),
                                "note": note.strip(),
                                "cost_impact": 0,
                                "time_impact_days": 0,
                            },
                            selected,
                        )
                        st.session_state[select_key] = int(saved_id)

                        upload_fn = app_globals.get("_prepare_inline_upload_ticket")
                        if not callable(upload_fn):
                            st.error("Chức năng đính kèm file chưa sẵn sàng.")
                        else:
                            panel_key = f"meeting_minutes_files_{pid}_{saved_id}"
                            st.session_state.pop(panel_key + "_ticket", None)
                            st.session_state.pop(panel_key + "_upload_open", None)
                            upload_fn(
                                pid,
                                kind="document",
                                subtype=DOC_TYPE,
                                record_code=effective_code,
                                panel_key=panel_key,
                            )
                            st.rerun()
                    except Exception as exc:
                        st.error(f"Chưa mở được vùng đính kèm file: {exc}")

    if selected:
        current = db.document(selected)
        if current:
            render_files_fn = app_globals.get("_render_inline_drive_attachments")
            panel_key = f"meeting_minutes_files_{pid}_{selected}"
            if callable(render_files_fn):
                render_files_fn(
                    pid,
                    kind="document",
                    subtype=DOC_TYPE,
                    record_code=str(_row(current, "code", "")),
                    record_id=int(selected),
                    panel_key=panel_key,
                )

            ai_key = f"meeting_minutes_ai_review_{pid}_{selected}"
            if st.button("🤖 Rà soát nội dung bằng AI", key=f"meeting_minutes_ai_btn_{pid}_{selected}", type="primary"):
                try:
                    ai, provider_name = _build_ai(app_globals)
                    with st.spinner(f"{provider_name} đang rà soát biên bản..."):
                        answer = ai.ask_project(int(pid), _review_prompt(current), history=[], status_date=date.today(), use_web=False)
                    st.session_state[ai_key] = str(answer or "").strip()
                except Exception as exc:
                    st.error(f"Không rà soát được bằng AI: {exc}")
            if st.session_state.get(ai_key):
                st.markdown("#### Kết quả rà soát AI")
                st.markdown(st.session_state[ai_key])

            if is_admin and st.button("🗑 Xóa biên bản này", key=f"meeting_minutes_delete_{pid}_{selected}"):
                try:
                    trash_fn = app_globals.get("_trash_record_drive_files")
                    if callable(trash_fn):
                        _, errors = trash_fn(pid, kind="document", subtype=DOC_TYPE, record_code=str(_row(current, "code", "")))
                        if errors:
                            st.error("Chưa xóa được file trên kho lưu trữ: " + " | ".join(errors))
                            return
                    db.delete_document(int(selected))
                    st.session_state.pop(select_key, None)
                    st.session_state.pop(ai_key, None)
                    st.success("Đã xóa biên bản họp.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Không xóa được biên bản: {exc}")

    if rows:
        st.markdown("#### Danh sách biên bản đã lưu")
        table = []
        for item in rows:
            table.append({
                "Mã": _row(item, "code", ""),
                "Ngày họp": _row(item, "issue_date", ""),
                "Cuộc họp": _row(item, "subject", ""),
                "Đơn vị/Chủ trì": _row(item, "contractor", ""),
                "Người lập": _row(item, "issuer", ""),
                "Thành phần": _row(item, "assignee", ""),
                "Kết luận / Công việc": _row(item, "response", ""),
            })
        st.dataframe(table, hide_index=True, width="stretch")


def _patch_app_renderer(st, app_globals: dict[str, Any]) -> None:
    if app_globals.get(_PATCH_FLAG):
        return
    original = app_globals.get("render_document_type")
    if not callable(original):
        return

    def _render_document_type(pid: int, doc_type: str):
        if str(doc_type) == DOC_TYPE:
            return render_meeting_minutes_simple(st, app_globals, int(pid))
        return original(pid, doc_type)

    app_globals["render_document_type"] = _render_document_type
    app_globals[_PATCH_FLAG] = True


def install_meeting_minutes_simple_ui() -> None:
    """Install a status-free meeting-minutes renderer without changing other sheets."""
    import streamlit as st

    if getattr(st, "_qlda_meeting_minutes_simple_ui_installed", False):
        return

    original_segmented = st.segmented_control

    def _segmented_control(label, options, *args, **kwargs):
        result = original_segmented(label, options, *args, **kwargs)
        if str(label or "").strip() == "Loại hồ sơ":
            app_globals = _find_app_globals()
            if app_globals is not None:
                cfg = app_globals.get("DOC_CONFIG")
                if isinstance(cfg, dict) and DOC_TYPE in cfg:
                    cfg[DOC_TYPE].update({
                        "title": DOC_LABEL,
                        "statuses": [],
                        "done_statuses": [""],
                        "subject": "Tên cuộc họp / Nội dung chính",
                        "code_label": "Mã biên bản họp",
                        "issuer_label": "Người lập biên bản",
                        "assignee_label": "Thành phần tham dự",
                        "issue_date_label": "Ngày họp",
                        "response_label": "Kết luận / Công việc sau họp",
                    })
                _patch_app_renderer(st, app_globals)
        return result

    st._qlda_meeting_minutes_simple_original_segmented_control = original_segmented
    st.segmented_control = _segmented_control
    st._qlda_meeting_minutes_simple_ui_installed = True
