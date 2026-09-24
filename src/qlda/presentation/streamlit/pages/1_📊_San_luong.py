from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import streamlit as st

from qlda.application.google_sheets.service import normalize_production_sheet, parse_spreadsheet_id
from qlda.infrastructure.google_sheets.client import GoogleSheetsClient
from qlda.runtime_core.bootstrap import initialize_runtime
from qlda.runtime_core.production_progress import ProductionProgressStore
from qlda.runtime_core.project_store import CloudDatabase

initialize_runtime()
st.set_page_config(page_title="Sản lượng thi công", page_icon="📊", layout="wide")

DATA_DIR = Path(os.environ.get("QLDA_RUNTIME_DATA_ROOT", "/opt/qlda/data/runtime"))
DEFAULT_DB_PATH = DATA_DIR / "qlda_cloud.db"
DB_PATH = Path(os.environ.get("QLDA_DB_PATH", str(DEFAULT_DB_PATH)))


@st.cache_resource
def _db(path: str):
    return CloudDatabase(path)


def _project_rows(db):
    try:
        return list(db.projects())
    except Exception:
        return []


def _sync_source(store: ProductionProgressStore, client: GoogleSheetsClient, project_id: int, source: dict) -> int:
    spreadsheet_id = str(source.get("spreadsheet_id") or "")
    all_rows = []
    for worksheet in list(source.get("worksheet_names") or []):
        safe_name = str(worksheet).replace("'", "''")
        values = client.values(spreadsheet_id, f"'{safe_name}'!A:ZZ")
        all_rows.extend(normalize_production_sheet(str(worksheet), values))
    store.replace_current(project_id, str(source["source_id"]), all_rows)
    return len(all_rows)


db = _db(str(DB_PATH))
store = ProductionProgressStore(db)
projects = _project_rows(db)

st.title("📊 Sản lượng thi công")
st.caption("Google Sheets Data Hub: 1 dự án → nhiều Google Sheets → nhiều worksheet → dữ liệu sản lượng thống nhất.")

if not projects:
    st.info("Chưa có dự án để cấu hình nguồn dữ liệu.")
    st.stop()

project_options = [int(x["id"]) for x in projects]
project_map = {int(x["id"]): x for x in projects}
project_id = st.selectbox(
    "Dự án",
    project_options,
    format_func=lambda x: f"{project_map[x]['code']} - {project_map[x]['name']}",
)

client = GoogleSheetsClient()
tab_overview, tab_sources, tab_history = st.tabs(["📈 Tổng quan", "🔗 Google Sheets", "🕘 Lịch sử"])

with tab_sources:
    st.subheader("Nguồn Google Sheets")
    try:
        st.info(f"Chia sẻ các Google Sheet cần đọc cho service account: **{client.service_account_email}** với quyền Viewer.")
    except Exception as exc:
        st.warning(str(exc))

    with st.form(f"add_sheet_{project_id}"):
        c1, c2 = st.columns([2, 3])
        source_name = c1.text_input("Tên nguồn", placeholder="Theo dõi sản lượng MEP")
        sheet_url = c2.text_input("Link Google Sheet / Spreadsheet ID")
        test = st.form_submit_button("🔎 Đọc danh sách worksheet", type="primary")
    if test:
        try:
            sid = parse_spreadsheet_id(sheet_url)
            meta = client.metadata(sid)
            st.session_state[f"sheet_candidate_{project_id}"] = {
                "spreadsheet_id": sid,
                "name": source_name.strip() or meta.get("title") or "Google Sheet",
                "title": meta.get("title") or "",
                "worksheets": [x["title"] for x in meta.get("sheets", []) if not x.get("hidden")],
            }
            st.success(f"Đã kết nối: {meta.get('title') or sid}")
        except Exception as exc:
            st.error(str(exc))

    candidate = st.session_state.get(f"sheet_candidate_{project_id}")
    if candidate:
        selected_tabs = st.multiselect(
            "Worksheet cần đồng bộ",
            candidate.get("worksheets") or [],
            default=candidate.get("worksheets") or [],
            key=f"sheet_tabs_{project_id}",
        )
        c1, c2 = st.columns([1, 4])
        if c1.button("💾 Lưu nguồn", type="primary", disabled=not bool(selected_tabs), key=f"save_sheet_{project_id}"):
            store.save_source(
                project_id,
                name=candidate["name"],
                spreadsheet_id=candidate["spreadsheet_id"],
                spreadsheet_title=candidate["title"],
                worksheet_names=selected_tabs,
            )
            st.session_state.pop(f"sheet_candidate_{project_id}", None)
            st.success("Đã lưu nguồn Google Sheets.")
            st.rerun()
        c2.caption("Có thể chọn Hầm, S2, S3, S4…; worksheet mới có thể bổ sung sau mà không thay cấu trúc dữ liệu.")

    sources = store.list_sources(project_id)
    if not sources:
        st.info("Dự án chưa có nguồn Google Sheets.")
    for source in sources:
        with st.expander(f"{source['name']} • {source.get('spreadsheet_title') or source['spreadsheet_id']}", expanded=False):
            st.write("Worksheet:", ", ".join(source.get("worksheet_names") or []) or "—")
            st.write("Đồng bộ gần nhất:", source.get("last_sync") or "Chưa đồng bộ")
            if source.get("last_error"):
                st.error(source["last_error"])
            c1, c2 = st.columns([1, 1])
            if c1.button("🔄 Đồng bộ ngay", key=f"sync_{source['source_id']}", type="primary"):
                try:
                    count = _sync_source(store, client, project_id, source)
                    st.success(f"Đã đồng bộ {count:,} điểm sản lượng.")
                    st.rerun()
                except Exception as exc:
                    store.mark_error(project_id, source["source_id"], str(exc))
                    st.error(str(exc))
            if c2.button("🗑️ Xóa nguồn", key=f"del_{source['source_id']}"):
                store.delete_source(project_id, source["source_id"])
                st.rerun()

with tab_overview:
    rows = store.current_rows(project_id)
    if not rows:
        st.info("Chưa có dữ liệu sản lượng. Hãy thêm nguồn Google Sheets và bấm Đồng bộ ngay.")
    else:
        df = pd.DataFrame(rows)
        df["progress_percent"] = pd.to_numeric(df["progress_percent"], errors="coerce").fillna(0.0)
        avg_progress = float(df["progress_percent"].mean()) if len(df) else 0.0
        completed = int((df["progress_percent"] >= 100).sum())
        delayed = int((df["progress_percent"] < 50).sum())
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Điểm theo dõi", f"{len(df):,}")
        c2.metric("Tiến độ TB", f"{avg_progress:.1f}%")
        c3.metric("Đạt 100%", f"{completed:,}")
        c4.metric("Dưới 50%", f"{delayed:,}")

        worksheets = sorted(x for x in df["worksheet"].dropna().unique())
        zones = sorted(x for x in df["zone"].dropna().unique())
        f1, f2 = st.columns(2)
        ws_filter = f1.multiselect("Tầng / worksheet", worksheets, default=worksheets)
        zone_filter = f2.multiselect("Zone", zones, default=zones)
        view = df[df["worksheet"].isin(ws_filter) & df["zone"].isin(zone_filter)].copy()

        pivot = view.pivot_table(index="work_item", columns="zone", values="progress_percent", aggfunc="mean")
        st.markdown("#### Bảng sản lượng chuẩn hóa")
        st.dataframe(pivot.round(1), use_container_width=True, height=480)

        by_sheet = view.groupby("worksheet", as_index=False)["progress_percent"].mean().sort_values("progress_percent")
        st.markdown("#### Tiến độ trung bình theo tầng / worksheet")
        st.bar_chart(by_sheet.set_index("worksheet")["progress_percent"])

        st.markdown("#### Các công tác cần chú ý")
        attention = view[view["progress_percent"] < 100][
            ["worksheet", "work_item", "zone", "progress_percent", "source_name", "synced_at"]
        ].sort_values(["progress_percent", "worksheet", "work_item"])
        st.dataframe(attention, hide_index=True, use_container_width=True, height=420)

with tab_history:
    hist = store.history_rows(project_id)
    if not hist:
        st.info("Lịch sử sẽ được tạo theo ngày sau mỗi lần đồng bộ.")
    else:
        hdf = pd.DataFrame(hist)
        hdf["progress_percent"] = pd.to_numeric(hdf["progress_percent"], errors="coerce").fillna(0.0)
        trend = hdf.groupby("snapshot_date", as_index=False)["progress_percent"].mean()
        st.line_chart(trend.set_index("snapshot_date")["progress_percent"])
        st.dataframe(hdf, hide_index=True, use_container_width=True, height=500)
