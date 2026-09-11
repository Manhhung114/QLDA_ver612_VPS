from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import contract_management_v622 as cm


PATCH_MARKER = "V6.22 CONTRACT DURATION AUTO EXPIRY"

_ORIGINAL_ENSURE_SCHEMA_CONNECTION = cm.ensure_schema_connection
_ORIGINAL_CREATE_CONTRACT_RECORD = cm.create_contract_record
_ORIGINAL_UPDATE_CONTRACT_RECORD = cm.update_contract_record
_INSTALLED = False


def _duration_days(value: Any) -> int:
    try:
        return max(0, int(round(float(value or 0))))
    except Exception:
        return 0


def _date_value(value: Any) -> date | None:
    if value in {None, ""}:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = cm._text(value)
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except Exception:
            pass
    return None


def _calculate_expiry_date(effective_date: Any, duration_days: Any) -> str:
    start = _date_value(effective_date)
    duration = _duration_days(duration_days)
    if start is None or duration <= 0:
        return ""
    return (start + timedelta(days=duration)).isoformat()


def _existing_duration(effective_date: Any, expiry_date: Any) -> int:
    start = _date_value(effective_date)
    finish = _date_value(expiry_date)
    if start is None or finish is None or finish < start:
        return 0
    return max(0, (finish - start).days)


def _row_value(row: Any, key: str, index: int, default: Any = None) -> Any:
    try:
        return row[key]
    except Exception:
        try:
            return row[index]
        except Exception:
            return default


def _has_duration_column(connection) -> bool:
    # PostgreSQL uses the compatibility connection installed by postgres_backend_v622.
    # Avoid intentionally failing SQL on PostgreSQL because that would abort its
    # transaction. SQLite is detected separately and can use PRAGMA table_info.
    if hasattr(connection, "_raw"):
        row = connection.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name=? AND column_name=? LIMIT 1",
            (cm.RECORDS_TABLE, "duration_days"),
        ).fetchone()
        return bool(row)

    rows = connection.execute(f"PRAGMA table_info({cm.RECORDS_TABLE})").fetchall()
    for row in rows:
        name = cm._text(_row_value(row, "name", 1, ""))
        if name == "duration_days":
            return True
    return False


def _backfill_duration_days(connection) -> None:
    rows = connection.execute(
        f"SELECT id,effective_date,expiry_date,duration_days FROM {cm.RECORDS_TABLE}"
    ).fetchall()
    for row in rows:
        current = _duration_days(_row_value(row, "duration_days", 3, 0))
        if current > 0:
            continue
        duration = _existing_duration(
            _row_value(row, "effective_date", 1, ""),
            _row_value(row, "expiry_date", 2, ""),
        )
        if duration > 0:
            connection.execute(
                f"UPDATE {cm.RECORDS_TABLE} SET duration_days=? WHERE id=?",
                (duration, int(_row_value(row, "id", 0, 0) or 0)),
            )


def ensure_schema_connection(connection) -> None:
    _ORIGINAL_ENSURE_SCHEMA_CONNECTION(connection)
    if not _has_duration_column(connection):
        connection.execute(
            f"ALTER TABLE {cm.RECORDS_TABLE} "
            "ADD COLUMN duration_days INTEGER NOT NULL DEFAULT 0"
        )
    _backfill_duration_days(connection)


def _save_duration(db, record_id: int, workspace_project_id: int, duration_days: Any) -> None:
    with db.connect() as connection:
        connection.execute(
            f"UPDATE {cm.RECORDS_TABLE} SET duration_days=? "
            "WHERE id=? AND workspace_project_id=?",
            (_duration_days(duration_days), int(record_id), int(workspace_project_id)),
        )


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
    duration_days: Any = None,
    note: str = "",
    actor: Any = None,
) -> dict[str, Any]:
    cm.ensure_schema(db)

    # Backward compatibility: callers that do not yet pass duration_days keep
    # their old expiry date and the duration is inferred from the two dates.
    if duration_days is None:
        duration = _existing_duration(effective_date, expiry_date)
        calculated_expiry = cm._date_text(expiry_date)
    else:
        duration = _duration_days(duration_days)
        calculated_expiry = _calculate_expiry_date(effective_date, duration)

    record = _ORIGINAL_CREATE_CONTRACT_RECORD(
        db,
        workspace_project_id=int(workspace_project_id),
        record_type=record_type,
        record_no=record_no,
        title=title,
        signed_date=signed_date,
        amount=amount,
        currency=currency,
        effective_date=effective_date,
        expiry_date=calculated_expiry,
        note=note,
        actor=actor,
    )
    _save_duration(db, int(record["id"]), int(workspace_project_id), duration)
    return cm.get_contract_record(db, int(record["id"]), workspace_project_id=int(workspace_project_id))


def update_contract_record(
    db,
    record_id: int,
    *,
    workspace_project_id: int,
    actor: Any = None,
    **values: Any,
) -> dict[str, Any]:
    cm.ensure_schema(db)
    current = cm.get_contract_record(db, int(record_id), workspace_project_id=int(workspace_project_id))
    if not current:
        raise ValueError("Không tìm thấy hồ sơ hợp đồng trong workspace hiện tại.")

    effective_date = values.get("effective_date", current.get("effective_date"))
    if "duration_days" in values:
        duration = _duration_days(values.pop("duration_days"))
        values["expiry_date"] = _calculate_expiry_date(effective_date, duration)
    else:
        duration = _duration_days(current.get("duration_days"))
        # Preserve old/manual records when no duration was supplied by a legacy caller.
        if duration > 0:
            values["expiry_date"] = _calculate_expiry_date(effective_date, duration)

    updated = _ORIGINAL_UPDATE_CONTRACT_RECORD(
        db,
        int(record_id),
        workspace_project_id=int(workspace_project_id),
        actor=actor,
        **values,
    )
    _save_duration(db, int(record_id), int(workspace_project_id), duration)
    return cm.get_contract_record(db, int(record_id), workspace_project_id=int(workspace_project_id))


def _record_metadata_lines(records: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for row in records:
        duration = _duration_days(row.get("duration_days"))
        duration_text = f"{duration} ngày" if duration > 0 else "chưa nhập"
        lines.append(
            f"[HĐ:{row.get('id')}|{cm._text(row.get('record_no'))}|{cm._text(row.get('current_file_name')) or 'không-file'}] "
            f"loại={cm._text(row.get('record_type'))}; tiêu đề={cm._text(row.get('title'))}; "
            f"ngày ký={cm._text(row.get('signed_date'))}; giá trị={cm._amount(row.get('amount'))} {cm._text(row.get('currency')) or 'VND'}; "
            f"hiệu lực={cm._text(row.get('effective_date'))}; thời gian thi công={duration_text}; "
            f"hết hiệu lực={cm._text(row.get('expiry_date'))}; ghi chú={cm._text(row.get('note'))}"
        )
    return lines


def _duration_label(value: Any) -> str:
    duration = _duration_days(value)
    return f"{duration} ngày" if duration > 0 else "—"


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
    if not cm.can_access_contract_management(identity):
        st.warning("📑 Quản lý hợp đồng chỉ dành cho Admin, Ban điều hành và Ban quản lý dự án.")
        return

    cm.ensure_schema(db)
    user = cm._identity(identity)
    can_edit = cm.can_edit_contract_management(identity, is_admin=is_admin)
    rows = cm.list_contract_records(db, int(workspace_project_id))
    summary = cm.contract_summary(db, int(workspace_project_id))

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
                    answer = cm.ask_contract_ai(db, gateway, session_token, workspace_project_id=int(workspace_project_id), question=question)
                history.append({"q": question, "a": answer, "at": cm._now()})
                st.session_state[ai_key] = history[-6:]
            except Exception as exc:
                st.error(str(exc))
        for item in reversed(list(st.session_state.get(ai_key) or [])[-3:]):
            st.markdown(f"**Hỏi:** {cm._text(item.get('q'))}")
            st.markdown(cm._text(item.get("a")))
            st.divider()

    if can_edit:
        with st.expander("➕ Thêm hợp đồng / phụ lục", expanded=not bool(rows)):
            st.caption("Ngày hết hiệu lực do APP tự tính = Ngày hiệu lực + Thời gian thi công theo hợp đồng.")
            with st.form(f"contract_add_{workspace_project_id}", clear_on_submit=True):
                a1, a2 = st.columns(2)
                record_type = a1.selectbox("Loại hồ sơ", cm.RECORD_TYPES)
                record_no = a2.text_input("Số hợp đồng / phụ lục *")
                title = st.text_input("Tên / nội dung")
                b1, b2, b3, b4 = st.columns(4)
                signed_date = b1.date_input("Ngày ký", value=None)
                effective_date = b2.date_input("Ngày hiệu lực", value=None)
                duration_days = b3.number_input(
                    "Thời gian thi công (ngày)",
                    min_value=0,
                    value=0,
                    step=1,
                    help="Nhập tay đúng số ngày thi công ghi trong hợp đồng/phụ lục.",
                )
                computed_expiry = _calculate_expiry_date(effective_date, duration_days)
                b4.text_input(
                    "Ngày hết hiệu lực (tự tính)",
                    value=cm._format_date(computed_expiry) if computed_expiry else "—",
                    disabled=True,
                )
                c1, c2 = st.columns([2, 1])
                amount = c1.number_input("Giá trị", min_value=0.0, value=0.0, step=1000000.0, format="%.0f")
                currency = c2.selectbox("Tiền tệ", ["VND", "USD", "EUR"])
                note = st.text_area("Ghi chú")
                upload = st.file_uploader("File hợp đồng / phụ lục", type=["pdf", "doc", "docx", "xls", "xlsx", "txt", "csv"], key=f"contract_add_file_{workspace_project_id}")
                submitted = st.form_submit_button("💾 Lưu hồ sơ", type="primary")
            if submitted:
                try:
                    record = cm.create_contract_record(
                        db,
                        workspace_project_id=int(workspace_project_id),
                        record_type=record_type,
                        record_no=record_no,
                        title=title,
                        signed_date=signed_date,
                        amount=amount,
                        currency=currency,
                        effective_date=effective_date,
                        duration_days=duration_days,
                        note=note,
                        actor=user,
                    )
                    if upload is not None:
                        cm._upload_record_file(st, db, gateway, session_token, int(workspace_project_id), record, upload, user)
                    st.success("Đã lưu hồ sơ hợp đồng và tự tính ngày hết hiệu lực.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Không lưu được hồ sơ: {exc}")
    else:
        st.info("Tài khoản hiện có quyền xem Quản lý hợp đồng nhưng không có quyền cập nhật dữ liệu.")

    rows = cm.list_contract_records(db, int(workspace_project_id))
    search = st.text_input("🔍 Tìm hợp đồng / phụ lục", key=f"contract_search_{workspace_project_id}").strip().lower()
    type_filter = st.selectbox("Loại hồ sơ", ["Tất cả", *cm.RECORD_TYPES], key=f"contract_type_filter_{workspace_project_id}")
    filtered = []
    for row in rows:
        if type_filter != "Tất cả" and cm._text(row.get("record_type")) != type_filter:
            continue
        haystack = " ".join(cm._text(row.get(k)) for k in ("record_type", "record_no", "title", "note", "current_file_name", "effective_date", "expiry_date", "duration_days")).lower()
        if search and search not in haystack:
            continue
        filtered.append(row)

    table_rows = [
        {
            "Loại": cm._text(r.get("record_type")),
            "Số hồ sơ": cm._text(r.get("record_no")),
            "Tên / nội dung": cm._text(r.get("title")),
            "Ngày ký": cm._format_date(r.get("signed_date")),
            "Ngày hiệu lực": cm._format_date(r.get("effective_date")),
            "Thời gian thi công": _duration_label(r.get("duration_days")),
            "Ngày hết hiệu lực": cm._format_date(r.get("expiry_date")),
            "Giá trị": cm._format_amount(r.get("amount"), r.get("currency")),
            "File": cm._text(r.get("current_file_name")) or "—",
            "Cập nhật": cm._text(r.get("updated_at")),
        }
        for r in filtered
    ]
    st.dataframe(table_rows, use_container_width=True, hide_index=True)
    if not filtered:
        st.info("Chưa có hồ sơ phù hợp.")
        return

    labels = {
        int(r["id"]): f"{cm._text(r.get('record_type'))} · {cm._text(r.get('record_no'))} · {cm._text(r.get('title')) or 'Không tiêu đề'}"
        for r in filtered
    }
    selected_id = st.selectbox(
        "Mở hồ sơ",
        list(labels),
        format_func=lambda x: labels.get(int(x), str(x)),
        key=f"contract_selected_{workspace_project_id}",
    )
    record = cm.get_contract_record(db, int(selected_id), workspace_project_id=int(workspace_project_id))
    if not record:
        return

    st.markdown(f"### {cm._text(record.get('record_type'))} · {cm._text(record.get('record_no'))}")
    d1, d2, d3, d4 = st.columns(4)
    d1.write(f"**Ngày ký:** {cm._format_date(record.get('signed_date'))}")
    d2.write(f"**Hiệu lực:** {cm._format_date(record.get('effective_date'))}")
    d3.write(f"**Thời gian thi công:** {_duration_label(record.get('duration_days'))}")
    d4.write(f"**Hết hiệu lực:** {cm._format_date(record.get('expiry_date'))}")
    st.write(f"**Tên / nội dung:** {cm._text(record.get('title')) or '—'}")
    st.write(f"**Giá trị:** {cm._format_amount(record.get('amount'), record.get('currency'))}")
    if cm._text(record.get("note")):
        st.write(f"**Ghi chú:** {cm._text(record.get('note'))}")

    st.markdown("#### 📎 File hiện tại")
    if cm._text(record.get("current_file_id")):
        st.write(f"**{cm._text(record.get('current_file_name'))}**")
        cm._render_file_links(st, gateway, session_token, cm._text(record.get("current_file_id")), prefix=f"contract_{record['id']}")
    else:
        st.info("Hồ sơ này chưa có file đính kèm.")

    versions = cm.list_contract_file_versions(db, int(record["id"]), workspace_project_id=int(workspace_project_id))
    if versions:
        with st.expander(f"🕘 Lịch sử file ({len(versions)} phiên bản)", expanded=False):
            for version in versions:
                current_mark = " · hiện tại" if int(version.get("is_current") or 0) else ""
                st.write(f"**V{int(version.get('version_no') or 0)}{current_mark}** — {cm._text(version.get('file_name'))} — {cm._text(version.get('uploaded_at'))}")
                cm._render_file_links(st, gateway, session_token, cm._text(version.get("file_id")), prefix=f"contract_ver_{version['id']}")

    if can_edit:
        with st.expander("✏️ Cập nhật hồ sơ / thay file", expanded=False):
            st.caption("Khi đổi Ngày hiệu lực hoặc Thời gian thi công, APP sẽ tự tính lại Ngày hết hiệu lực.")
            with st.form(f"contract_edit_{record['id']}"):
                e1, e2 = st.columns(2)
                current_type = cm._text(record.get("record_type"))
                edit_type = e1.selectbox("Loại hồ sơ", cm.RECORD_TYPES, index=cm.RECORD_TYPES.index(current_type) if current_type in cm.RECORD_TYPES else 0)
                edit_no = e2.text_input("Số hợp đồng / phụ lục *", value=cm._text(record.get("record_no")))
                edit_title = st.text_input("Tên / nội dung", value=cm._text(record.get("title")))
                f1, f2, f3, f4 = st.columns(4)
                edit_signed = f1.date_input("Ngày ký", value=cm._date_input_value(record.get("signed_date")))
                edit_effective = f2.date_input("Ngày hiệu lực", value=cm._date_input_value(record.get("effective_date")))
                edit_duration = f3.number_input(
                    "Thời gian thi công (ngày)",
                    min_value=0,
                    value=_duration_days(record.get("duration_days")),
                    step=1,
                    help="Nhập tay đúng số ngày thi công ghi trong hợp đồng/phụ lục.",
                )
                edit_expiry = _calculate_expiry_date(edit_effective, edit_duration)
                f4.text_input(
                    "Ngày hết hiệu lực (tự tính)",
                    value=cm._format_date(edit_expiry) if edit_expiry else "—",
                    disabled=True,
                )
                g1, g2 = st.columns([2, 1])
                edit_amount = g1.number_input("Giá trị", min_value=0.0, value=cm._amount(record.get("amount")), step=1000000.0, format="%.0f")
                currencies = ["VND", "USD", "EUR"]
                current_currency = cm._text(record.get("currency")) or "VND"
                edit_currency = g2.selectbox("Tiền tệ", currencies, index=currencies.index(current_currency) if current_currency in currencies else 0)
                edit_note = st.text_area("Ghi chú", value=cm._text(record.get("note")))
                replacement = st.file_uploader("🔄 File mới (nếu cần thay/cập nhật)", type=["pdf", "doc", "docx", "xls", "xlsx", "txt", "csv"], key=f"contract_replace_{record['id']}")
                saved = st.form_submit_button("💾 Cập nhật", type="primary")
            if saved:
                try:
                    updated = cm.update_contract_record(
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
                        duration_days=edit_duration,
                        note=edit_note,
                    )
                    if replacement is not None:
                        cm._upload_record_file(st, db, gateway, session_token, int(workspace_project_id), updated, replacement, user)
                    st.success("Đã cập nhật hồ sơ và tự tính lại ngày hết hiệu lực.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Không cập nhật được hồ sơ: {exc}")

    if is_admin:
        with st.expander("🗑 Xóa hồ sơ · Admin", expanded=False):
            confirm = st.checkbox("Tôi xác nhận xóa hồ sơ này và đưa toàn bộ file của hồ sơ vào thùng rác VPS.", key=f"contract_delete_confirm_{record['id']}")
            typed = st.text_input("Nhập lại số hồ sơ để xác nhận", key=f"contract_delete_code_{record['id']}")
            if st.button("🗑 Xóa hồ sơ", key=f"contract_delete_{record['id']}", disabled=not (confirm and typed.strip() == cm._text(record.get("record_no")))):
                try:
                    file_ids = [x.get("file_id") for x in versions if cm._text(x.get("file_id"))]
                    if gateway is not None and session_token:
                        for file_id in file_ids:
                            gateway.trash_file(session_token, cm._text(file_id))
                    cm.delete_contract_record(db, int(record["id"]), workspace_project_id=int(workspace_project_id))
                    st.success("Đã xóa hồ sơ hợp đồng.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Không xóa được hồ sơ: {exc}")


def install_contract_duration_v622() -> None:
    global _INSTALLED
    if _INSTALLED or getattr(cm, "_V622_CONTRACT_DURATION_INSTALLED", False):
        return

    cm.ensure_schema_connection = ensure_schema_connection
    cm.create_contract_record = create_contract_record
    cm.update_contract_record = update_contract_record
    cm._record_metadata_lines = _record_metadata_lines
    cm.render_contract_management_v622 = render_contract_management_v622
    cm.PATCH_MARKER = f"{cm.PATCH_MARKER} + {PATCH_MARKER}"
    cm._V622_CONTRACT_DURATION_INSTALLED = True
    _INSTALLED = True
