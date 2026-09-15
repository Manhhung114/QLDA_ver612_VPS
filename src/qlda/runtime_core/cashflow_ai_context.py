from __future__ import annotations

"""Live V3 cashflow context for Công cụ -> Trợ lý AI.

The module respects the existing contractor ContextVar guard. It never creates a
second assistant and never writes forecast/source data while answering a question.
"""

from datetime import date, timedelta
import re
import unicodedata
from typing import Any

PATCH_MARKER = "V7.6 CASHFLOW SHARED AI CONTEXT V3"
MAX_DETAIL_ROWS = 90
MAX_ALERTS = 30


def _text(value: Any) -> str:
    return str(value or "").strip()


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or 0)
    except Exception:
        return float(default)


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
    text = unicodedata.normalize("NFKD", _text(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("đ", "d")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _money(value: Any) -> str:
    n = _float(value)
    if abs(n) >= 1_000_000_000:
        return f"{n / 1_000_000_000:,.3f} tỷ"
    if abs(n) >= 1_000_000:
        return f"{n / 1_000_000:,.2f} triệu"
    return f"{n:,.0f} VND"


def _parse_date(value: Any):
    from qlda.runtime_core.cashflow_forecast_v3 import _parse_date as parse
    return parse(value)


class _BuilderDB:
    def __init__(self, builder):
        self._builder = builder

    def connect(self):
        return self._builder.connect()


def _scope_workspaces(builder, project_id: int) -> list[dict[str, Any]]:
    pid = int(project_id)
    try:
        from qlda.runtime_core.contractor_access_control import _AI_WORKSPACE_SCOPE
        restricted = _AI_WORKSPACE_SCOPE.get()
    except Exception:
        restricted = None

    with builder.connect() as connection:
        if restricted:
            rid = int(restricted)
            try:
                row = connection.execute(
                    """SELECT pc.master_project_id,pc.workspace_project_id,pc.contractor_code,pc.contractor_name,
                              pc.contract_no,pc.package_name,p.code AS project_code,p.name AS project_name
                       FROM project_contractors pc JOIN projects p ON p.id=pc.workspace_project_id
                       WHERE pc.workspace_project_id=? LIMIT 1""",
                    (rid,),
                ).fetchone()
                if row:
                    return [_rowdict(row)]
            except Exception:
                pass
            try:
                row = connection.execute("SELECT id,code,name FROM projects WHERE id=? LIMIT 1", (rid,)).fetchone()
                d = _rowdict(row)
                if d:
                    return [{"master_project_id": rid, "workspace_project_id": rid,
                             "contractor_code": "", "contractor_name": "",
                             "contract_no": "", "package_name": "",
                             "project_code": d.get("code", ""), "project_name": d.get("name", "")}]
            except Exception:
                pass
            return []

        master_id = pid
        try:
            from qlda.runtime_core.contractor_workspace import resolve_master_project_id_connection
            master_id = int(resolve_master_project_id_connection(connection, pid) or pid)
        except Exception:
            pass
        try:
            rows = connection.execute(
                """SELECT pc.master_project_id,pc.workspace_project_id,pc.contractor_code,pc.contractor_name,
                          pc.contract_no,pc.package_name,p.code AS project_code,p.name AS project_name
                   FROM project_contractors pc JOIN projects p ON p.id=pc.workspace_project_id
                   WHERE pc.master_project_id=? AND pc.status='Đang hoạt động'
                   ORDER BY pc.is_default DESC,pc.id""",
                (master_id,),
            ).fetchall()
            data = [_rowdict(r) for r in rows]
            if data:
                return data
        except Exception:
            pass
        try:
            row = connection.execute("SELECT id,code,name FROM projects WHERE id=? LIMIT 1", (pid,)).fetchone()
            d = _rowdict(row)
            if d:
                return [{"master_project_id": pid, "workspace_project_id": pid,
                         "contractor_code": "", "contractor_name": "",
                         "contract_no": "", "package_name": "",
                         "project_code": d.get("code", ""), "project_name": d.get("name", "")}]
        except Exception:
            pass
    return []


def _requested_horizon(question: str) -> int:
    q = _norm(question)
    # Prefer explicit days.
    matches = re.findall(r"\b(30|60|90|120|180|270|365)\s*(?:ngay|day|days)\b", q)
    if matches:
        return int(matches[-1])
    m = re.search(r"\b(\d{1,2})\s*(?:thang|month|months)\b", q)
    if m:
        return max(30, min(365, int(m.group(1)) * 30))
    if "1 nam" in q or "mot nam" in q or "12 thang" in q or "365" in q:
        return 365
    if "9 thang" in q or "270" in q:
        return 270
    if "6 thang" in q or "180" in q:
        return 180
    if "4 thang" in q or "120" in q:
        return 120
    return 90


def _requested_scenario(question: str) -> str:
    q = _norm(question)
    if "optimistic" in q or "lac quan" in q:
        return "Optimistic"
    if "conservative" in q or "than trong" in q or "bao thu" in q:
        return "Conservative"
    return "Base"


def _delay_request(question: str) -> int:
    q = _norm(question)
    if not any(term in q for term in ("cham", "tre", "delay", "dich")):
        return 0
    m = re.search(r"\b(\d{1,3})\s*(?:ngay|day|days)\b", q)
    if not m:
        return 0
    return max(0, min(365, int(m.group(1))))


def _build_rows(db, workspace_id: int, scenario: str):
    import qlda.runtime_core.cashflow_forecast_v1 as v1
    import qlda.runtime_core.cashflow_forecast_v2 as v2

    # In Streamlit runtime V2 has patched the shared build function. Background AI
    # services may initialize without UI, so explicitly append V2 there.
    base = v1.build_forecast_rows(db, int(workspace_id), scenario=scenario)
    if getattr(v1, "_qlda_cashflow_forecast_v2_installed", False):
        return list(base)
    future, _ = v2.build_schedule_boq_rows(db, int(workspace_id), scenario=scenario)
    rows = list(base) + list(future)
    rows.sort(key=lambda r: (_parse_date(r.get("forecast_date")) or date.today(), _text(r.get("source_key"))))
    return rows


def _matching_contractor(question: str, rows: list[dict[str, Any]]) -> str:
    q = _norm(question)
    names = sorted({_text(r.get("contractor")) for r in rows if _text(r.get("contractor"))}, key=len, reverse=True)
    for name in names:
        n = _norm(name)
        if n and n in q:
            return name
    return ""


def _appendix(builder, project_id: int, question: str) -> str:
    import qlda.runtime_core.cashflow_forecast_v1 as v1
    import qlda.runtime_core.cashflow_forecast_v2 as v2
    import qlda.runtime_core.cashflow_forecast_v3 as v3

    workspaces = _scope_workspaces(builder, int(project_id))
    if not workspaces:
        return ""
    db = _BuilderDB(builder)
    horizon = _requested_horizon(question)
    scenario = _requested_scenario(question)
    start = date.today()
    end = start + timedelta(days=horizon)
    delay_days = _delay_request(question)

    lines = [
        "",
        "## DỰ TRÙ DÒNG TIỀN V3 — LIVE CLAIM + BOQ + TIẾN ĐỘ",
        f"Kỳ ưu tiên theo câu hỏi: {horizon} ngày ({start:%d/%m/%Y} → {end:%d/%m/%Y}); scenario={scenario}.",
        "QUY TẮC: Forecast là giá trị nghĩa vụ/khối lượng dự kiến; Expected = Forecast × xác suất scenario. Scenario không được tự ý thay đổi giá trị hợp đồng/Claim.",
        "V1 là Claim/Payment hiện hữu; V2 bổ sung KL đã làm chưa Claim và BOQ tương lai theo tiến độ, sau khi trừ phần đã nằm trong Claim lũy kế.",
    ]

    project_expected = project_forecast = project_plan = 0.0
    scenario_totals = {"Base": 0.0, "Optimistic": 0.0, "Conservative": 0.0}
    total_alerts = 0

    for ws in workspaces:
        wid = int(ws.get("workspace_project_id") or 0)
        if wid <= 0:
            continue
        code = _text(ws.get("contractor_code")) or f"WS-{wid}"
        name = _text(ws.get("contractor_name")) or _text(ws.get("project_name"))
        rows = _build_rows(db, wid, scenario)
        _, diagnostics = v2.build_schedule_boq_rows(db, wid, scenario=scenario)
        summary = v3.summarize_rows(rows, start, end)
        alerts = v3.build_alerts(db, wid, rows, diagnostics, start, end)
        monthly = v3.monthly_expected(rows, start, end)

        project_expected += _float(summary.get("expected"))
        project_forecast += _float(summary.get("forecast"))
        project_plan += _float(summary.get("plan"))
        total_alerts += len(alerts)

        scen = v3.scenario_summary(db, wid, start, end)
        for item in scen:
            scenario_totals[_text(item.get("scenario"))] = scenario_totals.get(_text(item.get("scenario")), 0.0) + _float(item.get("expected"))

        lines += [
            "",
            f"### [CASHFLOW-CONTRACTOR:{code}] {name} | workspace={wid} | HĐ={_text(ws.get('contract_no'))}",
            f"Kỳ {horizon} ngày: Plan={_money(summary.get('plan'))} | Forecast={_money(summary.get('forecast'))} | Expected={_money(summary.get('expected'))} | số dòng={summary.get('rows',0)}.",
            f"BOQ liên kết={diagnostics.get('linked_rows',0)} dòng | chưa liên kết={diagnostics.get('unlinked_rows',0)} dòng/{_money(diagnostics.get('unlinked_value'))} | payment lag={diagnostics.get('payment_lag_days',0)} ngày.",
        ]

        # Standard horizon ladder is useful even when the question is generic.
        horizon_parts = []
        for days in (30, 60, 90, 180, 365):
            s = v3.summarize_rows(rows, start, start + timedelta(days=days))
            horizon_parts.append(f"{days}d={_money(s.get('expected'))}")
        lines.append("Expected theo kỳ: " + " | ".join(horizon_parts))

        if monthly:
            lines.append("Expected theo tháng: " + " | ".join(f"{m['month']}={_money(m['expected'])}" for m in monthly[:18]))

        by_contractor: dict[str, float] = {}
        by_source: dict[str, float] = {}
        detailed = []
        for row in rows:
            d = _parse_date(row.get("forecast_date"))
            if not d or not (start <= d <= end):
                continue
            expected = max(0.0, _float(row.get("expected_amount")))
            by_contractor[_text(row.get("contractor")) or name] = by_contractor.get(_text(row.get("contractor")) or name, 0.0) + expected
            source = _text(row.get("source_type")) or "OTHER"
            by_source[source] = by_source.get(source, 0.0) + expected
            detailed.append(row)
        if by_source:
            lines.append("Theo nguồn: " + " | ".join(f"{k}={_money(v)}" for k, v in sorted(by_source.items(), key=lambda kv: kv[1], reverse=True)))

        for alert in alerts[:MAX_ALERTS]:
            lines.append(
                f"[CASHFLOW-ALERT:{code}:{alert.get('code','')}] {alert.get('severity','')} | {alert.get('title','')} | giá trị={_money(alert.get('amount'))} | {alert.get('detail','')}"
            )

        detailed.sort(key=lambda r: (_parse_date(r.get("forecast_date")) or end, -_float(r.get("expected_amount"))))
        for row in detailed[:MAX_DETAIL_ROWS]:
            d = _parse_date(row.get("forecast_date"))
            ref = _text(row.get("claim_code")) or _text(row.get("source_key"))
            lines.append(
                f"[CASHFLOW:{code}:{ref}] ngày={d:%d/%m/%Y} | nguồn={row.get('source_type','')} | trạng thái={row.get('status','')} | "
                f"Forecast={_money(row.get('outstanding'))} | xác suất={_float(row.get('probability'))*100:.0f}% | Expected={_money(row.get('expected_amount'))} | "
                f"task={row.get('task_ref','')} | BOQ={row.get('boq_item','')} | ghi chú={row.get('note','')}"
            )

        if delay_days > 0:
            contractor = _matching_contractor(question, rows)
            base_month = {x["month"]: x["expected"] for x in v3.monthly_expected(rows, start, end)}
            delayed_month = {x["month"]: x["expected"] for x in v3.monthly_expected(rows, start, end, shift_days=delay_days, contractor=contractor)}
            months = sorted(set(base_month) | set(delayed_month))
            scope_label = contractor or "toàn bộ nguồn BOQ + tiến độ"
            lines.append(f"### [CASHFLOW-DELAY-SIM:{code}] Mô phỏng chậm {delay_days} ngày cho {scope_label}")
            for month in months:
                base_value = base_month.get(month, 0.0)
                delayed_value = delayed_month.get(month, 0.0)
                if abs(delayed_value - base_value) > 1.0:
                    lines.append(f"{month}: Base={_money(base_value)} | Sau trễ={_money(delayed_value)} | Δ={_money(delayed_value-base_value)}")

    lines += [
        "",
        "### [CASHFLOW-PROJECT-TOTAL] TỔNG PHẠM VI ĐƯỢC PHÉP ĐỌC",
        f"Plan={_money(project_plan)} | Forecast={_money(project_forecast)} | Expected({scenario})={_money(project_expected)} | cảnh báo={total_alerts}.",
        "Scenario Expected: " + " | ".join(f"{key}={_money(value)}" for key, value in scenario_totals.items()),
        "Khi người dùng hỏi nhu cầu vốn, ưu tiên Expected cho scenario được yêu cầu; đồng thời nêu Forecast để người dùng thấy nghĩa vụ/dải rủi ro. Không gọi Expected là số tiền chắc chắn phải trả.",
    ]
    return "\n".join(lines)


def install_cashflow_ai_context() -> None:
    import qlda.runtime_core.ai_service as ai_service

    cls = ai_service.ProjectContextBuilder
    if getattr(cls, "_qlda_cashflow_ai_context_installed", False):
        return
    original_build = cls.build

    def build_with_cashflow(self, project_id: int, question: str = "", status_date=None,
                            max_tasks: int = 80, max_docs: int = 70,
                            max_drawings: int = 60, max_legal: int = 40) -> str:
        snapshot = original_build(
            self, project_id, question, status_date,
            max_tasks=max_tasks, max_docs=max_docs,
            max_drawings=max_drawings, max_legal=max_legal,
        )
        try:
            appendix = _appendix(self, int(project_id), _text(question))
        except Exception as exc:
            appendix = f"\n## DỰ TRÙ DÒNG TIỀN V3\nKhông đọc được forecast live ở lượt này: {exc}"
        return snapshot.rstrip() + ("\n" + appendix if appendix else "") + "\n"

    cls.build = build_with_cashflow
    cls._qlda_cashflow_ai_context_installed = True
    cls._qlda_cashflow_ai_context_marker = PATCH_MARKER


__all__ = ["install_cashflow_ai_context"]
