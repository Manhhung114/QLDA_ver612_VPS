from __future__ import annotations

from datetime import date, datetime
from typing import Any


PATCH_VERSION = "V6.22 IPC CLAIM PERIOD V1"


def _clean_date_text(value: Any) -> str:
    """Return ISO yyyy-mm-dd, or empty for placeholders such as Excel '0'."""
    if value is None or isinstance(value, bool):
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    text = str(value).strip()
    if text.lower() in {"", "0", "0.0", "none", "nan", "null", "00/00/0000", "00-00-0000"}:
        return ""
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
        except Exception:
            pass
    return ""


def _date_after_label(ws, wanted: str) -> str:
    """Find a date next to a label such as TỪ NGÀY / ĐẾN NGÀY in small declaration sheets."""
    if ws is None:
        return ""
    import ipc_claim_v622 as ipc

    wanted_norm = ipc._norm(wanted)
    max_row = min(int(ws.max_row or 0), 45)
    max_col = min(int(ws.max_column or 0), 12)
    if max_row <= 0 or max_col <= 0:
        return ""
    for values in ws.iter_rows(min_row=1, max_row=max_row, min_col=1, max_col=max_col, values_only=True):
        vals = tuple(values)
        for idx, value in enumerate(vals):
            label = ipc._norm(value)
            if not label or wanted_norm not in label:
                continue
            for candidate in vals[idx + 1 : min(len(vals), idx + 5)]:
                parsed = _clean_date_text(candidate)
                if parsed:
                    return parsed
    return ""


def install_ipc_claim_period_parser_fix() -> None:
    """Normalize Claim dates and read them by labels instead of preserving Excel placeholder 0."""
    import ipc_claim_v622 as ipc

    if getattr(ipc, "_qlda_ipc_period_fix_installed", False):
        return

    original_date_text = ipc._date_text
    original_metadata = ipc._metadata_from_declaration

    def normalized_date_text(value: Any) -> str:
        clean = _clean_date_text(value)
        if clean:
            return clean
        try:
            legacy = original_date_text(value)
        except Exception:
            legacy = ""
        return _clean_date_text(legacy)

    def metadata_from_declaration(ws) -> dict[str, Any]:
        data = dict(original_metadata(ws) or {})
        from_date = _clean_date_text(data.get("from_date")) or _date_after_label(ws, "TỪ NGÀY")
        to_date = _clean_date_text(data.get("to_date")) or _date_after_label(ws, "ĐẾN NGÀY")
        data["from_date"] = from_date
        data["to_date"] = to_date
        return data

    ipc._date_text = normalized_date_text
    ipc._metadata_from_declaration = metadata_from_declaration
    ipc._qlda_ipc_period_fix_installed = True
    ipc._qlda_ipc_period_fix_marker = PATCH_VERSION


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


def update_claim_period(db, claim_id: str, from_date: str, to_date: str) -> None:
    import ipc_claim_v622 as ipc

    start = _clean_date_text(from_date)
    end = _clean_date_text(to_date)
    if not start or not end:
        raise ValueError("Từ ngày và Đến ngày phải là ngày hợp lệ (dd/mm/yyyy hoặc yyyy-mm-dd).")
    if start > end:
        raise ValueError("Từ ngày không được lớn hơn Đến ngày.")

    now = ipc._now()
    with db.connect() as connection:
        ipc._ensure_tables(connection)
        row = connection.execute(
            f"SELECT * FROM {ipc.CLAIMS_TABLE} WHERE claim_id=?",
            (str(claim_id),),
        ).fetchone()
        if row is None:
            raise ValueError("Không tìm thấy Claim cần cập nhật.")
        connection.execute(
            f"UPDATE {ipc.CLAIMS_TABLE} SET from_date=?,to_date=?,updated_at=? WHERE claim_id=?",
            (start, end, now, str(claim_id)),
        )
        updated = connection.execute(
            f"SELECT * FROM {ipc.CLAIMS_TABLE} WHERE claim_id=?",
            (str(claim_id),),
        ).fetchone()
        if updated is not None:
            ipc._sync_payment_tracking(connection, _rowdict(updated))


def sync_claim_periods_from_saved_workbooks(db, project_id: int) -> dict[str, Any]:
    import ipc_claim_v622 as ipc

    updated: list[str] = []
    missing: list[str] = []
    unchanged: list[str] = []
    for claim in ipc.list_ipc_claims(db, int(project_id)):
        claim_id = str(claim.get("claim_id") or "")
        code = str(claim.get("claim_code") or claim.get("claim_no") or claim_id)
        workbook = ipc.load_ipc_workbook(db, claim_id)
        meta = dict((workbook or {}).get("metadata") or {})
        start = _clean_date_text(meta.get("from_date"))
        end = _clean_date_text(meta.get("to_date"))
        if not start or not end:
            missing.append(code)
            continue
        old_start = _clean_date_text(claim.get("from_date"))
        old_end = _clean_date_text(claim.get("to_date"))
        if old_start == start and old_end == end:
            unchanged.append(code)
            continue
        update_claim_period(db, claim_id, start, end)
        updated.append(code)
    return {"updated": updated, "missing": missing, "unchanged": unchanged}


def render_ipc_claim_period_ui(db, project_id: int, *, can_update: bool = True) -> None:
    import pandas as pd
    import streamlit as st
    import ipc_claim_v622 as ipc

    claims = ipc.list_ipc_claims(db, int(project_id))
    if not claims:
        return

    missing_claims = [
        c for c in claims
        if not _clean_date_text(c.get("from_date")) or not _clean_date_text(c.get("to_date"))
    ]

    with st.expander("📅 Kỳ thanh toán / ngày Claim", expanded=bool(missing_claims)):
        st.caption(
            "Ngày được ưu tiên đọc từ file Excel IPC (nhãn TỪ NGÀY / ĐẾN NGÀY). "
            "Giá trị 0 trong Excel được xem là chưa khai báo, không còn hiển thị thành ngày '0'."
        )
        table = pd.DataFrame([
            {
                "Claim": c.get("claim_code") or "",
                "Từ ngày": _clean_date_text(c.get("from_date")),
                "Đến ngày": _clean_date_text(c.get("to_date")),
                "File Excel": c.get("filename") or "",
            }
            for c in claims
        ])
        st.dataframe(table, hide_index=True, width="stretch")

        if st.button(
            "🔄 Đồng bộ ngày từ các file Excel Claim đã lưu",
            disabled=not bool(can_update),
            key=f"ipc_period_sync_{int(project_id)}",
            use_container_width=True,
        ):
            try:
                result = sync_claim_periods_from_saved_workbooks(db, int(project_id))
                if result["updated"]:
                    st.success("Đã cập nhật ngày từ Excel: " + ", ".join(result["updated"]))
                if result["missing"]:
                    st.warning(
                        "Các file Excel sau chưa có ngày hợp lệ (TỪ NGÀY / ĐẾN NGÀY đang trống hoặc bằng 0): "
                        + ", ".join(result["missing"])
                        + ". Hãy nhập ngày thủ công bên dưới hoặc cập nhật lại file Excel."
                    )
                if not result["updated"] and not result["missing"]:
                    st.info("Ngày của các Claim đã khớp với file Excel đã lưu.")
                st.rerun()
            except Exception as exc:
                st.error(f"Không thể đồng bộ ngày Claim: {exc}")

        labels = [str(c.get("claim_code") or c.get("claim_no") or "Claim") for c in claims]
        selected = st.selectbox(
            "Claim cần bổ sung / điều chỉnh ngày",
            labels,
            key=f"ipc_period_select_{int(project_id)}",
        )
        claim = claims[labels.index(selected)]
        current_from = _clean_date_text(claim.get("from_date"))
        current_to = _clean_date_text(claim.get("to_date"))
        workbook = ipc.load_ipc_workbook(db, str(claim.get("claim_id") or ""))
        meta = dict((workbook or {}).get("metadata") or {})
        excel_from = _clean_date_text(meta.get("from_date"))
        excel_to = _clean_date_text(meta.get("to_date"))

        if excel_from and excel_to:
            st.info(f"File Excel đã lưu: {excel_from} → {excel_to}")
        else:
            st.warning(
                "File Excel hiện tại không có kỳ ngày hợp lệ. Nếu ô TỪ NGÀY / ĐẾN NGÀY trong file đang bằng 0, "
                "hệ thống không tự suy đoán ngày để tránh sai dữ liệu."
            )

        col1, col2 = st.columns(2)
        with col1:
            start_text = st.text_input(
                "Từ ngày",
                value=current_from,
                placeholder="dd/mm/yyyy hoặc yyyy-mm-dd",
                key=f"ipc_period_from_{claim.get('claim_id')}",
            )
        with col2:
            end_text = st.text_input(
                "Đến ngày",
                value=current_to,
                placeholder="dd/mm/yyyy hoặc yyyy-mm-dd",
                key=f"ipc_period_to_{claim.get('claim_id')}",
            )

        if st.button(
            "💾 Lưu kỳ Claim",
            type="primary",
            disabled=not bool(can_update),
            key=f"ipc_period_save_{claim.get('claim_id')}",
            use_container_width=True,
        ):
            try:
                update_claim_period(db, str(claim.get("claim_id") or ""), start_text, end_text)
                st.success(f"Đã cập nhật ngày cho {selected}.")
                st.rerun()
            except Exception as exc:
                st.error(f"Không thể cập nhật ngày Claim: {exc}")


# Install parser normalization on import so generated Streamlit source benefits immediately.
install_ipc_claim_period_parser_fix()
