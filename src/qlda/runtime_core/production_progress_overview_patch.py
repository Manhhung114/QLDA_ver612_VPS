from __future__ import annotations

import re
from typing import Any, Iterable

import pandas as pd


def _contractor_label(row: dict[str, Any]) -> str:
    code = str(row.get("contractor_code") or "").strip()
    name = str(row.get("contractor_name") or "").strip()
    return f"{code} - {name}".strip(" -") or f"Nhà thầu #{row.get('id','')}"


def _progress_dimension_sort_key(value: object) -> tuple[int, float, str]:
    """Natural order for Zone 1/2/3 and SME floor labels T1/TL/T2/.../T19A."""
    text = str(value or "").strip()
    compact = re.sub(r"\s+", "", text).upper()

    floor = re.fullmatch(r"T(\d+)([A-Z]?)", compact)
    if floor:
        number = float(floor.group(1))
        suffix = floor.group(2)
        if suffix:
            number += (ord(suffix) - ord("A") + 1) / 10.0
        return (0, number, text)
    if compact == "TL":
        # SME source places TL between T1 and T2.
        return (0, 1.5, text)

    zone = re.fullmatch(r"(?:ZONE|KV|KHU|AREA)(\d+)", compact)
    if zone:
        return (1, float(zone.group(1)), text)
    return (2, 0.0, text.casefold())


def _sanitize_multiselect_state(st, key: str, options: list[str]) -> None:
    """Drop stale selections when synchronized worksheets change their dimensions.

    Before floor-matrix support a user could have Zone 1/2/3 stored in session
    state while switching to S2/S3/S4, whose dimensions are T1/TL/T2/... . If all
    old values are invalid, select the currently available dimensions once so the
    table does not appear empty after a successful re-sync. A deliberately empty
    selection remains empty.
    """
    try:
        if key not in st.session_state:
            return
        current = list(st.session_state.get(key) or [])
        valid = [x for x in current if x in options]
        if current and not valid and options:
            valid = list(options)
        st.session_state[key] = valid
    except Exception:
        pass


def _sync_dependent_multiselect_state(
    st,
    *,
    key: str,
    options: list[str],
    signature_key: str,
    signature: str,
) -> None:
    """Keep a dependent multiselect aligned with its parent worksheet filter.

    When the selected contractor/worksheet set changes, previously selected
    dimensions may belong to a different worksheet (for example Zone 1/2/3 from
    Hầm while S2/S3/S4 use T1/TL/T2/...). Reset the dependent selection to the
    dimensions available for the new worksheet scope. On later reruns with the
    same worksheet scope, preserve the user's manual subset selection.
    """
    try:
        previous_signature = str(st.session_state.get(signature_key) or "")
        if previous_signature != signature:
            st.session_state[signature_key] = signature
            st.session_state[key] = list(options)
            return
        if key not in st.session_state:
            st.session_state[key] = list(options)
            return
        _sanitize_multiselect_state(st, key, options)
    except Exception:
        pass


def worksheet_catalog(
    records: Iterable[dict[str, Any]],
    contractors: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return one status row per contractor + worksheet.

    A worksheet is considered synchronized as soon as any row from it exists in
    PostgreSQL. It is considered normalized only when at least one PRODUCTION row
    was generated. This lets the UI expose sheets whose raw data was synchronized
    but whose layout could not yet be normalized.
    """
    contractor_by_workspace = {
        int(row.get("workspace_project_id") or 0): dict(row)
        for row in contractors
        if int(row.get("workspace_project_id") or 0) > 0
    }
    grouped: dict[tuple[int, str], dict[str, Any]] = {}
    for raw in records:
        item = dict(raw or {})
        worksheet = str(item.get("worksheet") or "").strip()
        if not worksheet:
            continue
        workspace_id = int(item.get("workspace_project_id") or 0)
        key = (workspace_id, worksheet)
        entry = grouped.setdefault(
            key,
            {
                "workspace_project_id": workspace_id,
                "Nhà thầu": _contractor_label(contractor_by_workspace.get(workspace_id, {})),
                "Worksheet": worksheet,
                "Records": 0,
                "Điểm sản lượng": 0,
                "Nguồn": set(),
            },
        )
        entry["Records"] += 1
        if str(item.get("record_type") or "").upper() == "PRODUCTION":
            entry["Điểm sản lượng"] += 1
        source_name = str(item.get("source_name") or "").strip()
        if source_name:
            entry["Nguồn"].add(source_name)

    out: list[dict[str, Any]] = []
    for entry in grouped.values():
        production_points = int(entry["Điểm sản lượng"] or 0)
        out.append(
            {
                "workspace_project_id": int(entry["workspace_project_id"] or 0),
                "Nhà thầu": str(entry["Nhà thầu"] or ""),
                "Worksheet": str(entry["Worksheet"] or ""),
                "Trạng thái": "Đã chuẩn hóa" if production_points > 0 else "Chưa chuẩn hóa",
                "Records": int(entry["Records"] or 0),
                "Điểm sản lượng": production_points,
                "Nguồn": " · ".join(sorted(entry["Nguồn"])),
            }
        )
    return sorted(out, key=lambda x: (x["Nhà thầu"], x["Worksheet"]))


def _render_overview_all_worksheets(
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

    total_sources = sum(int(x.get("source_count") or 0) for x in metrics)
    total_records = len(records)
    avg_progress = 0.0
    if production:
        avg_progress = sum(float(x.get("progress_percent") or 0) for x in production) / len(production)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Kho nhà thầu", f"{len(metrics):,}")
    c2.metric("Nguồn dữ liệu", f"{total_sources:,}")
    c3.metric("Records AI", f"{total_records:,}")
    c4.metric("Sản lượng TB", f"{avg_progress:.1f}%" if production else "—")

    st.markdown("#### Trạng thái từng kho nhà thầu")
    if metrics:
        table = []
        for row in metrics:
            avg = row.get("avg_progress")
            table.append({
                "Nhà thầu": f"{row.get('contractor_code','')} - {row.get('contractor_name','')}".strip(" -"),
                "Nguồn": int(row.get("source_count") or 0),
                "Records": int(row.get("record_count") or 0),
                "Điểm sản lượng": int(row.get("production_points") or 0),
                "Tiến độ TB (%)": round(float(avg), 1) if avg is not None else None,
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
        "Nhà thầu",
        contractor_options,
        default=contractor_options,
        key=contractor_key,
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
        help="Chọn một hoặc nhiều worksheet cần sử dụng. Dữ liệu tiến độ bên phải sẽ tự thay đổi theo lựa chọn này.",
    )

    production_rows: list[dict[str, Any]] = []
    contractor_by_workspace = {
        int(x.get("workspace_project_id") or 0): dict(x)
        for x in contractors
        if int(x.get("workspace_project_id") or 0) > 0
    }
    for item in production:
        contractor = contractor_by_workspace.get(int(item.get("workspace_project_id") or 0), {})
        production_rows.append({
            "Nhà thầu": _contractor_label(contractor),
            "Nguồn": item.get("source_name") or "",
            "Worksheet": item.get("worksheet") or "",
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
            (
                x for x in zone_scope["Zone"].dropna().astype(str).unique().tolist() if x
            ),
            key=_progress_dimension_sort_key,
        )

    zone_key = f"cdh_overview_zones_{int(project_id)}"
    zone_signature_key = f"cdh_overview_zone_scope_{int(project_id)}"
    zone_signature = repr((
        tuple(sorted(selected_contractors)),
        tuple(sorted(selected_ws)),
    ))
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
        help=(
            "Tự động lấy đúng chiều dữ liệu của worksheet đã chọn: Hầm dùng Zone 1/2/3; "
            "các sheet dạng ma trận dùng T1, TL, T2, ... Nếu chọn nhiều worksheet, app hiển thị hợp nhất "
            "các chiều dữ liệu của các worksheet đang chọn."
        ),
    )

    selected_catalog = catalog_scope[catalog_scope["Worksheet"].isin(selected_ws)].copy()
    normalized_count = int((selected_catalog["Trạng thái"] == "Đã chuẩn hóa").sum())
    not_normalized = selected_catalog[selected_catalog["Trạng thái"] != "Đã chuẩn hóa"]
    st.caption(
        f"Đã đồng bộ {len(selected_catalog):,} worksheet · "
        f"Đã chuẩn hóa {normalized_count:,} · Chưa chuẩn hóa {len(not_normalized):,}."
    )
    if not not_normalized.empty:
        names = ", ".join(
            sorted(not_normalized["Worksheet"].astype(str).unique().tolist())[:20]
        )
        st.warning(
            "Các worksheet đã đọc từ Google nhưng chưa nhận diện được dữ liệu sản lượng: "
            + names
            + ". App hỗ trợ cả dạng Zone/Khu vực và dạng ma trận Công tác + T1/TL/T2/...; "
            "hãy kiểm tra dòng tiêu đề hoặc dữ liệu phần trăm nếu sheet vẫn ở trạng thái này."
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
    if production_df.empty:
        st.info("Các worksheet đã đồng bộ nhưng chưa có dữ liệu sản lượng chuẩn hóa.")
        return

    view = production_df[
        production_df["Nhà thầu"].isin(selected_contractors)
        & production_df["Worksheet"].isin(selected_ws)
        & production_df["Zone"].isin(selected_zones)
    ].copy()
    if view.empty:
        st.info("Không có dữ liệu sản lượng chuẩn hóa phù hợp bộ lọc.")
        return

    pivot = view.pivot_table(
        index=["Nhà thầu", "Worksheet", "Công tác"],
        columns="Zone",
        values="Tiến độ (%)",
        aggfunc="mean",
    )
    ordered_columns = [x for x in selected_zones if x in pivot.columns]
    if ordered_columns:
        pivot = pivot.reindex(columns=ordered_columns)
    st.dataframe(pivot.round(1), use_container_width=True, height=480)

    st.markdown("#### Tiến độ trung bình theo nhà thầu")
    by_contractor = view.groupby("Nhà thầu", as_index=False)["Tiến độ (%)"].mean()
    st.bar_chart(by_contractor.set_index("Nhà thầu")["Tiến độ (%)"])

    st.markdown("#### Công tác cần chú ý")
    attention = view[view["Tiến độ (%)"] < 100].sort_values(
        ["Tiến độ (%)", "Nhà thầu", "Worksheet", "Công tác"]
    )
    st.dataframe(attention, hide_index=True, use_container_width=True, height=420)


def install_production_progress_overview_patch() -> None:
    """Install the all-worksheet overview without changing public UI entrypoints."""
    from qlda.presentation.streamlit import production_progress_ui

    if getattr(production_progress_ui, "_qlda_all_worksheet_overview_v1", False):
        return
    production_progress_ui._render_overview = _render_overview_all_worksheets
    production_progress_ui._qlda_all_worksheet_overview_v1 = True


__all__ = ["worksheet_catalog", "install_production_progress_overview_patch"]
