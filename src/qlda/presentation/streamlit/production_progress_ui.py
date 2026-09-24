from __future__ import annotations

import pandas as pd

from qlda.application.google_sheets.service import normalize_production_sheet, parse_spreadsheet_id
from qlda.infrastructure.google_sheets.client import GoogleSheetsClient
from qlda.runtime_core.production_progress import ProductionProgressStore


def _sync_source(store: ProductionProgressStore, client: GoogleSheetsClient, project_id: int, source: dict) -> int:
    spreadsheet_id = str(source.get("spreadsheet_id") or "")
    all_rows = []
    for worksheet in list(source.get("worksheet_names") or []):
        safe_name = str(worksheet).replace("'", "''")
        values = client.values(spreadsheet_id, f"'{safe_name}'!A:ZZ")
        all_rows.extend(normalize_production_sheet(str(worksheet), values))
    store.replace_current(project_id, str(source["source_id"]), all_rows)
    return len(all_rows)


def render_production_progress(st, db, project_id: int, *, identity: dict | None = None) -> None:
    """Render production progress as a native QLDA navigation section.

    This renderer is intentionally reusable from the main Streamlit app. It is not
    a standalone Streamlit page and therefore inherits the current QLDA project,
    session and RBAC context.
    """
    project_id = int(project_id)
    identity = dict(identity or {})
    role = str(identity.get("role") or "read").strip().lower()
    can_update = role in {"update", "admin"}
    can_admin = role == "admin"

    store = ProductionProgressStore(db)
    client = GoogleSheetsClient()

    st.subheader("📊 Sản lượng thi công")
    st.caption(
        "Theo dõi sản lượng từ nhiều Google Sheets trong cùng dự án: "
        "nhiều file → nhiều worksheet → dữ liệu Công tác × Zone × % hoàn thành thống nhất."
    )

    tab_overview, tab_sources, tab_history = st.tabs([
        "📈 Tổng quan",
        "🔗 Google Sheets",
        "🕘 Lịch sử",
    ])

    with tab_sources:
        st.markdown("#### Nguồn Google Sheets của dự án")
        try:
            service_email = client.service_account_email
            st.info(
                "Chia sẻ các Google Sheet cần đọc cho Service Account dưới đây với quyền **Viewer**:\n\n"
                f"`{service_email}`"
            )
        except Exception as exc:
            st.warning(str(exc))

        if can_admin:
            with st.form(f"production_add_sheet_{project_id}"):
                c1, c2 = st.columns([2, 3])
                source_name = c1.text_input(
                    "Tên nguồn",
                    placeholder="Theo dõi sản lượng MEP - S234",
                    key=f"production_source_name_{project_id}",
                )
                sheet_url = c2.text_input(
                    "Link Google Sheet / Spreadsheet ID",
                    placeholder="https://docs.google.com/spreadsheets/d/.../edit",
                    key=f"production_sheet_url_{project_id}",
                )
                test = st.form_submit_button("🔎 Đọc danh sách worksheet", type="primary")

            if test:
                try:
                    sid = parse_spreadsheet_id(sheet_url)
                    meta = client.metadata(sid)
                    st.session_state[f"production_sheet_candidate_{project_id}"] = {
                        "spreadsheet_id": sid,
                        "name": source_name.strip() or meta.get("title") or "Google Sheet",
                        "title": meta.get("title") or "",
                        "worksheets": [
                            x["title"] for x in meta.get("sheets", []) if not x.get("hidden")
                        ],
                    }
                    st.success(f"Đã kết nối: {meta.get('title') or sid}")
                except Exception as exc:
                    st.error(str(exc))

            candidate = st.session_state.get(f"production_sheet_candidate_{project_id}")
            if candidate:
                selected_tabs = st.multiselect(
                    "Worksheet cần đồng bộ",
                    candidate.get("worksheets") or [],
                    default=candidate.get("worksheets") or [],
                    key=f"production_sheet_tabs_{project_id}",
                )
                c1, c2 = st.columns([1, 4])
                if c1.button(
                    "💾 Lưu nguồn",
                    type="primary",
                    disabled=not bool(selected_tabs),
                    key=f"production_save_sheet_{project_id}",
                ):
                    store.save_source(
                        project_id,
                        name=candidate["name"],
                        spreadsheet_id=candidate["spreadsheet_id"],
                        spreadsheet_title=candidate["title"],
                        worksheet_names=selected_tabs,
                    )
                    st.session_state.pop(f"production_sheet_candidate_{project_id}", None)
                    st.success("Đã lưu nguồn Google Sheets vào dự án.")
                    st.rerun()
                c2.caption(
                    "Có thể chọn Hầm, S2, S3, S4… Một dự án có thể khai báo nhiều Google Sheet."
                )
        else:
            st.caption(
                "Chỉ Admin được thêm/xóa nguồn Google Sheets. "
                "Người có quyền Cập nhật được phép đồng bộ dữ liệu."
            )

        sources = store.list_sources(project_id)
        if not sources:
            st.info("Dự án chưa có nguồn Google Sheets sản lượng.")

        for source in sources:
            label = f"{source['name']} • {source.get('spreadsheet_title') or source['spreadsheet_id']}"
            with st.expander(label, expanded=False):
                st.write("Worksheet:", ", ".join(source.get("worksheet_names") or []) or "—")
                st.write("Đồng bộ gần nhất:", source.get("last_sync") or "Chưa đồng bộ")
                if source.get("last_error"):
                    st.error(source["last_error"])

                c1, c2 = st.columns([1, 1])
                if c1.button(
                    "🔄 Đồng bộ ngay",
                    key=f"production_sync_{source['source_id']}",
                    type="primary",
                    disabled=not can_update,
                ):
                    try:
                        count = _sync_source(store, client, project_id, source)
                        st.success(f"Đã đồng bộ {count:,} điểm sản lượng.")
                        st.rerun()
                    except Exception as exc:
                        store.mark_error(project_id, source["source_id"], str(exc))
                        st.error(str(exc))

                if c2.button(
                    "🗑️ Xóa nguồn",
                    key=f"production_delete_{source['source_id']}",
                    disabled=not can_admin,
                ):
                    store.delete_source(project_id, source["source_id"])
                    st.rerun()

    with tab_overview:
        rows = store.current_rows(project_id)
        if not rows:
            st.info("Chưa có dữ liệu sản lượng. Vào tab Google Sheets để thêm nguồn và đồng bộ.")
        else:
            df = pd.DataFrame(rows)
            df["progress_percent"] = pd.to_numeric(
                df["progress_percent"], errors="coerce"
            ).fillna(0.0)

            avg_progress = float(df["progress_percent"].mean()) if len(df) else 0.0
            completed = int((df["progress_percent"] >= 100).sum())
            below_50 = int((df["progress_percent"] < 50).sum())
            incomplete = int((df["progress_percent"] < 100).sum())

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Điểm theo dõi", f"{len(df):,}")
            c2.metric("Tiến độ TB", f"{avg_progress:.1f}%")
            c3.metric("Đạt 100%", f"{completed:,}")
            c4.metric("Chưa hoàn thành", f"{incomplete:,}", f"Dưới 50%: {below_50:,}")

            worksheets = sorted(x for x in df["worksheet"].dropna().unique())
            zones = sorted(x for x in df["zone"].dropna().unique())
            sources = sorted(x for x in df["source_name"].dropna().unique())

            f1, f2, f3 = st.columns(3)
            source_filter = f1.multiselect("Nguồn", sources, default=sources)
            ws_filter = f2.multiselect("Tầng / worksheet", worksheets, default=worksheets)
            zone_filter = f3.multiselect("Zone", zones, default=zones)

            view = df[
                df["source_name"].isin(source_filter)
                & df["worksheet"].isin(ws_filter)
                & df["zone"].isin(zone_filter)
            ].copy()

            st.markdown("#### Bảng sản lượng chuẩn hóa")
            if view.empty:
                st.info("Không có dữ liệu phù hợp bộ lọc.")
            else:
                pivot = view.pivot_table(
                    index=["source_name", "worksheet", "work_item"],
                    columns="zone",
                    values="progress_percent",
                    aggfunc="mean",
                )
                st.dataframe(pivot.round(1), width="stretch", height=480)

                st.markdown("#### Tiến độ trung bình theo tầng / worksheet")
                by_sheet = (
                    view.groupby("worksheet", as_index=False)["progress_percent"]
                    .mean()
                    .sort_values("progress_percent")
                )
                st.bar_chart(by_sheet.set_index("worksheet")["progress_percent"])

                st.markdown("#### Công tác cần chú ý")
                attention = view[view["progress_percent"] < 100][
                    [
                        "source_name",
                        "worksheet",
                        "work_item",
                        "zone",
                        "progress_percent",
                        "synced_at",
                    ]
                ].sort_values(["progress_percent", "worksheet", "work_item"])
                st.dataframe(attention, hide_index=True, width="stretch", height=420)

    with tab_history:
        hist = store.history_rows(project_id)
        if not hist:
            st.info("Lịch sử sẽ được tạo theo ngày sau mỗi lần đồng bộ.")
        else:
            hdf = pd.DataFrame(hist)
            hdf["progress_percent"] = pd.to_numeric(
                hdf["progress_percent"], errors="coerce"
            ).fillna(0.0)
            trend = hdf.groupby("snapshot_date", as_index=False)["progress_percent"].mean()
            st.markdown("#### Xu hướng sản lượng theo ngày")
            st.line_chart(trend.set_index("snapshot_date")["progress_percent"])
            st.dataframe(hdf, hide_index=True, width="stretch", height=500)
