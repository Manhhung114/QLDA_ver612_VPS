from __future__ import annotations

import inspect
import os
import time as _time
from datetime import date, datetime
from html import escape
from typing import Any, Callable

from qlda.runtime_core.vn_datetime import format_tabular_vn, install_work_task_vn_display


PATCH_MARKER = "V7 COMPACT UI RUNTIME V5 UNIFIED COLOR + MEETING MINUTES"
_CAPTION_STATE_KEY = "qlda_v7_show_captions"
_CAPTION_ADMIN_KEY = "qlda_v7_caption_admin_authorized"
_MEETING_DOC_TYPE = "BBHOP"
_MEETING_DOC_LABEL = "Biên bản họp"
_MEETING_DOC_CONFIG = {
    "title": "Biên bản họp",
    "statuses": [
        "Soạn thảo",
        "Đã phát hành",
        "Chờ xác nhận",
        "Yêu cầu chỉnh sửa",
        "Đã xác nhận",
        "Đóng",
        "Hủy",
    ],
    "done_statuses": ["Đã xác nhận", "Đóng", "Hủy"],
    "subject": "Tên cuộc họp / Nội dung chính",
    "code_label": "Mã biên bản họp *",
    "issuer_label": "Người / Đơn vị lập biên bản",
    "assignee_label": "Người / Đơn vị tham dự / xác nhận",
    "issue_date_label": "Ngày họp / phát hành",
    "due_date_label": "Hạn xác nhận",
    "closed_date_label": "Ngày xác nhận / đóng",
    "response_label": "Kết luận / Ý kiến / Hành động sau họp",
}


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


def _safe_float(value: Any) -> float:
    try:
        return float(value or 0)
    except Exception:
        return 0.0


def _safe_date(value: Any):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except Exception:
            pass
    return None


def _find_app_globals() -> dict[str, Any] | None:
    """Locate the running Streamlit app globals without importing the app again."""
    frame = inspect.currentframe()
    try:
        frame = frame.f_back if frame else None
        while frame is not None:
            glob = frame.f_globals
            cfg = glob.get("DOC_CONFIG")
            if isinstance(cfg, dict) and "NCR" in cfg and "BBHT" in cfg:
                return glob
            frame = frame.f_back
    finally:
        del frame
    return None


def install_meeting_minutes_sheet_v7(st) -> None:
    """Add the meeting-minutes document sheet to the existing document UI.

    The production app still owns the generic document renderer and persistence.
    This presentation extension registers one additional document type and makes
    it available in the existing ``Loại hồ sơ`` segmented control. Because the
    generic documents table stores ``doc_type`` as text, no database migration is
    required and attachments continue to use the normal document attachment flow.
    """
    app_globals = _find_app_globals()
    if app_globals is not None:
        doc_config = app_globals.get("DOC_CONFIG")
        if isinstance(doc_config, dict):
            doc_config.setdefault(_MEETING_DOC_TYPE, dict(_MEETING_DOC_CONFIG))

    if getattr(st, "_qlda_v7_meeting_minutes_sheet_installed", False):
        return

    original_segmented_control = st.segmented_control

    def _segmented_control_with_meeting_minutes(label, options, *args, **kwargs):
        try:
            values = list(options)
        except Exception:
            values = options

        if str(label or "").strip() == "Loại hồ sơ" and isinstance(values, list):
            known = {str(v) for v in values}
            if {"NCR", "RFA", "RFI", "BBHT"}.issubset(known):
                if _MEETING_DOC_TYPE not in known:
                    values.append(_MEETING_DOC_TYPE)

                base_format = kwargs.get("format_func")

                def _format(value):
                    if value == _MEETING_DOC_TYPE:
                        return _MEETING_DOC_LABEL
                    if callable(base_format):
                        return base_format(value)
                    return str(value)

                kwargs["format_func"] = _format

        return original_segmented_control(label, values, *args, **kwargs)

    st._qlda_v7_original_segmented_control = original_segmented_control
    st.segmented_control = _segmented_control_with_meeting_minutes
    st._qlda_v7_meeting_minutes_sheet_installed = True


def install_caption_policy_v7(st) -> None:
    """Hide user-facing Streamlit captions by default across the whole app."""
    if getattr(st, "_qlda_v7_caption_policy_installed", False):
        return

    original_caption = st.caption

    def _caption_if_enabled(*args, **kwargs):
        allowed = bool(st.session_state.get(_CAPTION_ADMIN_KEY, False))
        enabled = bool(st.session_state.get(_CAPTION_STATE_KEY, False))
        if allowed and enabled:
            return original_caption(*args, **kwargs)
        return None

    st._qlda_v7_original_caption = original_caption
    st.caption = _caption_if_enabled
    st._qlda_v7_caption_policy_installed = True


def install_vn_datetime_policy_v7(st) -> None:
    """Use Vietnam wall-clock time consistently in read-only app tables."""
    if getattr(st, "_qlda_v7_vn_datetime_policy_installed", False):
        return

    os.environ["TZ"] = "Asia/Ho_Chi_Minh"
    try:
        _time.tzset()
    except Exception:
        pass

    original_dataframe = st.dataframe
    original_table = st.table

    def _dataframe_vn(data=None, *args, **kwargs):
        return original_dataframe(format_tabular_vn(data), *args, **kwargs)

    def _table_vn(data=None, *args, **kwargs):
        return original_table(format_tabular_vn(data), *args, **kwargs)

    st._qlda_v7_original_dataframe = original_dataframe
    st._qlda_v7_original_table = original_table
    st.dataframe = _dataframe_vn
    st.table = _table_vn

    try:
        from streamlit.delta_generator import DeltaGenerator

        if not getattr(DeltaGenerator, "_qlda_v7_vn_datetime_policy_installed", False):
            dg_dataframe = DeltaGenerator.dataframe
            dg_table = DeltaGenerator.table

            def _dg_dataframe_vn(self, data=None, *args, **kwargs):
                return dg_dataframe(self, format_tabular_vn(data), *args, **kwargs)

            def _dg_table_vn(self, data=None, *args, **kwargs):
                return dg_table(self, format_tabular_vn(data), *args, **kwargs)

            DeltaGenerator.dataframe = _dg_dataframe_vn
            DeltaGenerator.table = _dg_table_vn
            DeltaGenerator._qlda_v7_vn_datetime_policy_installed = True
    except Exception:
        pass

    install_work_task_vn_display()
    st._qlda_v7_vn_datetime_policy_installed = True


def render_admin_caption_toggle_v7(st, is_admin: bool) -> None:
    """Admin-only switch for restoring explanatory captions when needed."""
    authorized = bool(is_admin)
    st.session_state[_CAPTION_ADMIN_KEY] = authorized

    if not authorized:
        st.session_state[_CAPTION_STATE_KEY] = False
        return

    with st.expander("⚙️ Giao diện · Admin", expanded=False):
        st.toggle(
            "Hiện chú thích / hướng dẫn",
            value=False,
            key=_CAPTION_STATE_KEY,
            help="Bật tạm các dòng chú thích nhỏ trên toàn app. Mặc định giao diện V7 luôn ẩn chú thích.",
        )


def install_theme_v7(st) -> None:
    """Render the unified V7 visual layer and register presentation extensions."""
    install_meeting_minutes_sheet_v7(st)
    st.markdown(
        """
<style>
:root {
  --qlda-navy:#0f2747; --qlda-navy-2:#173b6b; --qlda-blue:#1d4ed8;
  --qlda-blue-strong:#1746b5; --qlda-blue-soft:#eaf2ff; --qlda-cyan:#0ea5e9;
  --qlda-indigo:#4f46e5; --qlda-bg:#f4f7fc; --qlda-card:#ffffff;
  --qlda-card-soft:#f8fbff; --qlda-border:#d6e2f0; --qlda-border-strong:#b9cce5;
  --qlda-text:#14213d; --qlda-muted:#64748b; --qlda-good:#15803d;
  --qlda-good-soft:#ecfdf3; --qlda-warn:#b45309; --qlda-warn-soft:#fff7ed;
  --qlda-bad:#b91c1c; --qlda-bad-soft:#fef2f2;
  --qlda-shadow:0 8px 24px rgba(15,39,71,.08);
  --qlda-shadow-sm:0 2px 8px rgba(15,39,71,.07);
}
html,body,[class*="css"]{color:var(--qlda-text)}
.stApp{color:var(--qlda-text);background:radial-gradient(circle at 100% 0%,rgba(29,78,216,.055),transparent 28rem),linear-gradient(180deg,#f8fbff 0%,var(--qlda-bg) 52%,#f7f9fd 100%)}
.block-container{max-width:1680px;padding-top:.65rem;padding-bottom:3.2rem}
h1,h2,h3,h4,h5,h6{color:var(--qlda-navy)!important;letter-spacing:-.012em}
a{color:var(--qlda-blue)} a:hover{color:var(--qlda-blue-strong)}
label,[data-testid="stWidgetLabel"]{color:var(--qlda-text)!important;font-weight:570}
[data-testid="stSidebar"]{background:linear-gradient(180deg,#f7faff 0%,#edf4ff 100%);border-right:1px solid var(--qlda-border);box-shadow:6px 0 24px rgba(15,39,71,.035)}
[data-testid="stSidebar"] [data-testid="stVerticalBlock"]{gap:.55rem}
[data-testid="stSidebar"] hr{border-color:#cbd9ea!important}
[data-baseweb="tab-list"]{gap:4px;padding:4px;background:var(--qlda-blue-soft);border:1px solid #d5e3fa;border-radius:12px;overflow-x:auto}
[data-baseweb="tab"]{min-height:2.55rem;border-radius:9px;color:#40516a!important;font-weight:650;padding-left:14px!important;padding-right:14px!important}
[data-baseweb="tab"]:hover{color:var(--qlda-blue)!important;background:rgba(255,255,255,.62)}
[data-baseweb="tab"][aria-selected="true"]{color:var(--qlda-blue)!important;background:var(--qlda-card);box-shadow:var(--qlda-shadow-sm)}
[data-baseweb="tab-highlight"]{background-color:var(--qlda-blue)!important;height:3px!important}
[data-baseweb="tab-border"]{background-color:transparent!important}
[data-testid="stMetric"]{position:relative;background:linear-gradient(145deg,#fff 0%,#f8fbff 100%);border:1px solid var(--qlda-border);border-left:4px solid var(--qlda-blue);border-radius:14px;padding:12px 14px;box-shadow:var(--qlda-shadow-sm)}
[data-testid="stMetricLabel"]{color:var(--qlda-muted)} [data-testid="stMetricValue"]{color:var(--qlda-navy);font-weight:760} [data-testid="stMetricDelta"]{font-weight:650}
[data-testid="stExpander"]{background:var(--qlda-card);border:1px solid var(--qlda-border);border-radius:12px;overflow:hidden;box-shadow:0 1px 4px rgba(15,39,71,.035)}
[data-testid="stExpander"] details>summary:hover{background:#f6f9ff}
[data-testid="stForm"]{background:linear-gradient(180deg,#fff 0%,#fbfdff 100%);border:1px solid var(--qlda-border);border-radius:14px;padding:14px 16px 8px;box-shadow:0 2px 10px rgba(15,39,71,.045)}
.stButton>button,.stDownloadButton>button,.stLinkButton>a{border-radius:10px!important;min-height:2.45rem;border:1px solid var(--qlda-border-strong)!important;background:linear-gradient(180deg,#fff 0%,#f7faff 100%)!important;color:var(--qlda-navy)!important;font-weight:650!important;box-shadow:0 1px 3px rgba(15,39,71,.05);transition:border-color .16s ease,box-shadow .16s ease,transform .16s ease}
.stButton>button:hover,.stDownloadButton>button:hover,.stLinkButton>a:hover{border-color:var(--qlda-blue)!important;color:var(--qlda-blue)!important;box-shadow:0 4px 12px rgba(29,78,216,.12);transform:translateY(-1px)}
.stButton>button[kind="primary"],.stDownloadButton>button[kind="primary"]{border-color:var(--qlda-blue)!important;background:linear-gradient(135deg,var(--qlda-blue-strong) 0%,#2563eb 65%,var(--qlda-cyan) 145%)!important;color:#fff!important;box-shadow:0 5px 14px rgba(29,78,216,.22)}
.stButton>button[kind="primary"]:hover,.stDownloadButton>button[kind="primary"]:hover{color:#fff!important;border-color:#123f9f!important;box-shadow:0 7px 18px rgba(29,78,216,.29)}
.stButton>button:disabled,.stDownloadButton>button:disabled{opacity:.58;box-shadow:none;transform:none}
[data-baseweb="input"]>div,[data-baseweb="textarea"],[data-baseweb="select"]>div,[data-baseweb="base-input"]{background-color:#fff!important;border-color:var(--qlda-border-strong)!important;border-radius:10px!important}
[data-baseweb="input"]>div:focus-within,[data-baseweb="textarea"]:focus-within,[data-baseweb="select"]>div:focus-within{border-color:var(--qlda-blue)!important;box-shadow:0 0 0 2px rgba(29,78,216,.10)!important}
[data-baseweb="tag"]{background:var(--qlda-blue-soft)!important;color:var(--qlda-blue-strong)!important;border:1px solid #cbdcf8}
[data-testid="stFileUploaderDropzone"]{background:linear-gradient(135deg,#f7faff 0%,#edf5ff 100%);border:1.5px dashed #9eb9df;border-radius:14px}
[data-testid="stFileUploaderDropzone"]:hover{border-color:var(--qlda-blue);background:#eaf2ff}
[data-testid="stFileUploaderDropzoneInstructions"]{color:var(--qlda-muted)}
[data-testid="stDataFrame"],[data-testid="stDataEditor"]{border:1px solid var(--qlda-border);border-top:3px solid var(--qlda-blue);border-radius:12px;overflow:hidden;background:var(--qlda-card);box-shadow:0 2px 10px rgba(15,39,71,.045)}
[data-testid="stAlert"]{border-radius:12px;border-width:1px;box-shadow:0 1px 4px rgba(15,39,71,.04)}
[data-testid="stProgressBar"]>div>div>div>div{background:linear-gradient(90deg,var(--qlda-blue) 0%,var(--qlda-cyan) 100%)!important}
hr{border-color:var(--qlda-border)!important}
@keyframes qlda-v7-globe-spin{from{transform:rotate(0deg)}to{transform:rotate(360deg)}}
[data-testid="stStatusWidget"] svg,[data-testid="stStatusWidget"] [data-testid="stIconMaterial"]{display:none!important}
[data-testid="stStatusWidget"]::before{content:"🌍";display:inline-block;margin-right:7px;font-size:20px;line-height:1;transform-origin:center;animation:qlda-v7-globe-spin 1.35s linear infinite;vertical-align:middle}
.qlda-v7-hero{display:flex;align-items:center;justify-content:space-between;gap:16px;background:radial-gradient(circle at 92% 15%,rgba(255,255,255,.20),transparent 9rem),linear-gradient(135deg,var(--qlda-navy) 0%,#16477d 48%,var(--qlda-blue) 100%);border:1px solid rgba(255,255,255,.12);border-radius:17px;padding:16px 19px;margin:.1rem 0 .9rem 0;box-shadow:0 10px 28px rgba(15,39,71,.16)}
.qlda-v7-title{font-size:1.42rem;font-weight:800;color:#fff;line-height:1.15;letter-spacing:.01em}.qlda-v7-project{font-size:.99rem;font-weight:680;color:#f8fbff;margin-top:5px}.qlda-v7-contractor{font-size:.85rem;color:#dce9fb;margin-top:3px}.qlda-v7-user{text-align:right;font-size:.82rem;color:#e8f1ff;white-space:nowrap}
.qlda-v7-badge{display:inline-block;padding:4px 10px;border-radius:999px;background:rgba(255,255,255,.94);color:var(--qlda-blue-strong);font-size:.76rem;font-weight:750;box-shadow:0 2px 8px rgba(4,20,47,.12)}
.qlda-v7-actions{background:linear-gradient(135deg,#fffdf9 0%,var(--qlda-warn-soft) 100%);border:1px solid #f2d4ad;border-left:4px solid #f59e0b;border-radius:14px;padding:12px 14px;margin:.7rem 0 .8rem 0;box-shadow:0 2px 10px rgba(180,83,9,.06)}
.qlda-v7-actions-title{font-weight:760;color:#7c3b08;margin-bottom:7px}.qlda-v7-action-row{display:flex;gap:7px;flex-wrap:wrap}.qlda-v7-chip{border:1px solid #f0c38c;background:#fff8ed;color:#92400e;border-radius:999px;padding:5px 10px;font-size:.82rem;font-weight:620}.qlda-v7-chip.good{border-color:#bde3ca;background:var(--qlda-good-soft);color:#166534}
.qlda-v7-section-title{font-size:1.02rem;font-weight:780;color:var(--qlda-navy);margin:.35rem 0 .48rem;padding-left:9px;border-left:4px solid var(--qlda-blue)}
.qlda-v7-credit{position:fixed;right:16px;bottom:8px;z-index:999999;font-size:10.5px;letter-spacing:.1px;color:#27405f;background:rgba(255,255,255,.94);border:1px solid rgba(185,204,229,.86);border-radius:999px;padding:3px 8px;pointer-events:none;backdrop-filter:blur(5px);box-shadow:0 2px 8px rgba(15,39,71,.08)}
@media(max-width:760px){.block-container{padding-left:.75rem;padding-right:.75rem;padding-top:.45rem}[data-baseweb="tab-list"]{padding:3px;border-radius:10px}[data-baseweb="tab"]{padding-left:10px!important;padding-right:10px!important;min-height:2.35rem}.qlda-v7-hero{align-items:flex-start;padding:13px 14px;border-radius:15px}.qlda-v7-title{font-size:1.18rem}.qlda-v7-project{font-size:.9rem}.qlda-v7-user{display:none}.qlda-v7-credit{right:8px;bottom:5px;font-size:9px}[data-testid="stMetric"]{padding:10px 11px}}
</style>
<div class="qlda-v7-credit">by: Hoàng Mạnh Hùng &amp; AI</div>
        """,
        unsafe_allow_html=True,
    )


def render_header_v7(st, project: Any, contractor_ctx: Any, identity: Any) -> None:
    p = _rowdict(project)
    contractor = _rowdict(contractor_ctx)
    user = _rowdict(identity)

    project_code = escape(str(p.get("code") or ""))
    project_name = escape(str(p.get("name") or "Dự án"))
    contractor_code = escape(str(contractor.get("contractor_code") or ""))
    contractor_name = escape(str(contractor.get("contractor_name") or ""))
    user_name = escape(str(user.get("name") or user.get("email") or "Người dùng"))
    role = str(user.get("role") or "").lower()
    role_label = {"read": "Chỉ xem", "update": "Cập nhật", "admin": "Admin"}.get(role, "")

    contractor_line = ""
    if contractor_code or contractor_name:
        contractor_line = f"{contractor_code} · {contractor_name}".strip(" ·")

    st.markdown(
        f"""
<div class="qlda-v7-hero">
  <div>
    <div class="qlda-v7-title">QLDA XÂY DỰNG</div>
    <div class="qlda-v7-project">{project_code}{' · ' if project_code else ''}{project_name}</div>
    <div class="qlda-v7-contractor">{contractor_line or 'Workspace dự án'}</div>
  </div>
  <div class="qlda-v7-user">
    <span class="qlda-v7-badge">{escape(role_label)}</span><br/>
    {user_name}
  </div>
</div>
        """,
        unsafe_allow_html=True,
    )


def _latest_ipc_and_boq(db, pid: int) -> tuple[str, float]:
    try:
        from qlda.runtime_core.contractor_workspace import contractor_scope_stats

        stats = contractor_scope_stats(db, int(pid))
        return str(stats.get("latest_ipc") or "—"), _safe_float(stats.get("boq_total"))
    except Exception:
        return "—", 0.0


def _pending_documents(db, pid: int, doc_config: dict[str, Any]) -> int:
    pending = 0
    for doc_type, cfg in (doc_config or {}).items():
        try:
            rows = db.documents(int(pid), doc_type)
        except Exception:
            continue
        done = set((cfg or {}).get("done_statuses") or [])
        pending += sum(1 for row in rows if str(row["status"] or "") not in done)
    return pending


def _pending_drawings(db, pid: int, drawing_types: dict[str, Any]) -> int:
    terminal = {"Chấp thuận", "Chấp thuận có điều kiện", "Hủy", "Thay thế"}
    count = 0
    for drawing_type in (drawing_types or {}):
        try:
            rows = db.drawings(int(pid), drawing_type)
        except Exception:
            continue
        count += sum(1 for row in rows if str(row["status"] or "") not in terminal)
    return count


def _late_procurements(db, pid: int) -> int:
    try:
        rows = db.procurements(int(pid))
    except Exception:
        return 0
    today = date.today()
    late = 0
    for row in rows:
        status = str(row["status"] or "")
        if status in {"Chậm"}:
            late += 1
            continue
        if status in {"Đã về công trường", "Hủy"}:
            continue
        planned = _safe_date(row["planned_delivery_date"])
        actual = _safe_date(row["actual_delivery_date"])
        if planned and not actual and today > planned:
            late += 1
    return late


def render_overview_v7(
    st,
    db,
    pid: int,
    *,
    doc_config: dict[str, Any] | None = None,
    drawing_types: dict[str, Any] | None = None,
    detailed_renderer: Callable[[int], None] | None = None,
) -> None:
    """Decision-first overview. Calculations are read-only summaries of live data."""
    try:
        tasks = list(db.tasks(int(pid)) or [])
    except Exception:
        tasks = []

    total_tasks = len(tasks)
    planned = sum(_safe_float(t["planned_progress"]) for t in tasks) / total_tasks if total_tasks else 0.0
    actual = sum(_safe_float(t["actual_progress"]) for t in tasks) / total_tasks if total_tasks else 0.0
    delayed = sum(1 for t in tasks if str(t["status"] or "") == "Chậm tiến độ")
    pending_docs = _pending_documents(db, int(pid), doc_config or {})
    pending_drawings = _pending_drawings(db, int(pid), drawing_types or {})
    late_materials = _late_procurements(db, int(pid))
    latest_ipc, boq_total = _latest_ipc_and_boq(db, int(pid))

    st.markdown('<div class="qlda-v7-section-title">Tổng quan điều hành</div>', unsafe_allow_html=True)
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Tiến độ TT", f"{actual:.1f}%", f"{actual - planned:+.1f}% so KH")
    c2.metric("Công việc chậm", delayed)
    c3.metric("Hồ sơ chờ xử lý", pending_docs)
    c4.metric("IPC mới nhất", latest_ipc)
    c5.metric("BOQ", f"{boq_total / 1_000_000_000:.2f} tỷ" if boq_total else "—")

    actions: list[str] = []
    if delayed:
        actions.append(f"{delayed} công việc chậm tiến độ")
    if pending_docs:
        actions.append(f"{pending_docs} hồ sơ chưa kết thúc")
    if pending_drawings:
        actions.append(f"{pending_drawings} bản vẽ đang xử lý")
    if late_materials:
        actions.append(f"{late_materials} vật tư/mua sắm trễ")

    chips = "".join(f'<span class="qlda-v7-chip">{escape(item)}</span>' for item in actions)
    if not chips:
        chips = '<span class="qlda-v7-chip good">Không có cảnh báo chính từ dữ liệu hiện tại</span>'
    st.markdown(
        f'<div class="qlda-v7-actions"><div class="qlda-v7-actions-title">⚠ Cần xử lý</div>'
        f'<div class="qlda-v7-action-row">{chips}</div></div>',
        unsafe_allow_html=True,
    )

    if detailed_renderer is not None:
        show_detail = st.toggle(
            "Hiện phân tích chi tiết",
            value=False,
            key=f"v7_show_detailed_report_{int(pid)}",
            help="Mở các biểu đồ báo cáo cũ khi thật sự cần phân tích sâu.",
        )
        if show_detail:
            detailed_renderer(int(pid))
