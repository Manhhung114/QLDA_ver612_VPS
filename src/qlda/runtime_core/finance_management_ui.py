from __future__ import annotations

"""Giao diện Quản lý Tài chính.

Dự trù dòng tiền chỉ phản ánh các IPC đã tồn tại và còn số dư chưa thanh toán.
Không dùng dự báo BOQ/tiến độ, xác suất, kịch bản hay mô phỏng tương lai.
"""

from datetime import date, datetime
from functools import wraps
import inspect
import re
import unicodedata
from typing import Any

from qlda.runtime_core.finance_common import (
    date_text as _date_text,
    parse_date as _parse_date,
    scope_label as _shared_scope_label,
)

PATCH_MARKER = "V7.6 FINANCE IPC UNPAID CASH PLAN V2"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or 0)
    except Exception:
        return float(default)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value or 0)
    except Exception:
        return int(default)


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


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", _text(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("đ", "d")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _money(value: Any) -> str:
    number = _float(value)
    sign = "-" if number < 0 else ""
    number = abs(number)
    if number >= 1_000_000_000:
        return f"{sign}{number / 1_000_000_000:,.2f} tỷ"
    if number >= 1_000_000:
        return f"{sign}{number / 1_000_000:,.1f} triệu"
    return f"{sign}{number:,.0f} đ"


def _table_exists(connection, table: str) -> bool:
    try:
        connection.execute(f"SELECT 1 FROM {table} LIMIT 1")
        return True
    except Exception:
        return False


def _scope_label(db, pid: int) -> str:
    try:
        return _shared_scope_label(db, int(pid))
    except Exception:
        return "Workspace hiện tại"


def _payment_rows_by_code(connection, project_id: int) -> dict[str, dict[str, Any]]:
    if not _table_exists(connection, "payment_tracking"):
        return {}
    try:
        rows = connection.execute(
            "SELECT * FROM payment_tracking WHERE project_id=? ORDER BY id", (int(project_id),)
        ).fetchall()
    except Exception:
        try:
            rows = connection.execute(
                "SELECT * FROM payment_tracking WHERE project_id=?", (int(project_id),)
            ).fetchall()
        except Exception:
            rows = []
    out: dict[str, dict[str, Any]] = {}
    for raw in rows:
        row = _rowdict(raw)
        code = _text(row.get("payment_code"))
        if code:
            out[code] = row
    return out


def _is_cancelled(status: Any) -> bool:
    q = _norm(status)
    return any(term in q for term in ("huy", "tu choi", "cancelled", "canceled", "rejected"))


def _is_approved(status: Any, approved_amount: float) -> bool:
    if approved_amount > 0:
        return True
    q = _norm(status)
    return any(term in q for term in ("da duyet", "duoc duyet", "chap thuan", "approved", "certified", "cho thanh toan"))


def load_unpaid_ipcs(db, project_id: int) -> list[dict[str, Any]]:
    """Đọc IPC hiện hữu và chỉ giữ các khoản còn số dư phải thanh toán."""
    pid = int(project_id)
    today = date.today()
    rows: list[dict[str, Any]] = []

    with db.connect() as connection:
        if not _table_exists(connection, "payment_claims"):
            return []
        payments = _payment_rows_by_code(connection, pid)
        try:
            claims = connection.execute(
                "SELECT * FROM payment_claims WHERE project_id=? ORDER BY claim_no,updated_at", (pid,)
            ).fetchall()
        except Exception:
            claims = connection.execute(
                "SELECT * FROM payment_claims WHERE project_id=?", (pid,)
            ).fetchall()

        for raw in claims:
            claim = _rowdict(raw)
            code = _text(claim.get("claim_code") or claim.get("claim_no") or claim.get("claim_id"))
            payment = payments.get(code, {})
            status = _text(claim.get("payment_status") or payment.get("payment_status") or "Chưa thanh toán")
            if _is_cancelled(status):
                continue

            requested = max(0.0, _float(claim.get("requested_amount")))
            approved = max(0.0, _float(claim.get("approved_amount")))
            certified = max(0.0, _float(claim.get("certified_cumulative")))
            paid = max(
                0.0,
                _float(claim.get("disbursed_amount")),
                _float(payment.get("paid_amount")),
            )
            target = approved if approved > 0 else requested if requested > 0 else certified
            outstanding = max(0.0, target - paid)
            if outstanding <= 1e-6:
                continue

            due_date = None
            for candidate in (
                claim.get("payment_due_date"),
                payment.get("payment_due_date"),
                payment.get("due_date"),
                claim.get("due_date"),
            ):
                due_date = _parse_date(candidate)
                if due_date:
                    break

            overdue_days = max(0, (today - due_date).days) if due_date and due_date < today else 0
            rows.append({
                "claim_id": _text(claim.get("claim_id") or claim.get("id")),
                "claim_no": _text(claim.get("claim_no")),
                "claim_code": code,
                "contractor": _text(claim.get("contractor")),
                "contract_no": _text(claim.get("contract_no")),
                "package_name": _text(claim.get("package_name")),
                "status": status,
                "requested_amount": requested,
                "approved_amount": approved,
                "certified_amount": certified,
                "paid_amount": paid,
                "outstanding": outstanding,
                "due_date": due_date,
                "overdue_days": overdue_days,
                "approved_waiting": _is_approved(status, approved),
                "retention": max(0.0, _float(claim.get("retention_cumulative"))),
                "advance_recovery": max(0.0, _float(claim.get("advance_recovery"))),
                "deductions": max(0.0, _float(claim.get("current_deductions"))),
                "period_to": _parse_date(claim.get("to_date")),
                "note": _text(claim.get("note") or payment.get("note")),
            })

    rows.sort(key=lambda row: (row.get("due_date") is None, row.get("due_date") or date.max, _text(row.get("claim_code"))))
    return rows


def _render_due_month_summary(st, rows: list[dict[str, Any]]) -> None:
    import pandas as pd

    buckets: dict[str, float] = {}
    no_due = 0.0
    for row in rows:
        due = row.get("due_date")
        value = max(0.0, _float(row.get("outstanding")))
        if isinstance(due, date):
            key = due.strftime("%m/%Y")
            buckets[key] = buckets.get(key, 0.0) + value
        else:
            no_due += value

    if not buckets:
        if rows:
            st.info("Các IPC chưa thanh toán hiện chưa có ngày đến hạn để phân bổ dòng tiền theo tháng.")
        return

    ordered = sorted(
        buckets.items(),
        key=lambda item: datetime.strptime(item[0], "%m/%Y"),
    )
    frame = pd.DataFrame([{"Tháng đến hạn": key, "Cần thanh toán": value} for key, value in ordered])
    st.markdown("#### Dòng tiền theo hạn thanh toán IPC")
    st.bar_chart(frame.set_index("Tháng đến hạn"), use_container_width=True)
    st.dataframe(frame, hide_index=True, use_container_width=True)
    if no_due > 0:
        st.caption(f"IPC chưa có hạn thanh toán: {_money(no_due)}.")


def render_cashflow_finance_sheet(
    st,
    db,
    pid: int,
    *,
    identity: Any = None,
    can_update: bool = False,
    is_admin: bool = False,
) -> None:
    del identity, can_update, is_admin
    import pandas as pd

    pid = int(pid)
    rows = load_unpaid_ipcs(db, pid)
    today = date.today()

    st.markdown("### 💵 Dự trù dòng tiền thanh toán IPC")
    st.caption(
        f"Phạm vi: {_scope_label(db, pid)}. Chỉ tổng hợp các IPC đã tồn tại và còn số dư chưa thanh toán. "
        "Không sử dụng dự báo BOQ, tiến độ, xác suất, kịch bản hoặc mô phỏng."
    )

    total_outstanding = sum(_float(row.get("outstanding")) for row in rows)
    approved_waiting = sum(
        _float(row.get("outstanding")) for row in rows if bool(row.get("approved_waiting"))
    )
    overdue_total = sum(
        _float(row.get("outstanding")) for row in rows if _int(row.get("overdue_days")) > 0
    )
    no_due_total = sum(
        _float(row.get("outstanding")) for row in rows if not isinstance(row.get("due_date"), date)
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("IPC chưa thanh toán", f"{len(rows):,}")
    c2.metric("Còn phải thanh toán", _money(total_outstanding))
    c3.metric("Đã duyệt chờ thanh toán", _money(approved_waiting))
    c4.metric("Quá hạn thanh toán", _money(overdue_total))

    if not rows:
        st.success("Không có IPC nào còn số dư chưa thanh toán trong workspace hiện tại.")
        return

    statuses = sorted({_text(row.get("status")) for row in rows if _text(row.get("status"))})
    contractors = sorted({_text(row.get("contractor")) for row in rows if _text(row.get("contractor"))})
    f1, f2, f3 = st.columns(3)
    status_filter = f1.selectbox(
        "Trạng thái",
        ["Tất cả"] + statuses,
        key=f"ipc_cash_status_{pid}",
    )
    contractor_filter = f2.selectbox(
        "Nhà thầu",
        ["Tất cả"] + contractors,
        key=f"ipc_cash_contractor_{pid}",
    )
    due_filter = f3.selectbox(
        "Hạn thanh toán",
        ["Tất cả", "Quá hạn", "Chưa đến hạn", "Chưa có hạn"],
        key=f"ipc_cash_due_{pid}",
    )

    filtered = []
    for row in rows:
        if status_filter != "Tất cả" and _text(row.get("status")) != status_filter:
            continue
        if contractor_filter != "Tất cả" and _text(row.get("contractor")) != contractor_filter:
            continue
        due = row.get("due_date")
        if due_filter == "Quá hạn" and not (_int(row.get("overdue_days")) > 0):
            continue
        if due_filter == "Chưa đến hạn" and not (isinstance(due, date) and due >= today):
            continue
        if due_filter == "Chưa có hạn" and isinstance(due, date):
            continue
        filtered.append(row)

    st.markdown("#### Danh sách IPC còn phải thanh toán")
    table = pd.DataFrame([
        {
            "IPC": row.get("claim_code", ""),
            "Nhà thầu": row.get("contractor", ""),
            "Hợp đồng": row.get("contract_no", ""),
            "Gói thầu": row.get("package_name", ""),
            "Trạng thái": row.get("status", ""),
            "Giá trị đề nghị": row.get("requested_amount", 0.0),
            "Giá trị được duyệt": row.get("approved_amount", 0.0),
            "Giá trị chứng nhận": row.get("certified_amount", 0.0),
            "Đã thanh toán": row.get("paid_amount", 0.0),
            "Còn phải thanh toán": row.get("outstanding", 0.0),
            "Ngày đến hạn": _date_text(row.get("due_date")),
            "Quá hạn (ngày)": row.get("overdue_days", 0),
            "Giữ lại": row.get("retention", 0.0),
            "Thu hồi tạm ứng": row.get("advance_recovery", 0.0),
            "Khấu trừ": row.get("deductions", 0.0),
            "Ghi chú": row.get("note", ""),
        }
        for row in filtered
    ])
    st.dataframe(table, hide_index=True, use_container_width=True)
    st.download_button(
        "⬇️ Xuất danh sách IPC chưa thanh toán",
        data=table.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"du_tru_thanh_toan_ipc_{pid}_{today:%Y%m%d}.csv",
        mime="text/csv",
        use_container_width=True,
    )

    _render_due_month_summary(st, filtered)
    if no_due_total > 0:
        st.warning(
            f"Có {_money(no_due_total)} IPC chưa ghi nhận ngày đến hạn thanh toán. "
            "Các khoản này vẫn được tính vào tổng còn phải thanh toán nhưng chưa thể phân bổ theo tháng."
        )


def install_finance_management_ui() -> None:
    """Legacy compatibility installer; Cleanup V2 no longer composes it at startup."""
    import streamlit as st

    if getattr(st, "_qlda_finance_management_ui_installed", False):
        return

    previous_subheader = st.subheader
    previous_tabs = st.tabs

    @wraps(previous_subheader)
    def finance_subheader(body, *args, **kwargs):
        if _text(body) == "💰 Quản lý chi phí":
            body = "💰 Quản lý Tài chính"
        return previous_subheader(body, *args, **kwargs)

    @wraps(previous_tabs)
    def finance_tabs(labels, *args, **kwargs):
        label_list = list(labels) if isinstance(labels, (list, tuple)) else labels
        frame = inspect.currentframe()
        caller = frame.f_back if frame else None
        try:
            is_cost_screen = (
                caller is not None
                and caller.f_code.co_name == "render_cost_management"
                and isinstance(label_list, list)
                and label_list == ["Chi phí dự toán (BOQ)", "Thanh toán & giải ngân", "Chi phí phát sinh (VO)"]
            )
            if not is_cost_screen:
                return previous_tabs(labels, *args, **kwargs)

            all_tabs = previous_tabs(
                ["Chi phí dự toán (BOQ)", "Thanh toán & giải ngân", "Chi phí phát sinh (VO)", "Dự trù dòng tiền"],
                *args,
                **kwargs,
            )
            glb, loc = caller.f_globals, caller.f_locals
            db = glb.get("db")
            pid = _int(loc.get("pid"))
            if db is not None and pid > 0:
                can_update_fn = glb.get("_can_update")
                is_admin_fn = glb.get("_is_admin")
                identity_fn = glb.get("_cloud_identity")
                with all_tabs[3]:
                    render_cashflow_finance_sheet(
                        st,
                        db,
                        pid,
                        identity=identity_fn() if callable(identity_fn) else None,
                        can_update=bool(can_update_fn()) if callable(can_update_fn) else False,
                        is_admin=bool(is_admin_fn()) if callable(is_admin_fn) else False,
                    )
            return all_tabs[:3]
        finally:
            del caller
            del frame

    st.subheader = finance_subheader
    st.tabs = finance_tabs
    st._qlda_finance_management_ui_installed = True
    st._qlda_finance_management_ui_marker = PATCH_MARKER


__all__ = ["load_unpaid_ipcs", "render_cashflow_finance_sheet", "install_finance_management_ui"]
