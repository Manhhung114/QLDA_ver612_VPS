from __future__ import annotations

from typing import Any

import pandas as pd

from qlda.application.google_sheets.service import (
    normalize_production_sheet,
    parse_sheet_gid,
    parse_spreadsheet_id,
)
from qlda.infrastructure.google_sheets.client import (
    GoogleSheetsClient,
    make_oauth_state,
    verify_oauth_state,
)
from qlda.runtime_core.production_progress import ProductionProgressStore

_PUBLIC_PREFIX = "__gid__:\"


def _actor(identity: dict[str, Any]) -> str:
    for key in ("email", "username", "user_id", "id", "name"):
        value = str(identity.get(key) or "").strip()
        if value:
            return value
    return "anonymous-qlda-user"


def _query_value(st, key: str) -> str:
    try:
        value = st.query_params.get(key, "")
    except Exception:
        return ""
    if isinstance(value, (list, tuple)):
        return str(value[0] if value else "")
    return str(value or "")


def _clear_oauth_query(st) -> None:
    for key in ("code", "state", "scope", "authuser", "prompt", "error", "error_description"):
        try:
            if key in st.query_params:
                del st.query_params[key]
        except Exception:
            pass


def _handle_oauth_callback(st, project_id: int, identity: dict[str, Any]) -> None:
    code = _query_value(st, "code")
    error = _query_value(st, "error")
    state = _query_value(st, "state")
    if not code and not error:
        return
    if error:
        _clear_oauth_query(st)
        st.session_state["production_google_oauth_flash"] = (
            "error",
            f"Google không cấp quyền: {_query_value(st, 'error_description') or error}",
        )
        st.rerun()
    try:
        payload = verify_oauth_state(state, _actor(identity))
        if int(payload.get("p") or 0) != int(project_id):
            raise ValueError("Phiên đăng nhập Google thuộc dự án khác. Hãy mở lại Sản lượng của dự án cần dùng.")
        token = GoogleSheetsClient.exchange_code(code)
        st.session_state["production_google_oauth"] = token
        st.session_state["production_google_oauth_flash"] = (
            "success",
            "Đã kết nối Google. QLDA chỉ yêu cầu quyền đọc Google Sheets.",
        )
    except Exception as exc:
        st.session_state["production_google_oauth_flash"] = ("error", str(exc))
    finally:
        _clear_oauth_query(st)
    st.rerun()


def _oauth_client(st) -> GoogleSheetsClient:
    return GoogleSheetsClient(dict(st.session_state.get("production_google_oauth") or {}))


def _save_oauth_state(st, client: GoogleSheetsClient) -> None:
    if client.authorized:
        st.session_state["production_google_oauth"] = client.token_state()


def _public_gid_token(gid: int) -> str:
    return f"__gid__:{int(gid)}"


def _token_gid(value: str) -> int | None:
    text = str(value or "")
    if not text.startswith("__gid__:"):
        return None
    tail = text.split(":", 1)[1]
    return int(tail) if tail.isdigit() else None


def _sync_source(
    store: ProductionProgressStore,
    client: GoogleSheetsClient,
    project_id: int,
    source: dict,
) -> int:
    spreadsheet_id = str(source.get("spreadsheet_id") or "")
    mode = str(source.get("data_type") or "PRODUCTION_PROGRESS").upper()
    worksheets = list(source.get("worksheet_names") or [])
    all_rows = []

    if mode == "PUBLIC_LINK":
        for token in worksheets or [_public_gid_token(0)]:
            gid = _token_gid(token)
            if gid is None:
                gid = 0
            label = str(source.get("name") or f"Sheet gid {gid}")
            values = GoogleSheetsClient.public_values(spreadsheet_id, gid)
            all_rows.extend(normalize_production_sheet(label, values))
    else:
        if not client.authorized:
            # Compatibility for sources created before OAuth/link-only support:
            # if the old source is now shared publicly, allow the first tab to sync.
            if mode == "PRODUCTION_PROGRESS":
                values = GoogleSheetsClient.public_values(spreadsheet_id, 0)
                label = str(source.get("name") or "Google Sheet")
                all_rows.extend(normalize_production_sheet(label, values))
            else:
                raise RuntimeError("Nguồn này cần đăng nhập Google trước khi đồng bộ.")
        else:
            for worksheet in worksheets:
                if _token_gid(worksheet) is not None:
                    continue
                safe_name = str(worksheet).replace("'", "''")
                values = client.values(spreadsheet_id, f"'{safe_name}'!A:ZZ")
                all_rows.extend(normalize_production_sheet(str(worksheet), values))

    store.replace_current(project_id, str(source["source_id"]), all_rows)
    return len(all_rows)


def render_production_progress(st, db, project_id: int, *, identity: dict | None = None) -> None:
    """Render production progress inside the native QLDA project navigation."""
    project_id = int(project_id)
    identity = dict(identity or {})
    role = str(identity.get("role") or "read").strip().lower()
    can_update = role in {"update", "admin"}
    can_admin = role == "admin"

    store = ProductionProgressStore(db)
    _handle_oauth_callback(st, project_id, identity)
    client = _oauth_client(st)

    st.subheader("📊 Sản lượng thi công")
    st.caption(
        "Sản lượng là một phần của QLDA. Có thể đọc Google Sheet chỉ bằng link đã chia sẻ "
        "hoặc đăng nhập Google để dùng các Sheet riêng tư; không cần Service Account."
    )

    flash = st.session_state.pop("production_google_oauth_flash", None)
    if flash:
        level, message = flash
        (st.success if level == "success" else st.error)(message)

    tab_overview, tab_sources, tab_history = st.tabs([
        "📈 Tổng quan",
        "🔗 Google Sheets",
        "🕘 Lịch sử",
    ])

    with tab_sources:
        st.markdown("#### Nguồn Google Sheets của dự án")
        st.info(
            "Cách nhanh nhất: trong Google Sheet chọn **Share → General access → Anyone with the link → Viewer**, "
            "sau đó dán link của đúng worksheet vào QLDA. Nếu Sheet không được chia sẻ công khai bằng link, "
            "hãy dùng **Đăng nhập Google** bằng tài khoản đã được cấp quyền xem file."
        )

        oauth_ready = GoogleSheetsClient.oauth_available()
        if client.authorized:
            c1, c2 = st.columns([3, 1])
            c1.success("Google đã được kết nối trong phiên QLDA hiện tại.")
            if c2.button("Đăng xuất Google", key=f"production_google_logout_{project_id}", use_container_width=True):
                st.session_state.pop("production_google_oauth", None)
                st.rerun()
        elif oauth_ready:
            try:
                state = make_oauth_state(project_id, _actor(identity))
                auth_url = GoogleSheetsClient.build_authorization_url(state)
                st.link_button(
                    "🔐 Đăng nhập Google để đọc Sheet riêng tư",
                    auth_url,
                    type="primary",
                    use_container_width=False,
                )
                st.caption("QLDA chỉ xin quyền đọc Google Sheets; không xin quyền sửa hoặc xóa file.")
            except Exception as exc:
                st.warning(str(exc))
        else:
            st.caption(
                "Chế độ dán link công khai dùng ngay, không cần cấu hình. Để bật đăng nhập Google cho Sheet riêng tư, "
                "Admin VPS cấu hình GOOGLE_OAUTH_CLIENT_ID và GOOGLE_OAUTH_CLIENT_SECRET."
            )

        if can_admin:
            access_mode = st.radio(
                "Cách kết nối",
                ["Dán link đã chia sẻ", "Đăng nhập Google"],
                horizontal=True,
                key=f"production_access_mode_{project_id}",
                help="Link đã chia sẻ không cần credential. Đăng nhập Google dùng cho file riêng tư được chia sẻ cho tài khoản Google của người dùng.",
            )

            with st.form(f"production_add_sheet_{project_id}"):
                c1, c2 = st.columns([2, 3])
                source_name = c1.text_input(
                    "Tên nguồn / khu vực",
                    placeholder="Hầm, S2, S3, S4... (có thể để trống)",
                    key=f"production_source_name_{project_id}",
                )
                sheet_url = c2.text_input(
                    "Link Google Sheet",
                    placeholder="https://docs.google.com/spreadsheets/d/.../edit#gid=...",
                    key=f"production_sheet_url_{project_id}",
                )
                test_label = "🔎 Kiểm tra link" if access_mode == "Dán link đã chia sẻ" else "🔎 Đọc danh sách worksheet"
                test = st.form_submit_button(test_label, type="primary")

            if test:
                try:
                    sid = parse_spreadsheet_id(sheet_url)
                    if access_mode == "Dán link đã chia sẻ":
                        gid = parse_sheet_gid(sheet_url)
                        label = source_name.strip() or f"Google Sheet - gid {gid}"
                        values = GoogleSheetsClient.public_values(sid, gid)
                        preview = normalize_production_sheet(label, values)
                        st.session_state[f"production_sheet_candidate_{project_id}"] = {
                            "mode": "PUBLIC_LINK",
                            "spreadsheet_id": sid,
                            "name": label,
                            "title": label,
                            "worksheets": [_public_gid_token(gid)],
                            "preview_count": len(preview),
                        }
                        st.success(f"Đọc link thành công: nhận diện {len(preview):,} điểm sản lượng.")
                    else:
                        if not client.authorized:
                            raise RuntimeError("Hãy bấm Đăng nhập Google trước khi đọc Sheet riêng tư.")
                        meta = client.metadata(sid)
                        _save_oauth_state(st, client)
                        st.session_state[f"production_sheet_candidate_{project_id}"] = {
                            "mode": "GOOGLE_OAUTH",
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
                if candidate.get("mode") == "GOOGLE_OAUTH":
                    selected_tabs = st.multiselect(
                        "Worksheet cần đồng bộ",
                        candidate.get("worksheets") or [],
                        default=candidate.get("worksheets") or [],
                        key=f"production_sheet_tabs_{project_id}",
                    )
                else:
                    selected_tabs = list(candidate.get("worksheets") or [])
                    gid = _token_gid(selected_tabs[0]) if selected_tabs else 0
                    st.caption(
                        f"Link-only sẽ đồng bộ worksheet đang mở trong link (gid={gid}). "
                        "Muốn thêm tab khác, mở tab đó trên Google Sheets và dán link thành một nguồn khác."
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
                        data_type=candidate.get("mode") or "PUBLIC_LINK",
                    )
                    st.session_state.pop(f"production_sheet_candidate_{project_id}", None)
                    st.success("Đã lưu nguồn Google Sheets vào dự án.")
                    st.rerun()
                c2.caption("Một dự án có thể khai báo nhiều Google Sheet hoặc nhiều tab của cùng một file.")
        else:
            st.caption(
                "Chỉ Admin được thêm/xóa nguồn Google Sheets. Người có quyền Cập nhật được phép đồng bộ dữ liệu."
            )

        sources = store.list_sources(project_id)
        if not sources:
            st.info("Dự án chưa có nguồn Google Sheets sản lượng.")

        for source in sources:
            mode = str(source.get("data_type") or "PRODUCTION_PROGRESS").upper()
            mode_label = {
                "PUBLIC_LINK": "Link chia sẻ",
                "GOOGLE_OAUTH": "Google login",
                "PRODUCTION_PROGRESS": "Nguồn cũ",
            }.get(mode, mode)
            label = f"{source['name']} • {source.get('spreadsheet_title') or source['spreadsheet_id']}"
            with st.expander(label, expanded=False):
                st.write("Cách truy cập:", mode_label)
                public_gids = [_token_gid(x) for x in source.get("worksheet_names") or []]
                public_gids = [x for x in public_gids if x is not None]
                if public_gids:
                    st.write("Worksheet:", ", ".join(f"gid={x}" for x in public_gids))
                else:
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
                        _save_oauth_state(st, client)
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
            df["progress_percent"] = pd.to_numeric(df["progress_percent"], errors="coerce").fillna(0.0)

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
                    ["source_name", "worksheet", "work_item", "zone", "progress_percent", "synced_at"]
                ].sort_values(["progress_percent", "worksheet", "work_item"])
                st.dataframe(attention, hide_index=True, width="stretch", height=420)

    with tab_history:
        hist = store.history_rows(project_id)
        if not hist:
            st.info("Lịch sử sẽ được tạo theo ngày sau mỗi lần đồng bộ.")
        else:
            hdf = pd.DataFrame(hist)
            hdf["progress_percent"] = pd.to_numeric(hdf["progress_percent"], errors="coerce").fillna(0.0)
            trend = hdf.groupby("snapshot_date", as_index=False)["progress_percent"].mean()
            st.markdown("#### Xu hướng sản lượng theo ngày")
            st.line_chart(trend.set_index("snapshot_date")["progress_percent"])
            st.dataframe(hdf, hide_index=True, width="stretch", height=500)
