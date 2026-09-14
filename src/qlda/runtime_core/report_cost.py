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


def _table_exists(c, name: str) -> bool:
    try:
        c.execute(f"SELECT 1 FROM {name} LIMIT 1")
        return True
    except Exception:
        return False


def render_cost_report(db, project_id: int) -> None:
    import pandas as pd
    import streamlit as st

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

    bac = sum(float(r.get("budget_total") or 0) for r in boq)
    vo_proposed = sum(float(r.get("proposed_amount") or 0) for r in vo)
    vo_approved = sum(float(r.get("approved_amount") or 0) for r in vo)
    revised_budget = bac + vo_approved
    claim_requested = sum(float(r.get("requested_amount") or 0) for r in claims)
    claim_approved = sum(float(r.get("approved_amount") or 0) for r in claims)
    disbursed = sum(float(r.get("disbursed_amount") or 0) for r in claims)
    remaining = revised_budget - claim_approved
    approved_pct = (claim_approved / revised_budget * 100) if revised_budget else 0
    disbursed_pct = (disbursed / revised_budget * 100) if revised_budget else 0

    st.markdown("---")
    st.markdown("### 💰 Báo cáo chi phí")
    st.caption("Dữ liệu LIVE theo dự án: BOQ/BAC, VO, IPC/Claim và giải ngân.")

    a, b, c, d, e, f = st.columns(6)
    a.metric("BAC / BOQ", f"{_fmt(bac)} VND")
    b.metric("VO được duyệt", f"{_fmt(vo_approved)} VND")
    c.metric("Ngân sách điều chỉnh", f"{_fmt(revised_budget)} VND")
    d.metric("Claim được duyệt", f"{_fmt(claim_approved)} VND", f"{approved_pct:.1f}%")
    e.metric("Đã giải ngân", f"{_fmt(disbursed)} VND", f"{disbursed_pct:.1f}%")
    f.metric("Còn lại", f"{_fmt(remaining)} VND")

    t1, t2, t3 = st.tabs(["📊 Tổng hợp", "📑 BOQ & VO", "💳 Claim & giải ngân"])

    with t1:
        summary = pd.DataFrame([
            {"Chỉ tiêu": "BAC / BOQ", "Giá trị (VND)": bac},
            {"Chỉ tiêu": "VO đề xuất", "Giá trị (VND)": vo_proposed},
            {"Chỉ tiêu": "VO được duyệt", "Giá trị (VND)": vo_approved},
            {"Chỉ tiêu": "Ngân sách điều chỉnh", "Giá trị (VND)": revised_budget},
            {"Chỉ tiêu": "Claim đề nghị", "Giá trị (VND)": claim_requested},
            {"Chỉ tiêu": "Claim được duyệt", "Giá trị (VND)": claim_approved},
            {"Chỉ tiêu": "Đã giải ngân", "Giá trị (VND)": disbursed},
            {"Chỉ tiêu": "Còn lại sau Claim duyệt", "Giá trị (VND)": remaining},
            {"Chỉ tiêu": "Ảnh hưởng chi phí từ hồ sơ", "Giá trị (VND)": doc_cost_impact},
        ])
        show = summary.copy()
        show["Giá trị (VND)"] = show["Giá trị (VND)"].map(_fmt)
        st.dataframe(show, hide_index=True, width="stretch")
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
            st.caption(f"{len(boq):,} dòng BOQ · Tổng {_fmt(bac)} VND")
            st.dataframe(boq_df, hide_index=True, width="stretch", height=460)
        else:
            st.info("Chưa có dữ liệu BOQ trong dự án.")

        st.markdown("#### Chi phí phát sinh (VO)")
        if vo:
            vo_df = pd.DataFrame(vo).rename(columns={
                "vo_code": "Mã VO", "task_ref": "Mã Task", "description": "Nội dung",
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
                "claim_no": "Claim", "claim_code": "Mã IPC", "contractor": "Nhà thầu",
                "contract_value": "Giá trị HĐ", "requested_amount": "Đề nghị", "approved_amount": "Duyệt",
                "disbursed_amount": "Giải ngân", "certified_cumulative": "Lũy kế nghiệm thu",
                "payment_status": "Trạng thái", "disbursement_date": "Ngày giải ngân",
                "latest_revision": "Revision", "updated_at": "Cập nhật",
            })
            for col in ("Giá trị HĐ", "Đề nghị", "Duyệt", "Giải ngân", "Lũy kế nghiệm thu"):
                if col in claim_df.columns:
                    claim_df[col] = claim_df[col].map(_fmt)
            st.caption(
                f"{len(claims):,} Claim · Đề nghị {_fmt(claim_requested)} VND · "
                f"Duyệt {_fmt(claim_approved)} VND · Giải ngân {_fmt(disbursed)} VND"
            )
            st.dataframe(claim_df, hide_index=True, width="stretch")
        else:
            st.info("Chưa có IPC/Claim thanh toán trong dự án.")
