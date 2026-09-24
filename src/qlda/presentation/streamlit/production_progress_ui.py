from __future__ import annotations

from typing import Any

import pandas as pd

from qlda.application.contractor_data_hub import ContractorDataHubAI, ContractorDataHubService
from qlda.application.google_sheets.service import parse_sheet_gid, parse_spreadsheet_id
from qlda.infrastructure.google_sheets.client import make_oauth_state, verify_oauth_state
from qlda.infrastructure.google_sheets.drive import (
    GoogleWorkspaceClient,
    parse_drive_file_id,
    parse_drive_folder_id,
)
from qlda.runtime_core.contractor_access_control import authorized_contractor_rows
from qlda.runtime_core.google_connection_store import (
    delete_project_connection,
    load_project_connection,
    save_project_connection,
)
from qlda.runtime_core.google_oauth_settings import apply_to_environment


CATEGORY_OPTIONS = {
    "AUTO": "Tự nhận diện / AI đọc toàn bộ",
    "PRODUCTION": "Sản lượng thi công",
    "SCHEDULE": "Tiến độ",
    "BOQ": "BOQ / khối lượng",
    "IPC": "IPC / thanh toán",
    "VO": "Phát sinh VO",
    "MATERIAL": "Vật tư / thiết bị",
    "DOCUMENT": "Hồ sơ / biên bản",
    "OTHER": "Dữ liệu khác",
}
SOURCE_KIND_OPTIONS = {
    "SHEET": "Google Sheet",
    "FOLDER": "Google Drive Folder",
    "DRIVE_FILE": "Một file Google Drive",
}


def _actor(identity: dict[str, Any]) -> str:
    for key in ("email", "username", "user_id", "id", "name"):
        value = str(identity.get(key) or "").strip()
        if value:
            return value
    return "anonymous-qlda-user"


def _approval_role(identity: dict[str, Any]) -> str:
    value = str(
        identity.get("approval_role")
        or identity.get("approval_group")
        or ""
    ).strip().upper().replace("-", "_").replace(" ", "_")
    aliases = {
        "CONTRACTOR": "CONTRACTOR",
        "NHA_THAU": "CONTRACTOR",
        "NHÀ_THAU": "CONTRACTOR",
        "NHÀ_THẦU": "CONTRACTOR",
        "PROJECT_VIEWER": "PROJECT_VIEWER",
    }
    return aliases.get(value, value)


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


def _session_token_key(project_id: int) -> str:
    return f"contractor_data_google_oauth_{int(project_id)}"


def _connection_flash_key(project_id: int) -> str:
    return f"contractor_data_google_flash_{int(project_id)}"


def _stored_connection(project_id: int) -> dict[str, Any]:
    try:
        return load_project_connection(int(project_id))
    except Exception:
        return {}


def _google_client(st, project_id: int) -> tuple[GoogleWorkspaceClient, dict[str, Any]]:
    stored = _stored_connection(project_id)
    session_state = dict(st.session_state.get(_session_token_key(project_id)) or {})
    token_state = session_state or dict(stored.get("token_state") or {})
    return GoogleWorkspaceClient(token_state), stored


def _persist_google_client(
    st,
    project_id: int,
    client: GoogleWorkspaceClient,
    *,
    stored: dict[str, Any] | None = None,
    profile: dict[str, Any] | None = None,
) -> None:
    if not client.authorized:
        return
    state = client.token_state()
    st.session_state[_session_token_key(project_id)] = state
    old = dict(stored or _stored_connection(project_id))
    profile = dict(profile or {})
    try:
        save_project_connection(
            int(project_id),
            state,
            account_email=str(profile.get("email") or old.get("account_email") or ""),
            account_name=str(profile.get("name") or old.get("account_name") or ""),
            scopes=[GoogleWorkspaceClient.SHEETS_SCOPE, GoogleWorkspaceClient.DRIVE_SCOPE],
        )
    except Exception:
        # Session OAuth remains usable even when the VPS encryption key is not
        # available; the UI will surface persistence status separately.
        pass


def _handle_oauth_callback(st, project_id: int, identity: dict[str, Any]) -> None:
    code = _query_value(st, "code")
    error = _query_value(st, "error")
    state = _query_value(st, "state")
    if not code and not error:
        return

    if error:
        description = _query_value(st, "error_description") or error
        _clear_oauth_query(st)
        st.session_state[_connection_flash_key(project_id)] = (
            "error",
            f"Google không cấp quyền: {description}",
        )
        st.rerun()

    try:
        payload = verify_oauth_state(state, _actor(identity))
        if int(payload.get("p") or 0) != int(project_id):
            raise ValueError("Phiên Google OAuth thuộc dự án khác.")
        token = GoogleWorkspaceClient.exchange_code(code)
        client = GoogleWorkspaceClient(token)
        st.session_state[_session_token_key(project_id)] = client.token_state()
        profile: dict[str, Any] = {}
        try:
            profile = client.account_profile()
        except Exception:
            profile = {}
        try:
            save_project_connection(
                int(project_id),
                client.token_state(),
                account_email=str(profile.get("email") or ""),
                account_name=str(profile.get("name") or ""),
                scopes=[GoogleWorkspaceClient.SHEETS_SCOPE, GoogleWorkspaceClient.DRIVE_SCOPE],
            )
            persist_note = " Kết nối đã được lưu mã hóa để worker tự đồng bộ."
        except Exception as exc:
            persist_note = f" Phiên hiện tại đã kết nối nhưng chưa lưu được cho worker: {exc}"
        st.session_state[_connection_flash_key(project_id)] = (
            "success",
            "Đã kết nối Google read-only." + persist_note,
        )
    except Exception as exc:
        st.session_state[_connection_flash_key(project_id)] = ("error", str(exc))
    finally:
        _clear_oauth_query(st)
    st.rerun()


def _authorized_contractors(db, project_id: int, identity: dict[str, Any]) -> list[dict[str, Any]]:
    email = str(identity.get("email") or "").strip().lower()
    role = _approval_role(identity)
    return [
        dict(row)
        for row in authorized_contractor_rows(
            db,
            int(project_id),
            email,
            role,
            active_only=True,
        )
    ]


def _contractor_label(row: dict[str, Any]) -> str:
    code = str(row.get("contractor_code") or "").strip()
    name = str(row.get("contractor_name") or "").strip()
    return f"{code} - {name}".strip(" -") or f"Nhà thầu #{row.get('id','')}"


def _space_by_contractor(spaces: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    return {int(x.get("contractor_id") or 0): dict(x) for x in spaces}


def _workspace_map(contractors: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    return {
        int(x.get("workspace_project_id") or 0): dict(x)
        for x in contractors
        if int(x.get("workspace_project_id") or 0) > 0
    }


def _render_google_connection(
    st,
    project_id: int,
    identity: dict[str, Any],
    client: GoogleWorkspaceClient,
    stored: dict[str, Any],
    *,
    can_admin: bool,
) -> None:
    st.markdown("#### 🔐 Kết nối Google của dự án")
    st.caption(
        "Nhà thầu vẫn giữ Owner. QLDA dùng Gmail đã được nhà thầu cấp quyền Viewer và chỉ yêu cầu "
        "Google Sheets + Google Drive ở chế độ read-only."
    )

    if client.authorized:
        account = str(stored.get("account_email") or "").strip()
        scopes = set(stored.get("scopes") or [])
        c1, c2, c3 = st.columns([2.4, 1, 1])
        c1.success(f"✅ Đã kết nối Google{f': {account}' if account else ''}")
        if GoogleWorkspaceClient.DRIVE_SCOPE not in scopes and stored:
            c1.warning("Kết nối cũ có thể chưa có quyền Drive read-only. Hãy Kết nối lại Google một lần.")
        if c2.button("🧪 Kiểm tra", key=f"cdh_google_test_{project_id}", use_container_width=True):
            try:
                profile = client.account_profile()
                _persist_google_client(st, project_id, client, stored=stored, profile=profile)
                st.success(f"Google OK: {profile.get('email') or 'đã xác thực'}")
            except Exception as exc:
                st.error(str(exc))
        if c3.button(
            "Ngắt kết nối",
            key=f"cdh_google_disconnect_{project_id}",
            disabled=not can_admin,
            use_container_width=True,
        ):
            st.session_state.pop(_session_token_key(project_id), None)
            delete_project_connection(project_id)
            st.success("Đã xóa kết nối Google đã lưu của dự án.")
            st.rerun()
    else:
        st.warning("Chưa có Gmail Google được kết nối lâu dài cho dự án này.")

    if GoogleWorkspaceClient.oauth_available():
        try:
            state = make_oauth_state(int(project_id), _actor(identity))
            auth_url = GoogleWorkspaceClient.build_authorization_url(state)
            label = "🔄 KẾT NỐI LẠI GOOGLE" if client.authorized else "🔐 ĐĂNG NHẬP GOOGLE"
            st.link_button(label, auth_url, type="primary", use_container_width=False)
            st.caption(
                "Lần đầu hoặc sau khi nâng cấp Data Hub, hãy kết nối lại để cấp thêm quyền Google Drive read-only."
            )
        except Exception as exc:
            st.error(f"Không tạo được liên kết Google OAuth: {exc}")
    else:
        st.error("Google OAuth Client chưa được cấu hình.")
        st.info("Admin vào **Công cụ → Hệ thống → 🔐 Google OAuth** để nhập Client ID / Client Secret / Redirect URI.")


def _render_overview(
    st,
    service: ContractorDataHubService,
    project_id: int,
    contractors: list[dict[str, Any]],
    workspace_ids: list[int],
) -> None:
    repo = service.repo
    metrics = repo.project_metrics(project_id, workspace_ids=workspace_ids)
    records = repo.records(project_id, workspace_ids=workspace_ids, limit=20000)
    production = [x for x in records if str(x.get("record_type") or "") == "PRODUCTION"]

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

    if not production:
        st.info("Chưa có dữ liệu sản lượng chuẩn hóa. Đồng bộ Sheet/Drive của nhà thầu để tạo dashboard.")
        return

    by_workspace = _workspace_map(contractors)
    rows = []
    for item in production:
        contractor = by_workspace.get(int(item.get("workspace_project_id") or 0), {})
        rows.append({
            "Nhà thầu": _contractor_label(contractor),
            "Nguồn": item.get("source_name") or "",
            "Worksheet": item.get("worksheet") or "",
            "Công tác": item.get("work_item") or "",
            "Zone": item.get("zone") or "",
            "Tiến độ (%)": float(item.get("progress_percent") or 0),
            "Sync": item.get("synced_at") or "",
        })
    df = pd.DataFrame(rows)
    contractors_filter = sorted(x for x in df["Nhà thầu"].dropna().unique() if x)
    worksheets = sorted(x for x in df["Worksheet"].dropna().unique() if x)
    zones = sorted(x for x in df["Zone"].dropna().unique() if x)
    f1, f2, f3 = st.columns(3)
    selected_contractors = f1.multiselect("Nhà thầu", contractors_filter, default=contractors_filter)
    selected_ws = f2.multiselect("Tầng / worksheet", worksheets, default=worksheets)
    selected_zones = f3.multiselect("Zone", zones, default=zones)
    view = df[
        df["Nhà thầu"].isin(selected_contractors)
        & df["Worksheet"].isin(selected_ws)
        & df["Zone"].isin(selected_zones)
    ].copy()

    st.markdown("#### Bảng sản lượng chuẩn hóa")
    if view.empty:
        st.info("Không có dữ liệu phù hợp bộ lọc.")
        return
    pivot = view.pivot_table(
        index=["Nhà thầu", "Worksheet", "Công tác"],
        columns="Zone",
        values="Tiến độ (%)",
        aggfunc="mean",
    )
    st.dataframe(pivot.round(1), use_container_width=True, height=480)

    st.markdown("#### Tiến độ trung bình theo nhà thầu")
    by_contractor = view.groupby("Nhà thầu", as_index=False)["Tiến độ (%)"].mean()
    st.bar_chart(by_contractor.set_index("Nhà thầu")["Tiến độ (%)"])

    st.markdown("#### Công tác cần chú ý")
    attention = view[view["Tiến độ (%)"] < 100].sort_values(
        ["Tiến độ (%)", "Nhà thầu", "Worksheet", "Công tác"]
    )
    st.dataframe(attention, hide_index=True, use_container_width=True, height=420)


def _render_data_spaces(
    st,
    service: ContractorDataHubService,
    project_id: int,
    contractors: list[dict[str, Any]],
    spaces: list[dict[str, Any]],
    *,
    can_update: bool,
) -> None:
    repo = service.repo
    by_contractor = _space_by_contractor(spaces)
    st.markdown("#### 🏢 Kho dữ liệu riêng theo nhà thầu")
    st.caption(
        "Mỗi nhà thầu có một Data Space logic riêng. PostgreSQL vẫn dùng chung hạ tầng nhưng mọi record đều gắn "
        "contractor_id + workspace_project_id nên AI/RBAC không trộn phạm vi."
    )

    for contractor in contractors:
        cid = int(contractor.get("id") or 0)
        space = by_contractor.get(cid, {})
        if not space:
            continue
        label = _contractor_label(contractor)
        sources = repo.list_sources(str(space["data_space_id"]))
        records = repo.records(
            project_id,
            workspace_ids=[int(contractor.get("workspace_project_id") or 0)],
            limit=20000,
        )
        categories: dict[str, int] = {}
        for row in records:
            key = str(row.get("category") or "AUTO")
            categories[key] = categories.get(key, 0) + 1

        with st.expander(f"{label} · {len(sources)} nguồn · {len(records):,} records", expanded=False):
            c1, c2, c3 = st.columns(3)
            c1.metric("Nguồn", len(sources))
            c2.metric("Records", f"{len(records):,}")
            c3.metric("Sync gần nhất", str(space.get("last_sync") or "Chưa sync"))
            if categories:
                st.caption("Phân loại dữ liệu: " + " · ".join(f"{k}: {v:,}" for k, v in sorted(categories.items())))
            if space.get("last_error"):
                st.error(str(space.get("last_error")))

            interval = st.number_input(
                "Chu kỳ tự đồng bộ (phút)",
                min_value=15,
                max_value=1440,
                value=max(15, int(space.get("sync_interval_minutes") or 60)),
                step=15,
                key=f"cdh_interval_{space['data_space_id']}",
                disabled=not can_update,
            )
            a1, a2 = st.columns([1, 2])
            if a1.button(
                "💾 Lưu chu kỳ",
                key=f"cdh_interval_save_{space['data_space_id']}",
                disabled=not can_update,
                use_container_width=True,
            ):
                repo.update_space(
                    str(space["data_space_id"]), sync_interval_minutes=int(interval)
                )
                st.success("Đã lưu chu kỳ tự đồng bộ.")
                st.rerun()
            if a2.button(
                "🔄 Đồng bộ kho này ngay",
                key=f"cdh_space_sync_{space['data_space_id']}",
                disabled=not can_update,
                use_container_width=True,
            ):
                try:
                    with st.spinner(f"Đang đồng bộ {label}..."):
                        result = service.sync_space(str(space["data_space_id"]), trigger_type="MANUAL")
                    if result.get("errors"):
                        st.warning(
                            f"Đã xử lý {result.get('sources',0)} nguồn; lỗi {result.get('errors',0)}. "
                            + " | ".join(result.get("messages") or [])
                        )
                    else:
                        st.success(
                            f"Đồng bộ xong {result.get('records',0):,} records; "
                            f"{result.get('production_points',0):,} điểm sản lượng."
                        )
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))


def _render_sources(
    st,
    service: ContractorDataHubService,
    project_id: int,
    identity: dict[str, Any],
    contractors: list[dict[str, Any]],
    spaces: list[dict[str, Any]],
    client: GoogleWorkspaceClient,
    stored: dict[str, Any],
    *,
    can_update: bool,
    can_admin: bool,
) -> None:
    repo = service.repo
    _render_google_connection(
        st, project_id, identity, client, stored, can_admin=can_admin
    )
    st.divider()

    by_contractor = _space_by_contractor(spaces)
    contractor_ids = [int(x.get("id") or 0) for x in contractors]
    contractor_lookup = {int(x.get("id") or 0): x for x in contractors}

    st.markdown("#### ➕ Thêm nguồn vào kho nhà thầu")
    if not can_update:
        st.info("Tài khoản hiện tại chỉ có quyền xem dữ liệu nguồn.")
    else:
        with st.form(f"cdh_add_source_{project_id}"):
            contractor_id = st.selectbox(
                "Nhà thầu / kho dữ liệu",
                contractor_ids,
                format_func=lambda cid: _contractor_label(contractor_lookup[cid]),
                key=f"cdh_source_contractor_{project_id}",
            )
            c1, c2 = st.columns(2)
            kind = c1.selectbox(
                "Loại nguồn",
                list(SOURCE_KIND_OPTIONS),
                format_func=lambda x: SOURCE_KIND_OPTIONS[x],
                key=f"cdh_source_kind_{project_id}",
            )
            category = c2.selectbox(
                "Loại dữ liệu",
                list(CATEGORY_OPTIONS),
                format_func=lambda x: CATEGORY_OPTIONS[x],
                key=f"cdh_source_category_{project_id}",
            )
            source_name = st.text_input(
                "Tên nguồn",
                placeholder="Ví dụ: SME - Theo dõi sản lượng / Hồ sơ nghiệm thu / BOQ",
                key=f"cdh_source_name_{project_id}",
            )
            source_url = st.text_input(
                "Link Google Sheet / Drive Folder / Drive File",
                placeholder="https://docs.google.com/... hoặc https://drive.google.com/...",
                key=f"cdh_source_url_{project_id}",
            )
            public_link = False
            if kind == "SHEET":
                public_link = st.checkbox(
                    "Sheet công khai bằng link (không dùng Gmail OAuth)",
                    value=False,
                    key=f"cdh_public_link_{project_id}",
                    help="Với Sheet nhà thầu đã chia sẻ cho Gmail QLDA, không tick mục này.",
                )
            submitted = st.form_submit_button("💾 Lưu nguồn dữ liệu", type="primary")

        if submitted:
            try:
                contractor = contractor_lookup[int(contractor_id)]
                space = by_contractor[int(contractor_id)]
                url = str(source_url or "").strip()
                name = str(source_name or "").strip()
                worksheets: list[str] = []
                access_mode = "GOOGLE_OAUTH"
                if kind == "SHEET":
                    external_id = parse_spreadsheet_id(url)
                    if public_link:
                        access_mode = "PUBLIC_LINK"
                        worksheets = [f"__gid__:{parse_sheet_gid(url)}"]
                    elif not client.authorized:
                        raise ValueError("Hãy Đăng nhập Google trước khi lưu Sheet riêng tư.")
                    if not name:
                        name = f"Google Sheet - {external_id[:10]}"
                elif kind == "FOLDER":
                    if not client.authorized:
                        raise ValueError("Hãy Đăng nhập Google trước khi lưu Drive Folder.")
                    external_id = parse_drive_folder_id(url)
                    if not name:
                        name = f"Drive Folder - {external_id[:10]}"
                else:
                    if not client.authorized:
                        raise ValueError("Hãy Đăng nhập Google trước khi lưu Drive File.")
                    external_id = parse_drive_file_id(url)
                    if not name:
                        name = f"Drive File - {external_id[:10]}"

                source_id = repo.save_source(
                    str(space["data_space_id"]),
                    source_kind=kind,
                    name=name,
                    external_id=external_id,
                    source_url=url,
                    category=category,
                    access_mode=access_mode,
                    worksheet_names=worksheets,
                    discover_children=(kind == "FOLDER"),
                )
                if kind == "FOLDER":
                    repo.update_space(
                        str(space["data_space_id"]),
                        drive_folder_id=external_id,
                        drive_folder_url=url,
                    )
                st.success(f"Đã lưu nguồn vào kho {_contractor_label(contractor)}.")
                # Sync immediately so the source is validated and AI can use it.
                source = next(
                    (x for x in repo.list_sources(str(space["data_space_id"])) if x.get("source_id") == source_id),
                    {},
                )
                if source:
                    with st.spinner("Đang kiểm tra và đồng bộ nguồn lần đầu..."):
                        service.sync_source(source, trigger_type="INITIAL")
                    _persist_google_client(st, project_id, client, stored=stored)
                st.rerun()
            except Exception as exc:
                st.error(str(exc))

    st.divider()
    st.markdown("#### 📚 Nguồn đang theo dõi")
    if can_update:
        if st.button(
            "🔄 Đồng bộ tất cả kho được phép xem",
            type="primary",
            key=f"cdh_sync_all_{project_id}",
        ):
            try:
                workspace_ids = [int(x.get("workspace_project_id") or 0) for x in contractors]
                with st.spinner("Đang rà soát toàn bộ kho dữ liệu nhà thầu..."):
                    result = service.sync_project(
                        project_id, workspace_ids=workspace_ids, trigger_type="MANUAL_ALL"
                    )
                _persist_google_client(st, project_id, client, stored=stored)
                if result.get("errors"):
                    st.warning(
                        f"Đồng bộ xong với {result.get('errors')} kho có lỗi; "
                        f"{result.get('records',0):,} records được cập nhật."
                    )
                else:
                    st.success(
                        f"Đồng bộ hoàn tất {result.get('spaces',0)} kho · "
                        f"{result.get('files',0):,} file · {result.get('records',0):,} records."
                    )
                st.rerun()
            except Exception as exc:
                st.error(str(exc))

    any_source = False
    for contractor in contractors:
        space = by_contractor.get(int(contractor.get("id") or 0), {})
        if not space:
            continue
        sources = repo.list_sources(str(space["data_space_id"]))
        if not sources:
            continue
        any_source = True
        st.markdown(f"##### {_contractor_label(contractor)}")
        for source in sources:
            kind_label = SOURCE_KIND_OPTIONS.get(str(source.get("source_kind") or ""), str(source.get("source_kind") or ""))
            cat_label = CATEGORY_OPTIONS.get(str(source.get("category") or ""), str(source.get("category") or ""))
            with st.expander(
                f"{source.get('name','')} · {kind_label} · {cat_label}",
                expanded=False,
            ):
                st.write("Đường dẫn:", source.get("source_url") or source.get("external_id") or "—")
                st.write("Truy cập:", "Link công khai" if source.get("access_mode") == "PUBLIC_LINK" else "Google OAuth read-only")
                st.write("Đồng bộ gần nhất:", source.get("last_sync") or "Chưa đồng bộ")
                if source.get("last_error"):
                    st.error(str(source.get("last_error")))
                b1, b2 = st.columns(2)
                if b1.button(
                    "🔄 Đồng bộ nguồn",
                    key=f"cdh_sync_source_{source['source_id']}",
                    disabled=not can_update,
                    use_container_width=True,
                ):
                    try:
                        with st.spinner("Đang đọc nguồn Google..."):
                            result = service.sync_source(source, trigger_type="MANUAL")
                        _persist_google_client(st, project_id, client, stored=stored)
                        st.success(
                            f"Đã ghi {result.get('records',0):,} records; "
                            f"{result.get('production_points',0):,} điểm sản lượng."
                        )
                        st.rerun()
                    except Exception as exc:
                        st.error(str(exc))
                if b2.button(
                    "🗑️ Xóa nguồn",
                    key=f"cdh_delete_source_{source['source_id']}",
                    disabled=not can_update,
                    use_container_width=True,
                ):
                    repo.delete_source(str(source["source_id"]))
                    st.rerun()
    if not any_source:
        st.info("Chưa có nguồn dữ liệu nào trong các kho được phép xem.")


def _render_history(
    st,
    service: ContractorDataHubService,
    project_id: int,
    workspace_ids: list[int],
) -> None:
    repo = service.repo
    st.markdown("#### 🕘 Lịch sử đồng bộ / snapshot")
    runs = repo.sync_runs(project_id, limit=300)
    if runs:
        df = pd.DataFrame(runs)
        visible = [
            x for x in [
                "started_at", "finished_at", "contractor_code", "contractor_name",
                "source_name", "trigger_type", "status", "discovered_files",
                "records_written", "production_points", "message",
            ] if x in df.columns
        ]
        st.dataframe(df[visible], hide_index=True, use_container_width=True, height=360)
    else:
        st.info("Chưa có lịch sử đồng bộ.")

    snapshots = repo.snapshots(project_id, limit=300)
    allowed = set(workspace_ids)
    snapshots = [x for x in snapshots if int(x.get("workspace_project_id") or 0) in allowed]
    st.markdown("#### Snapshot thay đổi")
    if snapshots:
        sdf = pd.DataFrame(snapshots)
        visible = [
            x for x in [
                "captured_at", "contractor_code", "contractor_name", "source_name",
                "category", "item_count", "checksum",
            ] if x in sdf.columns
        ]
        if "checksum" in visible:
            sdf["checksum"] = sdf["checksum"].astype(str).str[:12]
        st.dataframe(sdf[visible], hide_index=True, use_container_width=True, height=360)
    else:
        st.info("Snapshot được tạo khi nội dung nguồn thay đổi.")


def _render_ai(
    st,
    db,
    project_id: int,
    contractors: list[dict[str, Any]],
    identity: dict[str, Any],
) -> None:
    st.markdown("#### 🤖 AI rà soát kho dữ liệu nhà thầu")
    st.caption(
        "AI chỉ nhận context đã truy xuất từ PostgreSQL. Dữ liệu vẫn giữ ranh giới nhà thầu; tài khoản Nhà thầu "
        "không thể dùng AI để đọc kho của nhà thầu khác."
    )
    workspace_lookup = {
        int(x.get("workspace_project_id") or 0): x
        for x in contractors
        if int(x.get("workspace_project_id") or 0) > 0
    }
    workspace_ids = list(workspace_lookup)
    restricted = _approval_role(identity) == "CONTRACTOR"

    options: list[str | int] = []
    if not restricted and len(workspace_ids) > 1:
        options.append("ALL")
    options.extend(workspace_ids)
    selected = st.selectbox(
        "Phạm vi AI",
        options,
        format_func=lambda x: (
            "🌐 Toàn dự án / tất cả nhà thầu được phép xem"
            if x == "ALL"
            else f"🏢 {_contractor_label(workspace_lookup[int(x)])}"
        ),
        key=f"cdh_ai_scope_{project_id}",
    )
    scope = workspace_ids if selected == "ALL" else [int(selected)]

    examples = [
        "Nhà thầu nào có dữ liệu sản lượng thấp nhất và các Zone cần chú ý?",
        "Những nguồn dữ liệu nào chưa cập nhật trong 24 giờ?",
        "Tổng hợp các công tác chưa đạt 100% theo từng nhà thầu.",
        "Rà soát toàn bộ kho và chỉ ra dữ liệu mâu thuẫn, thiếu hoặc có lỗi đồng bộ.",
    ]
    prompt = st.text_area(
        "Câu hỏi",
        placeholder="Ví dụ: SME còn những Zone nào dưới 80%?",
        height=120,
        key=f"cdh_ai_question_{project_id}",
    )
    with st.expander("Gợi ý câu hỏi", expanded=False):
        for item in examples:
            st.markdown(f"- {item}")

    if st.button("🔎 AI rà soát dữ liệu", type="primary", key=f"cdh_ai_run_{project_id}"):
        if not str(prompt or "").strip():
            st.warning("Nhập câu hỏi trước khi chạy AI.")
        else:
            try:
                with st.spinner("AI đang truy xuất và rà soát kho dữ liệu được phép xem..."):
                    answer = ContractorDataHubAI(db).answer(
                        project_id,
                        str(prompt),
                        workspace_ids=scope,
                    )
                st.session_state[f"cdh_ai_answer_{project_id}"] = answer
            except Exception as exc:
                st.error(str(exc))

    answer = st.session_state.get(f"cdh_ai_answer_{project_id}")
    if answer:
        st.markdown("#### Kết quả")
        st.markdown(str(answer))


def render_production_progress(st, db, project_id: int, *, identity: dict | None = None) -> None:
    """V1-V5 Contractor Data Hub inside native QLDA navigation.

    V1 Contractor Data Space
    V2 Google Sheets / Drive read-only connectors
    V3 Persistent snapshots + scheduled sync data model
    V4 Contractor-scoped AI retrieval
    V5 Project-wide AI retrieval with existing QLDA contractor RBAC
    """
    project_id = int(project_id)
    identity = dict(identity or {})
    role = str(identity.get("role") or "read").strip().lower()
    can_update = role in {"update", "admin"}
    can_admin = role == "admin"

    # Admin-managed Google OAuth survives service restarts without qlda.env.
    try:
        apply_to_environment()
    except Exception:
        pass

    _handle_oauth_callback(st, project_id, identity)

    try:
        contractors = _authorized_contractors(db, project_id, identity)
    except PermissionError as exc:
        st.error(str(exc))
        return
    except Exception as exc:
        st.error(f"Không xác định được phạm vi nhà thầu: {exc}")
        return
    if not contractors:
        st.info("Dự án chưa có nhà thầu đang hoạt động trong phạm vi tài khoản này.")
        return

    client, stored = _google_client(st, project_id)
    service = ContractorDataHubService(db, client=client)
    try:
        spaces = service.ensure_spaces(project_id, contractors)
    except Exception as exc:
        st.error(f"Không khởi tạo được kho dữ liệu nhà thầu: {exc}")
        return

    workspace_ids = [
        int(x.get("workspace_project_id") or 0)
        for x in contractors
        if int(x.get("workspace_project_id") or 0) > 0
    ]

    st.subheader("📊 Sản lượng & Kho dữ liệu nhà thầu")
    st.caption(
        "Mỗi nhà thầu là một kho dữ liệu riêng. QLDA đồng bộ dữ liệu Google ở chế độ read-only, lưu snapshot lịch sử "
        "và cho AI rà soát theo đúng quyền người dùng."
    )

    flash = st.session_state.pop(_connection_flash_key(project_id), None)
    if flash:
        level, message = flash
        if level == "success":
            st.success(message)
        else:
            st.error(message)

    tab_overview, tab_spaces, tab_sources, tab_history, tab_ai = st.tabs([
        "📈 Tổng quan",
        "🏢 Kho nhà thầu",
        "🔗 Nguồn Google",
        "🕘 Lịch sử",
        "🤖 AI phân tích",
    ])

    with tab_overview:
        _render_overview(st, service, project_id, contractors, workspace_ids)
    with tab_spaces:
        _render_data_spaces(
            st, service, project_id, contractors, spaces, can_update=can_update
        )
    with tab_sources:
        _render_sources(
            st,
            service,
            project_id,
            identity,
            contractors,
            spaces,
            client,
            stored,
            can_update=can_update,
            can_admin=can_admin,
        )
    with tab_history:
        _render_history(st, service, project_id, workspace_ids)
    with tab_ai:
        _render_ai(st, db, project_id, contractors, identity)
