from __future__ import annotations

from datetime import date, datetime
from html import escape
from typing import Any, Callable


PATCH_MARKER = "V6.23 UI UX RUNTIME V1"
_PENDING_NAV_KEY = "qlda_v623_pending_navigation"


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


def _text(value: Any) -> str:
    return str(value or "").strip()


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
    text = _text(value)
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except Exception:
            pass
    return None


def _actor(identity: Any) -> dict[str, str]:
    row = _rowdict(identity)
    return {
        "email": _text(row.get("email")).lower(),
        "name": _text(row.get("name") or row.get("email") or "Người dùng"),
        "role": _text(row.get("role")).lower(),
        "approval_role": _text(row.get("approval_role")).upper(),
    }


def _is_manager(identity: Any) -> bool:
    actor = _actor(identity)
    return actor["role"] == "admin" or actor["approval_role"] in {"SITE_MANAGEMENT", "PROJECT_MANAGEMENT"}


def _rerun(st) -> None:
    fn = getattr(st, "rerun", None) or getattr(st, "experimental_rerun", None)
    if callable(fn):
        fn()


def install_theme_v623(st) -> None:
    """Add a modern responsive layer on top of the proven V7 visual theme."""
    st.markdown(
        """
<style>
:root {
  --q623-primary:#1857a4;
  --q623-primary-soft:#edf4ff;
  --q623-success:#16803c;
  --q623-warning:#b54708;
  --q623-danger:#b42318;
  --q623-surface:#ffffff;
  --q623-border:#dce4ee;
  --q623-shadow:0 8px 24px rgba(16,24,40,.055);
}
.block-container { max-width:1720px; }
.q623-hero {
  display:flex; align-items:center; justify-content:space-between; gap:18px;
  background:linear-gradient(135deg,#17365d 0%,#245f9e 64%,#3575b9 100%);
  color:white; border-radius:18px; padding:17px 20px; margin:.05rem 0 .9rem;
  box-shadow:0 10px 28px rgba(23,54,93,.18);
}
.q623-brand {font-size:1.34rem;font-weight:800;letter-spacing:.2px;line-height:1.1}
.q623-project {font-size:1rem;font-weight:650;margin-top:6px}
.q623-sub {font-size:.82rem;opacity:.84;margin-top:3px}
.q623-user {text-align:right;font-size:.82rem;line-height:1.45;white-space:nowrap}
.q623-role {display:inline-block;background:rgba(255,255,255,.16);border:1px solid rgba(255,255,255,.28);padding:3px 9px;border-radius:999px;font-weight:700;margin-bottom:3px}
.q623-panel {background:var(--q623-surface);border:1px solid var(--q623-border);border-radius:16px;padding:14px 16px;margin:.65rem 0;box-shadow:0 1px 2px rgba(16,24,40,.025)}
.q623-panel-title {font-size:1rem;font-weight:760;color:#17365d;margin-bottom:9px}
.q623-alerts {display:flex;gap:7px;flex-wrap:wrap}
.q623-chip {display:inline-flex;align-items:center;gap:5px;border-radius:999px;padding:5px 10px;font-size:.8rem;font-weight:650;border:1px solid #ead7c5;background:#fff8f0;color:#8a3d05}
.q623-chip.bad {border-color:#f1c7c4;background:#fff4f3;color:#a42318}
.q623-chip.good {border-color:#cbe7d3;background:#f0faf3;color:#166534}
.q623-chip.info {border-color:#cbdcf2;background:#f2f7fd;color:#245f9e}
.q623-result {border:1px solid var(--q623-border);border-radius:12px;padding:9px 11px;margin:6px 0;background:#fff}
.q623-result-code {font-weight:750;color:#17365d}
.q623-result-title {font-size:.84rem;color:#667085;margin-top:2px}

/* Main horizontal section selector reads like product tabs instead of raw radios. */
[data-testid="stMainBlockContainer"] [data-testid="stRadio"] > div[role="radiogroup"] {
  gap:.35rem; flex-wrap:wrap; background:#eef3f8; padding:5px; border-radius:12px;
}
[data-testid="stMainBlockContainer"] [data-testid="stRadio"] > div[role="radiogroup"] label {
  background:white; border:1px solid #dce4ee; border-radius:9px; padding:4px 9px;
}
[data-testid="stMainBlockContainer"] [data-testid="stRadio"] > div[role="radiogroup"] label:has(input:checked) {
  border-color:#9cbce2; background:#edf4ff; box-shadow:0 1px 2px rgba(16,24,40,.05);
}
[data-testid="stSidebar"] .stRadio label {padding-top:3px;padding-bottom:3px}
[data-testid="stMetric"] { transition:transform .12s ease, box-shadow .12s ease; }
[data-testid="stMetric"]:hover { transform:translateY(-1px); box-shadow:var(--q623-shadow); }
.stButton > button[kind="primary"] {box-shadow:0 3px 9px rgba(24,87,164,.13)}

@media (max-width:760px) {
  .q623-hero {padding:13px 14px;border-radius:14px;align-items:flex-start}
  .q623-brand {font-size:1.08rem}
  .q623-project {font-size:.9rem}
  .q623-user {display:none}
  .q623-panel {padding:11px 12px;border-radius:13px}
  [data-testid="stMetric"] {padding:9px 10px}
  [data-testid="stMetricValue"] {font-size:1.35rem}
  [data-testid="stDataFrame"], [data-testid="stDataEditor"] {font-size:.82rem}
}
</style>
        """,
        unsafe_allow_html=True,
    )


def render_header_v623(st, project: Any, contractor_ctx: Any, identity: Any) -> None:
    p = _rowdict(project)
    contractor = _rowdict(contractor_ctx)
    actor = _actor(identity)
    project_code = escape(_text(p.get("code")))
    project_name = escape(_text(p.get("name")) or "Dự án")
    contractor_code = _text(contractor.get("contractor_code"))
    contractor_name = _text(contractor.get("contractor_name"))
    workspace = escape(" · ".join(x for x in (contractor_code, contractor_name) if x) or "Workspace dự án")
    role_label = {
        "admin": "Admin",
        "update": "Cập nhật",
        "read": "Chỉ xem",
    }.get(actor["role"], actor["approval_role"].replace("_", " ").title() or "Người dùng")
    st.markdown(
        f"""
<div class="q623-hero">
  <div>
    <div class="q623-brand">🏗️ QLDA XÂY DỰNG <span style="font-size:.68rem;opacity:.72">V6.23</span></div>
    <div class="q623-project">{project_code}{' · ' if project_code else ''}{project_name}</div>
    <div class="q623-sub">{workspace}</div>
  </div>
  <div class="q623-user"><span class="q623-role">{escape(role_label)}</span><br>{escape(actor['name'])}</div>
</div>
        """,
        unsafe_allow_html=True,
    )


def request_navigation_v623(st, group: str, section: str = "") -> None:
    st.session_state[_PENDING_NAV_KEY] = {"group": _text(group), "section": _text(section)}
    _rerun(st)


def apply_pending_navigation_v623(st, master_project_id: int) -> None:
    """Apply navigation before V7 widgets are instantiated on the next rerun."""
    target = st.session_state.pop(_PENDING_NAV_KEY, None)
    if not isinstance(target, dict):
        return
    group = _text(target.get("group"))
    section = _text(target.get("section"))
    if group:
        st.session_state[f"qlda_v7_group_{int(master_project_id)}"] = group
    if group and section:
        st.session_state[f"qlda_v7_section_{int(master_project_id)}_{group}"] = section


def _schedule_summary(db, pid: int) -> tuple[float, float, int]:
    try:
        rows = list(db.tasks(int(pid)) or [])
    except Exception:
        rows = []
    if not rows:
        return 0.0, 0.0, 0
    planned = sum(_safe_float(_rowdict(r).get("planned_progress")) for r in rows) / len(rows)
    actual = sum(_safe_float(_rowdict(r).get("actual_progress")) for r in rows) / len(rows)
    delayed = sum(1 for r in rows if _text(_rowdict(r).get("status")) == "Chậm tiến độ")
    return planned, actual, delayed


def _pending_documents(db, pid: int, doc_config: dict[str, Any]) -> int:
    total = 0
    for doc_type, cfg in (doc_config or {}).items():
        try:
            rows = list(db.documents(int(pid), doc_type) or [])
        except Exception:
            continue
        done = set((cfg or {}).get("done_statuses") or [])
        total += sum(1 for row in rows if _text(_rowdict(row).get("status")) not in done)
    return total


def _pending_drawings(db, pid: int, drawing_types: dict[str, Any]) -> int:
    terminal = {"Chấp thuận", "Chấp thuận có điều kiện", "Hủy", "Thay thế"}
    total = 0
    for kind in (drawing_types or {}):
        try:
            rows = list(db.drawings(int(pid), kind) or [])
        except Exception:
            continue
        total += sum(1 for row in rows if _text(_rowdict(row).get("status")) not in terminal)
    return total


def _late_materials(db, pid: int) -> int:
    try:
        rows = list(db.procurements(int(pid)) or [])
    except Exception:
        return 0
    today = date.today()
    count = 0
    for raw in rows:
        row = _rowdict(raw)
        status = _text(row.get("status"))
        if status == "Chậm":
            count += 1
            continue
        if status in {"Đã về công trường", "Hủy"}:
            continue
        planned = _safe_date(row.get("planned_delivery_date"))
        actual = _safe_date(row.get("actual_delivery_date"))
        if planned and not actual and today > planned:
            count += 1
    return count


def _finance_summary(db, pid: int) -> tuple[str, float]:
    try:
        from contractor_workspace_v622 import contractor_scope_stats
        stats = contractor_scope_stats(db, int(pid))
        return _text(stats.get("latest_ipc")) or "—", _safe_float(stats.get("boq_total"))
    except Exception:
        return "—", 0.0


def _work_context(db, pid: int, identity: Any) -> tuple[list[dict[str, Any]], dict[str, int]]:
    try:
        from work_tasks_v1_v622 import TERMINAL_STATUSES, is_overdue, list_work_tasks
        rows = list(list_work_tasks(db, int(pid)) or [])
    except Exception:
        return [], {"mine": 0, "mine_active": 0, "mine_overdue": 0, "overdue": 0, "waiting": 0}
    actor = _actor(identity)
    terminal = set(TERMINAL_STATUSES)
    mine = [r for r in rows if actor["email"] and _text(r.get("assignee_email")).lower() == actor["email"]]
    return rows, {
        "mine": len(mine),
        "mine_active": sum(1 for r in mine if _text(r.get("status")).upper() not in terminal),
        "mine_overdue": sum(1 for r in mine if is_overdue(r)),
        "overdue": sum(1 for r in rows if is_overdue(r)),
        "waiting": sum(1 for r in rows if _text(r.get("status")).upper() == "CHỜ XÁC NHẬN"),
    }


def _task_notifications(rows: list[dict[str, Any]], identity: Any, manager: bool) -> list[dict[str, Any]]:
    try:
        from work_tasks_v1_v622 import is_overdue
    except Exception:
        return []
    actor = _actor(identity)
    candidates: list[tuple[int, str, dict[str, Any]]] = []
    for row in rows:
        status = _text(row.get("status")).upper()
        mine = actor["email"] and _text(row.get("assignee_email")).lower() == actor["email"]
        overdue = is_overdue(row)
        waiting = status == "CHỜ XÁC NHẬN"
        if not ((mine and status not in {"HOÀN THÀNH", "ĐÓNG", "HỦY"}) or (manager and waiting)):
            continue
        priority = 0 if overdue else (1 if waiting else 2)
        candidates.append((priority, _text(row.get("due_at")), row))
    candidates.sort(key=lambda x: (x[0], x[1]))
    return [item[2] for item in candidates[:5]]


def _render_quick_search(st, db, pid: int, master_project_id: int) -> None:
    st.markdown('<div class="q623-panel-title">🔎 Tìm nhanh trong workspace</div>', unsafe_allow_html=True)
    key = f"q623_search_{int(pid)}"
    with st.form(key, clear_on_submit=False):
        c1, c2 = st.columns([3.2, 1])
        query = c1.text_input("Tìm mã hoặc tên", placeholder="VD: IPC-05, RFI-012, BOQ, tên hồ sơ...", label_visibility="collapsed")
        module = c2.selectbox("Phạm vi", ["Tất cả", "Tiến độ", "Hồ sơ", "Bản vẽ", "BOQ", "IPC/Claim", "VO"], label_visibility="collapsed")
        submitted = st.form_submit_button("Tìm", type="primary", use_container_width=True)
    state_key = f"q623_search_results_{int(pid)}"
    if submitted:
        q = _text(query).lower()
        results: list[dict[str, str]] = []
        if len(q) >= 2:
            try:
                from work_tasks_v1_v622 import source_catalog
                modules = [module] if module != "Tất cả" else ["Tiến độ", "Hồ sơ", "Bản vẽ", "BOQ", "IPC/Claim", "VO"]
                for name in modules:
                    for item in source_catalog(db, int(pid), name):
                        hay = f"{item.get('code','')} {item.get('title','')} {item.get('type','')}".lower()
                        if q in hay:
                            results.append({"module": name, **item})
                        if len(results) >= 30:
                            break
                    if len(results) >= 30:
                        break
            except Exception:
                results = []
        st.session_state[state_key] = results
    results = st.session_state.get(state_key, [])
    if submitted and not results:
        st.info("Không tìm thấy dữ liệu phù hợp trong workspace hiện tại.")
    if results:
        nav_map = {
            "Tiến độ": ("🏗️ Thi công", "📅 Tiến độ"),
            "Hồ sơ": ("📁 Hồ sơ", "📁 Hồ sơ"),
            "Bản vẽ": ("📁 Hồ sơ", "📐 Bản vẽ"),
            "BOQ": ("💰 Tài chính", "💰 Chi phí"),
            "IPC/Claim": ("💰 Tài chính", "💰 Chi phí"),
            "VO": ("💰 Tài chính", "💰 Chi phí"),
        }
        st.markdown(f"**{len(results)} kết quả**")
        for idx, item in enumerate(results[:12]):
            c1, c2 = st.columns([5.5, 1])
            c1.markdown(
                f'<div class="q623-result"><div class="q623-result-code">{escape(_text(item.get("module")))} · {escape(_text(item.get("code")) or "—")}</div>'
                f'<div class="q623-result-title">{escape(_text(item.get("title")) or "Không có tiêu đề")}</div></div>',
                unsafe_allow_html=True,
            )
            target = nav_map.get(_text(item.get("module")))
            if target and c2.button("Mở", key=f"q623_open_search_{pid}_{idx}", use_container_width=True):
                st.session_state[_PENDING_NAV_KEY] = {"group": target[0], "section": target[1]}
                _rerun(st)


def render_overview_v623(
    st,
    db,
    pid: int,
    *,
    master_project_id: int,
    identity: Any,
    doc_config: dict[str, Any] | None = None,
    drawing_types: dict[str, Any] | None = None,
    detailed_renderer: Callable[[int], None] | None = None,
) -> None:
    """Role-aware command center. Read-only summaries; no business data is mutated."""
    actor = _actor(identity)
    manager = _is_manager(identity)
    planned, actual, delayed = _schedule_summary(db, int(pid))
    pending_docs = _pending_documents(db, int(pid), doc_config or {})
    pending_drawings = _pending_drawings(db, int(pid), drawing_types or {})
    late_materials = _late_materials(db, int(pid))
    latest_ipc, boq_total = _finance_summary(db, int(pid))
    task_rows, work = _work_context(db, int(pid), identity)

    st.markdown('<div class="q623-panel-title">Trung tâm điều hành</div>', unsafe_allow_html=True)
    c1, c2, c3, c4, c5 = st.columns(5)
    if manager:
        c1.metric("Tiến độ thực tế", f"{actual:.1f}%", f"{actual - planned:+.1f}% so KH")
        c2.metric("Công việc quá hạn", work["overdue"])
        c3.metric("Chờ xác nhận", work["waiting"])
        c4.metric("Hồ sơ chờ xử lý", pending_docs)
        c5.metric("IPC mới nhất", latest_ipc)
    else:
        c1.metric("Việc của tôi", work["mine_active"])
        c2.metric("Quá hạn của tôi", work["mine_overdue"])
        c3.metric("Tiến độ thực tế", f"{actual:.1f}%", f"{actual - planned:+.1f}% so KH")
        c4.metric("Hồ sơ đang xử lý", pending_docs)
        c5.metric("IPC mới nhất", latest_ipc)

    alerts: list[tuple[str, str]] = []
    if work["overdue"]:
        alerts.append(("bad", f"{work['overdue']} công việc quá hạn"))
    if manager and work["waiting"]:
        alerts.append(("info", f"{work['waiting']} việc chờ xác nhận"))
    if delayed:
        alerts.append(("bad", f"{delayed} hạng mục chậm tiến độ"))
    if pending_drawings:
        alerts.append(("", f"{pending_drawings} bản vẽ đang xử lý"))
    if late_materials:
        alerts.append(("", f"{late_materials} vật tư/mua sắm trễ"))
    if pending_docs:
        alerts.append(("info", f"{pending_docs} hồ sơ chưa kết thúc"))
    if not alerts:
        alerts.append(("good", "Không có cảnh báo chính từ dữ liệu hiện tại"))
    chips = "".join(f'<span class="q623-chip {kind}">{escape(label)}</span>' for kind, label in alerts)
    boq_label = f"BOQ {boq_total / 1_000_000_000:.2f} tỷ" if boq_total else "BOQ chưa có tổng giá trị"
    st.markdown(
        f'<div class="q623-panel"><div class="q623-panel-title">⚡ Cần chú ý</div><div class="q623-alerts">{chips}<span class="q623-chip info">{escape(boq_label)}</span></div></div>',
        unsafe_allow_html=True,
    )

    notifications = _task_notifications(task_rows, identity, manager)
    left, right = st.columns([1.15, 1])
    with left:
        st.markdown('<div class="q623-panel-title">🔔 Việc cần xử lý</div>', unsafe_allow_html=True)
        if notifications:
            for row in notifications:
                try:
                    from work_tasks_v1_v622 import is_overdue
                    overdue = is_overdue(row)
                except Exception:
                    overdue = False
                status = "QUÁ HẠN" if overdue else _text(row.get("status"))
                st.markdown(
                    f"**{escape(_text(row.get('task_code')))} · {escape(_text(row.get('title')))}**  \n"
                    f"{escape(status)} · Hạn: {escape(_text(row.get('due_at')) or '—')}"
                )
            if st.button("Mở Trung tâm công việc", key=f"q623_open_work_{pid}", type="primary", use_container_width=True):
                st.session_state[_PENDING_NAV_KEY] = {"group": "📋 Công việc", "section": ""}
                _rerun(st)
        else:
            st.success("Không có công việc khẩn cần xử lý.")
    with right:
        _render_quick_search(st, db, int(pid), int(master_project_id))

    if detailed_renderer is not None:
        with st.expander("📊 Phân tích chi tiết / biểu đồ", expanded=False):
            detailed_renderer(int(pid))
