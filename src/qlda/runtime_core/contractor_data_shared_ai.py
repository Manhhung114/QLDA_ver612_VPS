from __future__ import annotations

import re
import unicodedata
from typing import Any

PATCH_MARKER = "V7 CONTRACTOR DATA SHARED AI V1"


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
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("đ", "d")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _question_terms(question: str) -> list[str]:
    stop = {
        "cho", "toi", "hay", "kiem", "tra", "du", "lieu", "cua", "va", "voi", "theo",
        "trong", "tren", "cac", "nhung", "bao", "nhieu", "tong", "hop", "duoc", "hien", "tai",
        "nha", "thau", "cong", "viec", "san", "luong", "tien", "do", "sheet", "worksheet",
    }
    out: list[str] = []
    for token in _norm(question).split():
        if len(token) >= 2 and token not in stop and token not in out:
            out.append(token)
    return out[:12]


def _table_exists(builder, connection, table: str) -> bool:
    try:
        return bool(builder.table_exists(connection, table))
    except Exception:
        try:
            connection.execute(f"SELECT 1 FROM {table} LIMIT 1")
            return True
        except Exception:
            return False


def build_contractor_data_hub_appendix(builder, project_id: int, question: str = "") -> str:
    """Build live Contractor Data Hub evidence for the app-wide AI assistant.

    The existing contractor AI access ContextVar is honored. A contractor account
    therefore receives only its authorized workspace while project users may use
    the whole master-project data hub.
    """
    try:
        from qlda.runtime_core.contractor_access_control import _AI_WORKSPACE_SCOPE
        restricted_workspace = _AI_WORKSPACE_SCOPE.get()
    except Exception:
        restricted_workspace = None

    try:
        from qlda.runtime_core.contractor_workspace import resolve_master_project_id_connection
    except Exception:
        resolve_master_project_id_connection = None

    lines: list[str] = []
    try:
        with builder.connect() as connection:
            if not _table_exists(builder, connection, "contractor_data_records"):
                return ""

            master_project_id = int(project_id)
            if callable(resolve_master_project_id_connection):
                try:
                    master_project_id = int(resolve_master_project_id_connection(connection, int(project_id)))
                except Exception:
                    master_project_id = int(project_id)

            where = "r.master_project_id=?"
            params: list[Any] = [master_project_id]
            if restricted_workspace:
                where += " AND r.workspace_project_id=?"
                params.append(int(restricted_workspace))

            summary = _rowdict(connection.execute(
                f"""SELECT COUNT(*) AS record_count,
                    COUNT(DISTINCT r.source_id) AS source_count,
                    COUNT(DISTINCT r.worksheet) AS worksheet_count,
                    SUM(CASE WHEN r.record_type='PRODUCTION' THEN 1 ELSE 0 END) AS production_points,
                    AVG(CASE WHEN r.record_type='PRODUCTION' THEN r.progress_percent ELSE NULL END) AS avg_progress,
                    MAX(r.synced_at) AS last_sync
                    FROM contractor_data_records r WHERE {where}""",
                params,
            ).fetchone())
            if int(summary.get("record_count") or 0) <= 0:
                return ""

            lines += [
                "",
                "## KHO DỮ LIỆU NHÀ THẦU – DÙNG CHUNG VỚI TRỢ LÝ AI",
                "Đây là dữ liệu live đã đồng bộ từ Google Sheet/Drive vào Contractor Data Hub. "
                "Dùng phần này như nguồn nội bộ chính thức khi câu hỏi liên quan sản lượng, worksheet, Zone/tầng dữ liệu hoặc dữ liệu nhà thầu.",
                (
                    f"Phạm vi: {'workspace nhà thầu được cấp quyền ' + str(restricted_workspace) if restricted_workspace else 'toàn dự án / các nhà thầu được phép xem'} | "
                    f"records={int(summary.get('record_count') or 0):,} | nguồn={int(summary.get('source_count') or 0):,} | "
                    f"worksheet={int(summary.get('worksheet_count') or 0):,} | điểm sản lượng={int(summary.get('production_points') or 0):,} | "
                    f"sản lượng TB={float(summary.get('avg_progress') or 0):.1f}% | sync gần nhất={summary.get('last_sync') or ''}"
                ),
            ]

            contractor_labels: dict[int, str] = {}
            if _table_exists(builder, connection, "contractor_data_spaces"):
                try:
                    rows = connection.execute(
                        "SELECT workspace_project_id,contractor_code,contractor_name FROM contractor_data_spaces WHERE master_project_id=?",
                        (master_project_id,),
                    ).fetchall()
                    for raw in rows:
                        row = _rowdict(raw)
                        wid = int(row.get("workspace_project_id") or 0)
                        label = f"{row.get('contractor_code','')} - {row.get('contractor_name','')}".strip(" -")
                        contractor_labels[wid] = label or f"Workspace {wid}"
                except Exception:
                    pass

            try:
                groups = connection.execute(
                    f"""SELECT r.workspace_project_id,r.source_name,r.worksheet,r.zone,
                        COUNT(*) AS record_count,
                        SUM(CASE WHEN r.record_type='PRODUCTION' THEN 1 ELSE 0 END) AS production_points,
                        AVG(CASE WHEN r.record_type='PRODUCTION' THEN r.progress_percent ELSE NULL END) AS avg_progress,
                        MAX(r.synced_at) AS last_sync
                        FROM contractor_data_records r WHERE {where}
                        GROUP BY r.workspace_project_id,r.source_name,r.worksheet,r.zone
                        ORDER BY production_points DESC,record_count DESC LIMIT 180""",
                    params,
                ).fetchall()
            except Exception:
                groups = []

            if groups:
                lines.append("### TỔNG HỢP THEO WORKSHEET / CHIỀU DỮ LIỆU")
                for raw in groups:
                    row = _rowdict(raw)
                    wid = int(row.get("workspace_project_id") or 0)
                    label = contractor_labels.get(wid, f"Workspace {wid}")
                    avg = row.get("avg_progress")
                    avg_text = "—" if avg is None else f"{float(avg):.1f}%"
                    lines.append(
                        f"[DATA-HUB] {label} | worksheet={row.get('worksheet','')} | chiều={row.get('zone','')} | "
                        f"records={int(row.get('record_count') or 0)} | điểm sản lượng={int(row.get('production_points') or 0)} | "
                        f"TB={avg_text} | nguồn={row.get('source_name','')} | sync={row.get('last_sync','')}"
                    )

            terms = _question_terms(question)
            detail_rows: list[dict[str, Any]] = []
            if terms:
                detail_where = where
                detail_params = list(params)
                clauses: list[str] = []
                # Use accent-insensitive scoring in Python after a bounded live read.
                try:
                    raw_rows = connection.execute(
                        f"""SELECT r.workspace_project_id,r.source_name,r.category,r.worksheet,r.record_type,
                            r.record_ref,r.work_item,r.zone,r.progress_percent,r.content,r.source_row,r.synced_at
                            FROM contractor_data_records r WHERE {detail_where}
                            ORDER BY r.synced_at DESC,r.source_row DESC LIMIT 5000""",
                        detail_params,
                    ).fetchall()
                except Exception:
                    raw_rows = []

                scored: list[tuple[int, dict[str, Any]]] = []
                for raw in raw_rows:
                    row = _rowdict(raw)
                    haystack = _norm(" ".join(str(row.get(k) or "") for k in (
                        "source_name", "category", "worksheet", "record_type", "work_item", "zone", "content"
                    )))
                    score = sum(1 for term in terms if term in haystack)
                    if score:
                        scored.append((score, row))
                scored.sort(key=lambda item: (item[0], int(item[1].get("source_row") or 0)), reverse=True)
                detail_rows = [row for _, row in scored[:120]]

            if detail_rows:
                lines.append("### RECORDS PHÙ HỢP CÂU HỎI")
                for row in detail_rows:
                    wid = int(row.get("workspace_project_id") or 0)
                    label = contractor_labels.get(wid, f"Workspace {wid}")
                    content = str(row.get("content") or "").strip().replace("\n", " ")
                    if len(content) > 600:
                        content = content[:597] + "..."
                    progress = row.get("progress_percent")
                    progress_text = "" if progress is None else f" | tiến độ={float(progress):.1f}%"
                    lines.append(
                        f"[DATA-HUB-ROW] {label} | {row.get('worksheet','')} | {row.get('work_item','')} | "
                        f"{row.get('zone','')}{progress_text} | dòng={row.get('source_row','')} | {content}"
                    )
    except Exception:
        return ""

    return "\n".join(lines)


def install_contractor_data_shared_ai_context() -> None:
    """Attach Contractor Data Hub to the app-wide ProjectContextBuilder."""
    import qlda.runtime_core.ai_service as ai_service

    cls = ai_service.ProjectContextBuilder
    if getattr(cls, "_qlda_contractor_data_shared_ai", False):
        return
    original_build = cls.build

    def build_with_contractor_data(self, project_id: int, question: str = "", *args, **kwargs):
        base = original_build(self, int(project_id), question, *args, **kwargs)
        appendix = build_contractor_data_hub_appendix(self, int(project_id), str(question or ""))
        if not appendix:
            return base
        return str(base) + "\n" + appendix

    cls.build = build_with_contractor_data
    cls._qlda_contractor_data_shared_ai = True
    cls._qlda_contractor_data_shared_ai_marker = PATCH_MARKER


def render_production_progress_shared(st, db, project_id: int, *, identity: dict | None = None) -> None:
    """Render Contractor Data Hub without a separate AI tab.

    AI analysis is intentionally centralized in the application's common assistant.
    """
    from qlda.presentation.streamlit import production_progress_ui as ui

    project_id = int(project_id)
    identity = dict(identity or {})
    role = str(identity.get("role") or "read").strip().lower()
    can_update = role in {"update", "admin"}
    can_admin = role == "admin"

    try:
        ui.apply_to_environment()
    except Exception:
        pass

    ui._handle_oauth_callback(st, project_id, identity)

    try:
        contractors = ui._authorized_contractors(db, project_id, identity)
    except PermissionError as exc:
        st.error(str(exc))
        return
    except Exception as exc:
        st.error(f"Không xác định được phạm vi nhà thầu: {exc}")
        return
    if not contractors:
        st.info("Dự án chưa có nhà thầu đang hoạt động trong phạm vi tài khoản này.")
        return

    client, stored = ui._google_client(st, project_id)
    service = ui.ContractorDataHubService(db, client=client)
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
        "Mỗi nhà thầu là một kho dữ liệu riêng. QLDA đồng bộ Google ở chế độ read-only, lưu snapshot lịch sử. "
        "Dữ liệu trong kho này được dùng chung trực tiếp bởi Trợ lý AI của app theo đúng quyền người dùng."
    )

    flash = st.session_state.pop(ui._connection_flash_key(project_id), None)
    if flash:
        level, message = flash
        if level == "success":
            st.success(message)
        else:
            st.error(message)

    tab_overview, tab_spaces, tab_sources, tab_history = st.tabs([
        "📈 Tổng quan",
        "🏢 Kho nhà thầu",
        "🔗 Nguồn Google",
        "🕘 Lịch sử",
    ])

    with tab_overview:
        ui._render_overview(st, service, project_id, contractors, workspace_ids)
    with tab_spaces:
        ui._render_data_spaces(st, service, project_id, contractors, spaces, can_update=can_update)
    with tab_sources:
        ui._render_sources(
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
        ui._render_history(st, service, project_id, workspace_ids)


def install_production_progress_shared_ai_ui() -> None:
    """Remove the local AI tab and route analysis to the app-wide assistant."""
    from qlda.presentation.streamlit import production_progress_ui

    if getattr(production_progress_ui, "_qlda_shared_ai_ui_v1", False):
        return
    production_progress_ui.render_production_progress = render_production_progress_shared
    production_progress_ui._qlda_shared_ai_ui_v1 = True


__all__ = [
    "build_contractor_data_hub_appendix",
    "install_contractor_data_shared_ai_context",
    "install_production_progress_shared_ai_ui",
    "render_production_progress_shared",
]
