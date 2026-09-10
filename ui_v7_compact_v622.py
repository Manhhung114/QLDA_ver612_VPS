from __future__ import annotations

from datetime import date, datetime
from html import escape
from typing import Any, Callable


PATCH_MARKER = "V7 COMPACT UI RUNTIME V2 CAPTION CONTROL"
_CAPTION_STATE_KEY = "qlda_v7_show_captions"
_CAPTION_ADMIN_KEY = "qlda_v7_caption_admin_authorized"


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


def install_caption_policy_v7(st) -> None:
    """Hide user-facing Streamlit captions by default across the whole app.

    The original ``st.caption`` remains available behind an Admin-controlled
    session flag. This is presentation-only: warnings, errors, success/status
    messages and business data are not suppressed.
    """
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


def render_admin_caption_toggle_v7(st, is_admin: bool) -> None:
    """Admin-only switch for restoring explanatory captions when needed."""
    authorized = bool(is_admin)
    st.session_state[_CAPTION_ADMIN_KEY] = authorized

    if not authorized:
        # A reused browser session must never carry the Admin preference into a
        # non-Admin account.
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
    """Render the isolated V7 visual layer; no business or persistence logic."""
    st.markdown(
        """
<style>
:root {
  --qlda-navy: #17365d;
  --qlda-blue: #245f9e;
  --qlda-bg: #f5f7fa;
  --qlda-card: #ffffff;
  --qlda-border: #dfe6ee;
  --qlda-text: #182230;
  --qlda-muted: #667085;
  --qlda-good: #16803c;
  --qlda-warn: #b54708;
  --qlda-bad: #b42318;
}
html, body, [class*="css"] { color: var(--qlda-text); }
.stApp { background: var(--qlda-bg); }
.block-container {
  max-width: 1680px;
  padding-top: .65rem;
  padding-bottom: 3.2rem;
}
[data-testid="stSidebar"] {
  background: #f0f4f8;
  border-right: 1px solid var(--qlda-border);
}
[data-testid="stSidebar"] [data-testid="stVerticalBlock"] { gap: .55rem; }
[data-testid="stMetric"] {
  background: var(--qlda-card);
  border: 1px solid var(--qlda-border);
  border-radius: 14px;
  padding: 12px 14px;
  box-shadow: 0 1px 2px rgba(16,24,40,.035);
}
[data-testid="stMetricLabel"] { color: var(--qlda-muted); }
[data-testid="stMetricValue"] { color: var(--qlda-navy); }
[data-testid="stExpander"] {
  background: var(--qlda-card);
  border: 1px solid var(--qlda-border);
  border-radius: 12px;
  overflow: hidden;
}
[data-testid="stForm"] {
  background: var(--qlda-card);
  border: 1px solid var(--qlda-border);
  border-radius: 12px;
  padding: 14px 16px 8px;
}
.stButton > button, .stDownloadButton > button, .stLinkButton > a {
  border-radius: 9px !important;
  min-height: 2.45rem;
}
[data-testid="stDataFrame"], [data-testid="stDataEditor"] {
  border: 1px solid var(--qlda-border);
  border-radius: 12px;
  overflow: hidden;
  background: var(--qlda-card);
}
hr { border-color: var(--qlda-border) !important; }
.qlda-v7-hero {
  display:flex;
  align-items:center;
  justify-content:space-between;
  gap:16px;
  background:linear-gradient(135deg,#ffffff 0%,#f7faff 100%);
  border:1px solid var(--qlda-border);
  border-radius:16px;
  padding:15px 18px;
  margin:.1rem 0 .85rem 0;
  box-shadow:0 1px 3px rgba(16,24,40,.04);
}
.qlda-v7-title { font-size:1.42rem; font-weight:750; color:var(--qlda-navy); line-height:1.15; }
.qlda-v7-project { font-size:.98rem; font-weight:600; color:var(--qlda-text); margin-top:4px; }
.qlda-v7-contractor { font-size:.84rem; color:var(--qlda-muted); margin-top:2px; }
.qlda-v7-user { text-align:right; font-size:.82rem; color:var(--qlda-muted); white-space:nowrap; }
.qlda-v7-badge {
  display:inline-block; padding:3px 9px; border-radius:999px;
  background:#eaf2fb; color:var(--qlda-blue); font-size:.76rem; font-weight:650;
}
.qlda-v7-actions {
  background:var(--qlda-card); border:1px solid var(--qlda-border); border-radius:14px;
  padding:12px 14px; margin:.7rem 0 .8rem 0;
}
.qlda-v7-actions-title { font-weight:700; color:var(--qlda-navy); margin-bottom:7px; }
.qlda-v7-action-row { display:flex; gap:7px; flex-wrap:wrap; }
.qlda-v7-chip {
  border:1px solid #ead7c5; background:#fff8f0; color:#8a3d05;
  border-radius:999px; padding:5px 10px; font-size:.82rem;
}
.qlda-v7-chip.good { border-color:#cce8d5; background:#f0faf3; color:#166534; }
.qlda-v7-section-title { font-size:1rem; font-weight:700; color:var(--qlda-navy); margin:.25rem 0 .4rem; }
.qlda-v7-credit {
  position:fixed; right:16px; bottom:8px; z-index:999999;
  font-size:10.5px; letter-spacing:.1px; color:#000000;
  background:rgba(255,255,255,.92); border:1px solid rgba(210,218,228,.78);
  border-radius:999px; padding:3px 8px; pointer-events:none; backdrop-filter:blur(4px);
}
@media (max-width: 760px) {
  .block-container { padding-left:.75rem; padding-right:.75rem; padding-top:.45rem; }
  .qlda-v7-hero { align-items:flex-start; padding:12px 13px; }
  .qlda-v7-title { font-size:1.18rem; }
  .qlda-v7-project { font-size:.9rem; }
  .qlda-v7-user { display:none; }
  .qlda-v7-credit { right:8px; bottom:5px; font-size:9px; }
}
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
        from contractor_workspace_v622 import contractor_scope_stats

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
