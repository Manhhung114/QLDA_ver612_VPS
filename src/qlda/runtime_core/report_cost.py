from __future__ import annotations

from typing import Any


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


def _fmt(value: Any) -> str:
    try:
        return f"{float(value or 0):,.0f}"
    except Exception:
        return "0"


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or 0)
    except Exception:
        return float(default)


def _metric_money(value: Any) -> str:
    """Compact money text for metric cards so large VND values do not truncate."""
    number = _num(value)
    sign = "-" if number < 0 else ""
    number = abs(number)
    if number >= 1_000_000_000:
        return f"{sign}{number / 1_000_000_000:,.2f} tỷ"
    if number >= 1_000_000:
        return f"{sign}{number / 1_000_000:,.1f} triệu"
    return f"{sign}{number:,.0f} VND"


def _table_exists(c, name: str) -> bool:
    try:
        c.execute(f"SELECT 1 FROM {name} LIMIT 1")
        return True
    except Exception:
        return False


def render_cost_report(db, project_id: int) -> None:
    import pandas as pd
    import streamlit as st

    from qlda.runtime_core.boq_after_tax_budget import boq_budget_total, saved_after_tax_total
    from qlda.runtime_core.boq_persistence import load_saved_boq_workbook
    from qlda.runtime_core.project_cost_management import build_cost_snapshot

    pid = int(project_id)
    with db.connect() as c:
        boq = [_rowdict(r) for r in c.execute(
            "SELECT task_ref,boq_item,quantity,unit,unit_price,budget_total,contract_type,contractor,note "
            "FROM cost_budgets WHERE project_id=? ORDER BY id",
            (pid,),
        ).fetchall()]
        vo = [_rowdict(r) for r in c.execute(
            "SELECT vo_code,task_ref,description,proposed_amount,approved_amount,funding_source,status,vo_date,note "
            "FROM cost_variations WHERE project_id=? ORDER BY vo_date,id",
            (pid,),
        ).fetchall()]
        docs_cost = c.execute(
            "SELECT COALESCE(SUM(cost_impact),0) AS total FROM documents WHERE project_id=?",
            (pid,),
        ).fetchone()
        doc_cost_impact = float((_rowdict(docs_cost).get("total") if docs_cost else 0) or 0)

        claims = []
        if _table_exists(c, "payment_claims"):
            claims = [_rowdict(r) for r in c.execute(
                "SELECT claim_no,claim_code,contractor,contract_value,requested_amount,approved_amount,"
                "disbursed_amount,certified_cumulative,payment_status,disbursement_date,latest_revision,updated_at "
                "FROM payment_claims WHERE project_id=? ORDER BY claim_no",
                (pid,),
            ).fetchall()]
        elif _table_exists(c, "payment_tracking"):
            claims = [_rowdict(r) for r in c.execute(
                "SELECT installment AS claim_no,payment_code AS claim_code,'' AS contractor,0 AS contract_value,"
                "certified_cumulative AS requested_amount,certified_cumulative AS approved_amount,paid_amount AS disbursed_amount,"
                "certified_cumulative,payment_status,payment_date AS disbursement_date,0 AS latest_revision,updated_at "
                "FROM payment_tracking WHERE project_id=? ORDER BY id",
                (pid,),
            ).fetchall()]

    # One authoritative BAC across BOQ, Finance, Cost Control and Overview report.
    bac = float(boq_budget_total(db, pid) or 0)
    detail_boq = sum(float(r.get("budget_total") or 0) for r in boq)
    after_tax = saved_after_tax_total(db, pid)
    try:
        saved_boq = load_saved_boq_workbook(db, pid) or {}
    except Exception:
        saved_boq = {}
    summary_before_tax = _num(saved_boq.get("before_tax_total"), detail_boq)
    summary_vat = _num(saved_boq.get("vat_total"), max(0.0, bac - summary_before_tax))
    detail_summary_gap = detail_boq - summary_before_tax

    vo_proposed = sum(float(r.get("proposed_amount") or 0) for r in vo)
    vo_approved = sum(float(r.get("approved_amount") or 0) for r in vo)

    # Ngân sách điều chỉnh trong Báo cáo Tổng quan phải cùng nguồn với màn hình
    # Kiểm soát chi phí: Chi phí đã cam kết = Hợp đồng + Phụ lục hợp đồng.
    # Không tự cộng VO tại đây để tránh cộng trùng khi VO đã được hợp thức hóa
    # thành Phụ lục hợp đồng.
    try:
        cost_snapshot = build_cost_snapshot(db, pid) or {}
    except Exception:
        cost_snapshot = {}
    committed_cost = max(0.0, _num(cost_snapshot.get("committed_cost")))
    revised_budget = committed_cost

    claim_requested = sum(float(r.get("requested_amount") or 0) for r in claims)
    claim_approved = sum(float(r.get("approved_amount") or 0) for r in claims)
    disbursed = sum(float(r.get("disbursed_amount") or 0) for r in claims)
    remaining = revised_budget - claim_approved
    approved_pct = (claim_approved / revised_budget * 100) if revised_budget else 0
    disbursed_pct = (disbursed / revised_budget * 100) if revised_budget else 0

    st.markdown("---")
    st.markdown("### 💰 Báo cáo Chi phí")
    st.caption(
        "Dữ liệu LIVE theo dự án: BOQ/BAC, VO, IPC và giải ngân. "
        "BAC dùng giá trị BOQ sau thuế; Ngân sách điều chỉnh lấy trực tiếp từ Chi phí đã cam kết "
        "trong Kiểm soát chi phí."
    )

    a, b, c, d, e, f = st.columns(6)
    a.metric("BAC / BOQ sau thuế", _metric_money(bac))
    b.metric("VO được duyệt", _metric_money(vo_approved))
    c.metric("Ngân sách điều chỉnh", _metric_money(revised_budget))
    d.metric("IPC được duyệt", _metric_money(claim_approved), f"{approved_pct:.1f}%")
    e.metric("Đã giải ngân", _metric_money(disbursed), f"{disbursed_pct:.1f}%")
    f.metric("Còn lại", _metric_money(remaining))

    t1, t2, t3 = st.tabs(["📊 Tổng hợp", "📑 BOQ & VO", "💳 IPC & giải ngân"])

    with t1:
        summary_rows = [
            {"Chỉ tiêu": "BAC / BOQ sau thuế", "Giá trị (VND)": bac},
        ]
        if after_tax is not None:
            summary_rows += [
                {"Chỉ tiêu": "BOQ tổng hợp trước thuế", "Giá trị (VND)": summary_before_tax},
                {"Chỉ tiêu": "Thuế VAT", "Giá trị (VND)": summary_vat},
            ]
        summary_rows += [
            {"Chỉ tiêu": "VO đề xuất", "Giá trị (VND)": vo_proposed},
            {"Chỉ tiêu": "VO được duyệt", "Giá trị (VND)": vo_approved},
            {"Chỉ tiêu": "Chi phí đã cam kết", "Giá trị (VND)": committed_cost},
            {"Chỉ tiêu": "Ngân sách điều chỉnh", "Giá trị (VND)": revised_budget},
            {"Chỉ tiêu": "IPC đề nghị", "Giá trị (VND)": claim_requested},
            {"Chỉ tiêu": "IPC được duyệt", "Giá trị (VND)": claim_approved},
            {"Chỉ tiêu": "Đã giải ngân", "Giá trị (VND)": disbursed},
            {"Chỉ tiêu": "Còn lại sau IPC duyệt", "Giá trị (VND)": remaining},
            {"Chỉ tiêu": "Ảnh hưởng chi phí từ hồ sơ", "Giá trị (VND)": doc_cost_impact},
        ]
        summary = pd.DataFrame(summary_rows)
        show = summary.copy()
        show["Giá trị (VND)"] = show["Giá trị (VND)"].map(_fmt)
        st.dataframe(show, hide_index=True, width="stretch")
        if after_tax is not None and abs(detail_summary_gap) > 1:
            st.caption(
                "ℹ️ BOQ chi tiết đang lệch tổng hợp trước thuế "
                f"{_fmt(abs(detail_summary_gap))} VND. Báo cáo ngân sách dùng số tổng hợp trong workbook làm chuẩn."
            )
        try:
            import plotly.express as px
            fig = px.bar(summary, x="Chỉ tiêu", y="Giá trị (VND)", title="Tổng hợp chi phí dự án")
            st.plotly_chart(fig, width="stretch")
        except Exception:
            pass

    with t2:
        st.markdown("#### BOQ")
        if boq:
            boq_df = pd.DataFrame(boq)
            boq_df = boq_df.rename(columns={
                "task_ref": "Mã/Sheet", "boq_item": "Hạng mục", "quantity": "KL", "unit": "ĐVT",
                "unit_price": "Đơn giá", "budget_total": "Thành tiền", "contract_type": "Loại HĐ",
                "contractor": "Nhà thầu", "note": "Ghi chú",
            })
            for col in ("KL", "Đơn giá", "Thành tiền"):
                if col in boq_df.columns:
                    boq_df[col] = boq_df[col].map(_fmt)
            if after_tax is not None:
                st.caption(
                    f"{len(boq):,} dòng BOQ · Tổng hợp trước thuế {_fmt(summary_before_tax)} VND · "
                    f"VAT {_fmt(summary_vat)} VND · BAC sau thuế {_fmt(bac)} VND"
                )
            else:
                st.caption(f"{len(boq):,} dòng BOQ · Tổng {_fmt(bac)} VND")
            st.dataframe(boq_df, hide_index=True, width="stretch", height=460)
        else:
            st.info("Chưa có dữ liệu BOQ trong dự án.")

        st.markdown("#### Chi phí phát sinh (VO)")
        if vo:
            vo_df = pd.DataFrame(vo).rename(columns={
                "vo_code": "Mã VO", "task_ref": "Mã công việc", "description": "Nội dung",
                "proposed_amount": "Đề xuất", "approved_amount": "Được duyệt",
                "funding_source": "Nguồn vốn", "status": "Trạng thái", "vo_date": "Ngày", "note": "Ghi chú",
            })
            for col in ("Đề xuất", "Được duyệt"):
                vo_df[col] = vo_df[col].map(_fmt)
            st.dataframe(vo_df, hide_index=True, width="stretch")
        else:
            st.info("Chưa có VO.")

    with t3:
        if claims:
            claim_df = pd.DataFrame(claims).rename(columns={
                "claim_no": "Kỳ IPC", "claim_code": "Mã IPC", "contractor": "Nhà thầu",
                "contract_value": "Giá trị HĐ", "requested_amount": "Đề nghị", "approved_amount": "Duyệt",
                "disbursed_amount": "Giải ngân", "certified_cumulative": "Lũy kế nghiệm thu",
                "payment_status": "Trạng thái", "disbursement_date": "Ngày giải ngân",
                "latest_revision": "Lần sửa", "updated_at": "Cập nhật",
            })
            for col in ("Giá trị HĐ", "Đề nghị", "Duyệt", "Giải ngân", "Lũy kế nghiệm thu"):
                if col in claim_df.columns:
                    claim_df[col] = claim_df[col].map(_fmt)
            st.caption(
                f"{len(claims):,} IPC · Đề nghị {_fmt(claim_requested)} VND · "
                f"Duyệt {_fmt(claim_approved)} VND · Giải ngân {_fmt(disbursed)} VND"
            )
            st.dataframe(claim_df, hide_index=True, width="stretch")
        else:
            st.info("Chưa có IPC thanh toán trong dự án.")
