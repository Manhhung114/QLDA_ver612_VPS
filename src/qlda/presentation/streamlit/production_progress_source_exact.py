from __future__ import annotations

"""Source-faithful Contractor Data Hub production overview.

The old pivot indexed only by contractor + worksheet + work-item text and used
``aggfunc='mean'``. In the real SME workbook labels repeat in separate sections;
this renderer keys every row by source_row and source name, so the dashboard is
a faithful view of the workbook.
"""

from typing import Any

import pandas as pd

from qlda.application.contractor_data_hub.official_summary import extract_official_summaries
from qlda.presentation.streamlit.production_progress_overview import (
    _contractor_label,
    _progress_dimension_sort_key,
    _sanitize_multiselect_state,
    _sync_dependent_multiselect_state,
    worksheet_catalog,
)

PATCH_MARKER = "V7 PRODUCTION SOURCE EXACT V1"


def _format_percent_table(frame: pd.DataFrame, progress_columns: list[str]) -> pd.DataFrame:
    out = frame.copy()
    for column in progress_columns:
        if column not in out.columns:
            continue
        out[column] = out[column].map(
            lambda value: "" if pd.isna(value) else f"{float(value):.0f}%"
        )
    return out


def build_source_exact_pivot(view: pd.DataFrame, selected_dimensions: list[str]) -> pd.DataFrame:
    if view.empty:
        return pd.DataFrame()
    index_cols = ["Nhà thầu", "Nguồn", "Worksheet", "Dòng nguồn", "Công tác"]
    pivot = view.pivot_table(
        index=index_cols,
        columns="Zone",
        values="Tiến độ (%)",
        aggfunc="first",
        sort=False,
    )
    ordered = [x for x in selected_dimensions if x in pivot.columns]
    if ordered:
        pivot = pivot.reindex(columns=ordered)
    pivot = pivot.reset_index().sort_values(
        ["Nhà thầu", "Nguồn", "Worksheet", "Dòng nguồn"], kind="stable"
    )
    return pivot


def _render_official_summary(st, records: list[dict[str, Any]], contractors: list[dict[str, Any]]) -> None:
    official = extract_official_summaries(records)
    if not official:
        st.caption(
            "Chưa có tổng chính thức được nhận diện từ phần TỔNG SẢN LƯỢNG của file nguồn. "
            "Sau khi cập nhật bản mới, Admin cần đồng bộ lại Google Sheet một lần."
        )
        return

    contractor_by_workspace = {
        int(row.get("workspace_project_id") or 0): dict(row)
        for row in contractors
        if int(row.get("workspace_project_id") or 0) > 0
    }
    rows: list[dict[str, Any]] = []
    for item in official:
        contractor = contractor_by_workspace.get(int(item.get("workspace_project_id") or 0), {})
        rows.append({
            "Nhà thầu": _contractor_label(contractor),
            "Worksheet": item.get("worksheet") or "",
            "TỔNG SẢN LƯỢNG (%)": (
                round(float(item["weighted_total"]), 1)
                if item.get("weighted_total") is not None else None
            ),
            "Tổng theo đầu mục tiến độ (%)": (
                round(float(item["package_total"]), 1)
                if item.get("package_total") is not None else None
            ),
            "Dòng tổng": item.get("weighted_total_row") or "",
            "Dòng tổng đầu mục": item.get("package_total_row") or "",
        })
    st.markdown("#### Tổng chính thức theo file nguồn")
    st.caption(
        "Đây là các số tổng do chính file Google Sheet tính. App và Trợ lý AI không lấy trung bình toàn bộ dòng chi tiết để thay thế các số này."
    )
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)


def render_overview_source_exact(
    st,
    service,
    project_id: int,
    contractors: list[dict[str, Any]],
    workspace_ids: list[int],
) -> None:
    repo = service.repo
    records = repo.records(project_id, workspace_ids=workspace_ids, limit=20000)
    production = [
        x for x in records
        if str(x.get("record_type") or "").upper() == "PRODUCTION"
    ]
    metrics = repo.project_metrics(project_id, workspace_ids=workspace_ids)
    official = extract_official_summaries(records)

    total_sources = sum(int(x.get("source_count") or 0) for x in metrics)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Kho nhà thầu", f"{len(metrics):,}")
    c2.metric("Nguồn dữ liệu", f"{total_sources:,}")
    c3.metric("Records", f"{len(records):,}")
    if len(official) == 1 and official[0].get("weighted_total") is not None:
        c4.metric("Tổng chính thức", f"{float(official[0]['weighted_total']):.1f}%")
    else:
        c4.metric("Tổng chính thức", f"{len(official):,} worksheet")

    st.markdown("#### Trạng thái từng kho nhà thầu")
    if metrics:
        table = []
        for row in metrics:
            table.append({
                "Nhà thầu": f"{row.get('contractor_code','')} - {row.get('contractor_name','')}".strip(" -"),
                "Nguồn": int(row.get("source_count") or 0),
                "Records": int(row.get("record_count") or 0),
                "Điểm sản lượng": int(row.get("production_points") or 0),
                "Đồng bộ gần nhất": row.get("last_sync") or "",
                "Lỗi": row.get("last_error") or "",
            })
        st.dataframe(pd.DataFrame(table), hide_index=True, use_container_width=True)
    else:
        st.info("Chưa có kho dữ liệu nhà thầu.")

    alerts = service.project_alerts(project_id, workspace_ids=workspace_ids)
    if alerts:
        st.markdown("#### Cảnh báo dữ liệu")
        for alert in alerts[:40]:
            text = f"**{alert.get('contractor','')}** · {alert.get('message','')}"
            level = str(alert.get("level") or "")
            if level == "error":
                st.error(text)
            elif level == "warning":
                st.warning(text)
            else:
                st.info(text)

    catalog = worksheet_catalog(records, contractors)
    if not catalog:
        st.info("Chưa có worksheet nào được đồng bộ. Hãy đồng bộ Sheet/Drive của nhà thầu.")
        return

    catalog_df = pd.DataFrame(catalog)
    contractor_options = sorted(
        x for x in catalog_df["Nhà thầu"].dropna().astype(str).unique().tolist() if x
    )

    f1, f2, f3 = st.columns(3)
    contractor_key = f"cdh_overview_contractors_{int(project_id)}"
    _sanitize_multiselect_state(st, contractor_key, contractor_options)
    selected_contractors = f1.multiselect(
        "Nhà thầu", contractor_options, default=contractor_options, key=contractor_key
    )

    catalog_scope = catalog_df[catalog_df["Nhà thầu"].isin(selected_contractors)].copy()
    worksheet_options = sorted(
        x for x in catalog_scope["Worksheet"].dropna().astype(str).unique().tolist() if x
    )
    worksheet_key = f"cdh_overview_worksheets_{int(project_id)}"
    _sanitize_multiselect_state(st, worksheet_key, worksheet_options)
    selected_ws = f2.multiselect(
        "Worksheet",
        worksheet_options,
        default=worksheet_options,
        key=worksheet_key,
        help="Chọn worksheet cần sử dụng; Zone/tầng dữ liệu đổi theo đúng worksheet đã chọn.",
    )

    contractor_by_workspace = {
        int(x.get("workspace_project_id") or 0): dict(x)
        for x in contractors
        if int(x.get("workspace_project_id") or 0) > 0
    }
    production_rows: list[dict[str, Any]] = []
    for item in production:
        contractor = contractor_by_workspace.get(int(item.get("workspace_project_id") or 0), {})
        production_rows.append({
            "Nhà thầu": _contractor_label(contractor),
            "Nguồn": item.get("source_name") or "",
            "Worksheet": item.get("worksheet") or "",
            "Dòng nguồn": int(item.get("source_row") or 0),
            "Công tác": item.get("work_item") or "",
            "Zone": item.get("zone") or "",
            "Tiến độ (%)": float(item.get("progress_percent") or 0),
            "Sync": item.get("synced_at") or "",
        })
    production_df = pd.DataFrame(production_rows)

    zone_options: list[str] = []
    if not production_df.empty:
        zone_scope = production_df[
            production_df["Nhà thầu"].isin(selected_contractors)
            & production_df["Worksheet"].isin(selected_ws)
        ]
        zone_options = sorted(
            [x for x in zone_scope["Zone"].dropna().astype(str).unique().tolist() if x],
            key=_progress_dimension_sort_key,
        )

    zone_key = f"cdh_overview_zones_{int(project_id)}"
    zone_signature_key = f"cdh_overview_zone_scope_{int(project_id)}"
    zone_signature = repr((tuple(sorted(selected_contractors)), tuple(sorted(selected_ws))))
    _sync_dependent_multiselect_state(
        st,
        key=zone_key,
        options=zone_options,
        signature_key=zone_signature_key,
        signature=zone_signature,
    )
    selected_zones = f3.multiselect(
        "Zone / tầng dữ liệu",
        zone_options,
        key=zone_key,
        help="Giữ đúng T1/TL/T2/... và TỔNG của từng dòng nguồn; không gộp các dòng chỉ vì trùng tên công tác.",
    )

    selected_catalog = catalog_scope[catalog_scope["Worksheet"].isin(selected_ws)].copy()
    normalized_count = int((selected_catalog["Trạng thái"] == "Đã chuẩn hóa").sum())
    not_normalized = selected_catalog[selected_catalog["Trạng thái"] != "Đã chuẩn hóa"]
    st.caption(
        f"Đã đồng bộ {len(selected_catalog):,} worksheet · Đã chuẩn hóa {normalized_count:,} · "
        f"Chưa chuẩn hóa {len(not_normalized):,}."
    )
    if not not_normalized.empty:
        names = ", ".join(sorted(not_normalized["Worksheet"].astype(str).unique().tolist())[:20])
        st.warning(
            "Các worksheet đã đọc từ Google nhưng chưa nhận diện được dữ liệu sản lượng: " + names + "."
        )

    with st.expander("Trạng thái đồng bộ worksheet", expanded=False):
        visible = ["Nhà thầu", "Worksheet", "Trạng thái", "Records", "Điểm sản lượng", "Nguồn"]
        st.dataframe(
            selected_catalog[visible],
            hide_index=True,
            use_container_width=True,
            height=min(420, 44 + max(1, len(selected_catalog)) * 35),
        )

    st.markdown("#### Bảng sản lượng chuẩn hóa")
    st.caption(
        "Mỗi hàng tương ứng đúng một dòng trong Google Sheet. Các nhãn trùng nhau ở CĂN HỘ / HÀNH LANG / TRỤC ĐỨNG không còn bị lấy trung bình. "
        "Phần trăm hiển thị làm tròn 0 chữ số giống định dạng của file nguồn; dữ liệu lưu và AI vẫn giữ giá trị chính xác."
    )
    if production_df.empty:
        st.info("Các worksheet đã đồng bộ nhưng chưa có dữ liệu sản lượng chuẩn hóa.")
        _render_official_summary(st, records, contractors)
        return

    view = production_df[
        production_df["Nhà thầu"].isin(selected_contractors)
        & production_df["Worksheet"].isin(selected_ws)
        & production_df["Zone"].isin(selected_zones)
    ].copy()
    if view.empty:
        st.info("Không có dữ liệu sản lượng chuẩn hóa phù hợp bộ lọc.")
        _render_official_summary(st, records, contractors)
        return

    pivot = build_source_exact_pivot(view, selected_zones)
    progress_columns = [x for x in selected_zones if x in pivot.columns]
    display = _format_percent_table(pivot, progress_columns)
    if "Dòng nguồn" in display.columns:
        display = display.drop(columns=["Dòng nguồn"])
    st.dataframe(display, hide_index=True, use_container_width=True, height=520)

    _render_official_summary(st, records, contractors)

    st.markdown("#### Công tác cần chú ý")
    attention = view[view["Tiến độ (%)"] < 100].sort_values(
        ["Tiến độ (%)", "Nhà thầu", "Worksheet", "Dòng nguồn", "Công tác"]
    )
    st.dataframe(attention, hide_index=True, use_container_width=True, height=420)


def install_production_progress_source_exact() -> None:
    from qlda.presentation.streamlit import production_progress_ui

    production_progress_ui._render_overview = render_overview_source_exact
    production_progress_ui._qlda_source_exact_v1 = True
    production_progress_ui._qlda_source_exact_marker = PATCH_MARKER


__all__ = [
    "PATCH_MARKER",
    "build_source_exact_pivot",
    "render_overview_source_exact",
    "install_production_progress_source_exact",
]
