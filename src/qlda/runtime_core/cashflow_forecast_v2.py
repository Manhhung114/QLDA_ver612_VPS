from __future__ import annotations

"""V2 dự trù dòng tiền: bổ sung BOQ + tiến độ vào forecast V1.

V1 vẫn là nguồn authoritative cho Claim/Payment đang có. V2 chỉ sinh phần dòng
tiền tương lai chưa nằm trong Claim lũy kế, dựa trên BOQ liên kết công việc và
tiến độ thực tế/kế hoạch. Như vậy không cộng trùng giá trị đã được chứng nhận.
"""

from datetime import date, datetime, timedelta
import math
import re
import unicodedata
from typing import Any

PATCH_MARKER = "V7.6 CASHFLOW FORECAST V2 BOQ SCHEDULE"
SETTINGS_TABLE = "cashflow_forecast_v2_settings"

DEFAULTS = {
    "claim_preparation_days": 5,
    "approval_days": 7,
    "forecast_cycle_days": 30,
    "prob_earned_unclaimed": 0.70,
    "prob_schedule_boq": 0.50,
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or 0)
    except Exception:
        return float(default)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value or 0)
    except Exception:
        return int(default)


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


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = _text(value)
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text[:19] if "%H" in fmt else text[:10], fmt).date()
        except Exception:
            pass
    return None


def _date_text(value: Any) -> str:
    d = _parse_date(value)
    return d.strftime("%d/%m/%Y") if d else ""


def _money(value: Any) -> str:
    n = _float(value)
    sign = "-" if n < 0 else ""
    n = abs(n)
    if n >= 1_000_000_000:
        return f"{sign}{n / 1_000_000_000:,.2f} tỷ"
    if n >= 1_000_000:
        return f"{sign}{n / 1_000_000:,.1f} triệu"
    return f"{sign}{n:,.0f} đ"


def _actor_name(identity: Any) -> str:
    row = _rowdict(identity)
    return _text(row.get("name") or row.get("email") or "Người dùng")


def _table_exists(connection, table: str) -> bool:
    try:
        connection.execute(f"SELECT 1 FROM {table} LIMIT 1")
        return True
    except Exception:
        return False


def ensure_schema(db) -> None:
    with db.connect() as connection:
        connection.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {SETTINGS_TABLE}(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                master_project_id INTEGER NOT NULL,
                workspace_project_id INTEGER NOT NULL UNIQUE,
                claim_preparation_days INTEGER NOT NULL DEFAULT 5,
                approval_days INTEGER NOT NULL DEFAULT 7,
                forecast_cycle_days INTEGER NOT NULL DEFAULT 30,
                prob_earned_unclaimed REAL NOT NULL DEFAULT 0.70,
                prob_schedule_boq REAL NOT NULL DEFAULT 0.50,
                updated_by TEXT DEFAULT '',
                created_at TEXT DEFAULT '',
                updated_at TEXT DEFAULT ''
            )
            """
        )
        connection.execute(
            f"CREATE INDEX IF NOT EXISTS idx_cashflow_v2_settings_master "
            f"ON {SETTINGS_TABLE}(master_project_id,workspace_project_id)"
        )


def get_settings(db, workspace_project_id: int) -> dict[str, Any]:
    import qlda.runtime_core.cashflow_forecast_v1 as v1

    ensure_schema(db)
    pid = int(workspace_project_id)
    scope = v1._resolve_scope(db, pid)
    with db.connect() as connection:
        row = connection.execute(
            f"SELECT * FROM {SETTINGS_TABLE} WHERE workspace_project_id=? LIMIT 1", (pid,)
        ).fetchone()
        if not row:
            stamp = v1._now()
            connection.execute(
                f"""INSERT INTO {SETTINGS_TABLE}(
                       master_project_id,workspace_project_id,claim_preparation_days,approval_days,
                       forecast_cycle_days,prob_earned_unclaimed,prob_schedule_boq,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    int(scope["master_project_id"]), pid,
                    int(DEFAULTS["claim_preparation_days"]), int(DEFAULTS["approval_days"]),
                    int(DEFAULTS["forecast_cycle_days"]), float(DEFAULTS["prob_earned_unclaimed"]),
                    float(DEFAULTS["prob_schedule_boq"]), stamp, stamp,
                ),
            )
            row = connection.execute(
                f"SELECT * FROM {SETTINGS_TABLE} WHERE workspace_project_id=? LIMIT 1", (pid,)
            ).fetchone()
    out = _rowdict(row)
    for key, value in DEFAULTS.items():
        out.setdefault(key, value)
    return out


def save_settings(db, workspace_project_id: int, values: dict[str, Any], *, actor: Any = None) -> None:
    import qlda.runtime_core.cashflow_forecast_v1 as v1

    current = get_settings(db, int(workspace_project_id))
    prep = max(0, min(90, _int(values.get("claim_preparation_days"), _int(current.get("claim_preparation_days"), 5))))
    approval = max(0, min(180, _int(values.get("approval_days"), _int(current.get("approval_days"), 7))))
    cycle = max(7, min(90, _int(values.get("forecast_cycle_days"), _int(current.get("forecast_cycle_days"), 30))))
    earned = max(0.0, min(1.0, _float(values.get("prob_earned_unclaimed"), _float(current.get("prob_earned_unclaimed"), 0.70))))
    schedule = max(0.0, min(1.0, _float(values.get("prob_schedule_boq"), _float(current.get("prob_schedule_boq"), 0.50))))
    with db.connect() as connection:
        connection.execute(
            f"""UPDATE {SETTINGS_TABLE}
                SET claim_preparation_days=?,approval_days=?,forecast_cycle_days=?,
                    prob_earned_unclaimed=?,prob_schedule_boq=?,updated_by=?,updated_at=?
                WHERE workspace_project_id=?""",
            (prep, approval, cycle, earned, schedule, _actor_name(actor), v1._now(), int(workspace_project_id)),
        )


def _task_ref(task: dict[str, Any]) -> str:
    task_id = task.get("source_task_id") or task.get("id") or ""
    return f"[TASK:{task_id}/{_text(task.get('wbs'))}]"


def _actual_progress(task: dict[str, Any]) -> float:
    override = task.get("actual_override")
    value = override if override not in (None, "") else task.get("actual_progress")
    return max(0.0, min(100.0, _float(value)))


def _planned_progress(task: dict[str, Any], on_date: date) -> float:
    start = _parse_date(task.get("start_date"))
    finish = _parse_date(task.get("end_date"))
    if not start or not finish:
        return max(0.0, min(100.0, _float(task.get("planned_progress"))))
    if on_date < start:
        return 0.0
    if on_date >= finish:
        return 100.0
    total = max(1, (finish - start).days + 1)
    elapsed = max(0, (on_date - start).days + 1)
    return max(0.0, min(100.0, elapsed * 100.0 / total))


def _projected_finish(task: dict[str, Any], today: date) -> date | None:
    start = _parse_date(task.get("start_date"))
    finish = _parse_date(task.get("end_date"))
    actual = _actual_progress(task)
    if actual >= 100.0:
        return _parse_date(task.get("actual_finish_date")) or today
    if not start or not finish:
        return finish
    if today < start:
        return finish

    duration = max(1, (finish - start).days + 1)
    planned = _planned_progress(task, today)
    plan_rate = 100.0 / duration

    if today <= finish:
        if actual + 1.0 >= planned:
            return finish
        delay_pct = max(0.0, planned - actual)
        delay_days = int(math.ceil(delay_pct / max(plan_rate, 0.01)))
        return finish + timedelta(days=delay_days)

    if actual > 0:
        elapsed = max(1, (today - start).days + 1)
        actual_rate = actual / elapsed
        remaining_days = int(math.ceil((100.0 - actual) / max(actual_rate, 0.05)))
    else:
        remaining_days = duration
    return today + timedelta(days=max(1, remaining_days))


def _projected_progress(task: dict[str, Any], cutoff: date, today: date, projected_finish: date) -> float:
    actual = _actual_progress(task)
    start = _parse_date(task.get("start_date")) or today
    if cutoff <= today:
        return actual

    baseline_date = today
    baseline_progress = actual
    if start > today:
        if cutoff < start:
            return 0.0
        baseline_date = start
        baseline_progress = 0.0
    if cutoff >= projected_finish:
        return 100.0

    remaining_days = max(1, (projected_finish - baseline_date).days)
    elapsed = max(0, (cutoff - baseline_date).days)
    return max(
        baseline_progress,
        min(100.0, baseline_progress + (100.0 - baseline_progress) * elapsed / remaining_days),
    )


def _claim_rank(row: dict[str, Any]) -> tuple[int, str, str]:
    raw = _text(row.get("claim_no") or row.get("claim_code"))
    digits = re.findall(r"\d+", raw)
    num = int(digits[-1]) if digits else -1
    return num, _text(row.get("updated_at")), _text(row.get("created_at"))


def _latest_claim_certified_groups(connection, project_id: int) -> tuple[dict[tuple[str, str], float], float, str]:
    if not (_table_exists(connection, "payment_claims") and _table_exists(connection, "payment_claim_items")):
        return {}, 0.0, ""
    try:
        claims = [_rowdict(r) for r in connection.execute(
            "SELECT claim_id,claim_no,claim_code,certified_cumulative,updated_at,created_at "
            "FROM payment_claims WHERE project_id=?",
            (int(project_id),),
        ).fetchall()]
    except Exception:
        claims = []
    if not claims:
        return {}, 0.0, ""

    latest = max(claims, key=_claim_rank)
    claim_id = _text(latest.get("claim_id"))
    if not claim_id:
        return {}, max(0.0, _float(latest.get("certified_cumulative"))), _text(latest.get("claim_code"))
    try:
        items = [_rowdict(r) for r in connection.execute(
            "SELECT boq_item,unit,cumulative_value FROM payment_claim_items WHERE claim_id=? ORDER BY row_no",
            (claim_id,),
        ).fetchall()]
    except Exception:
        items = []

    groups: dict[tuple[str, str], float] = {}
    for item in items:
        key = (_norm(item.get("boq_item")), _norm(item.get("unit")))
        if not key[0]:
            continue
        groups[key] = groups.get(key, 0.0) + max(0.0, _float(item.get("cumulative_value")))
    return groups, max(0.0, _float(latest.get("certified_cumulative"))), _text(latest.get("claim_code"))


def _budget_and_task_data(db, project_id: int) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    pid = int(project_id)
    with db.connect() as connection:
        budgets = []
        if _table_exists(connection, "cost_budgets"):
            try:
                budgets = [_rowdict(r) for r in connection.execute(
                    "SELECT * FROM cost_budgets WHERE project_id=? ORDER BY id", (pid,)
                ).fetchall()]
            except Exception:
                budgets = []
        tasks = []
        if _table_exists(connection, "tasks"):
            try:
                tasks = [_rowdict(r) for r in connection.execute(
                    "SELECT * FROM tasks WHERE project_id=? AND COALESCE(is_summary,0)=0 ORDER BY start_date,id",
                    (pid,),
                ).fetchall()]
            except Exception:
                tasks = []
    return budgets, {_task_ref(t): t for t in tasks}


def _certified_share_by_budget(
    budgets: list[dict[str, Any]],
    claim_groups: dict[tuple[str, str], float],
    latest_certified_total: float,
    tasks_by_ref: dict[str, dict[str, Any]],
) -> dict[int, tuple[float, str]]:
    group_budgets: dict[tuple[str, str], float] = {}
    total_budget = 0.0
    for row in budgets:
        value = max(0.0, _float(row.get("budget_total")))
        total_budget += value
        key = (_norm(row.get("boq_item")), _norm(row.get("unit")))
        if key[0]:
            group_budgets[key] = group_budgets.get(key, 0.0) + value

    project_ratio = min(1.0, latest_certified_total / total_budget) if total_budget > 0 and latest_certified_total > 0 else 0.0
    out: dict[int, tuple[float, str]] = {}
    for row in budgets:
        rid = _int(row.get("id"))
        budget = max(0.0, _float(row.get("budget_total")))
        key = (_norm(row.get("boq_item")), _norm(row.get("unit")))
        group_claim = max(0.0, _float(claim_groups.get(key)))
        group_budget = max(0.0, _float(group_budgets.get(key)))
        if group_claim > 0 and group_budget > 0:
            share = min(budget, group_claim * budget / group_budget)
            method = "Claim item lũy kế"
        elif project_ratio > 0:
            task = tasks_by_ref.get(_text(row.get("task_ref")), {})
            actual_ratio = _actual_progress(task) / 100.0 if task else 1.0
            share = min(budget, budget * min(project_ratio, actual_ratio))
            method = "Fallback tỷ lệ Claim × tiến độ"
        else:
            share = 0.0
            method = "Chưa có Claim lũy kế"
        out[rid] = (max(0.0, share), method)
    return out


def _apply_override(auto_row: dict[str, Any], override: dict[str, Any], scenario: str, settings_v1: dict[str, Any]) -> dict[str, Any]:
    import qlda.runtime_core.cashflow_forecast_v1 as v1

    row = dict(auto_row)
    override_prob = _float(override.get("probability"), -1.0)
    base_prob = override_prob if 0.0 <= override_prob <= 1.0 else _float(row.get("base_probability"))
    row["base_probability"] = base_prob
    row["probability"] = v1._scenario_probability(base_prob, scenario, settings_v1)
    row["probability_reason"] = "Điều chỉnh thủ công" if 0.0 <= override_prob <= 1.0 else row.get("probability_reason", "")
    row["expected_amount"] = max(0.0, _float(row.get("outstanding"))) * row["probability"]
    manual_date = _parse_date(override.get("forecast_date"))
    if manual_date:
        row["forecast_date"] = max(date.today(), manual_date)
        row["due_date"] = manual_date
        row["due_source"] = "Điều chỉnh thủ công"
    planned = _float(override.get("planned_amount"))
    row["planned_amount"] = planned if planned > 0 else max(0.0, _float(row.get("outstanding")))
    row["note"] = _text(override.get("note") or row.get("note"))
    row["has_override"] = bool(override)
    return row


def build_schedule_boq_rows(db, workspace_project_id: int, *, scenario: str = "Base") -> tuple[list[dict[str, Any]], dict[str, Any]]:
    import qlda.runtime_core.cashflow_forecast_v1 as v1

    pid = int(workspace_project_id)
    today = date.today()
    max_cutoff = today + timedelta(days=365)
    settings_v1 = v1.get_settings(db, pid)
    settings_v2 = get_settings(db, pid)
    overrides = v1._load_overrides(db, pid)

    prep_days = max(0, _int(settings_v2.get("claim_preparation_days"), 5))
    approval_days = max(0, _int(settings_v2.get("approval_days"), 7))
    terms_days = max(0, _int(settings_v1.get("payment_terms_days"), 30))
    cycle_days = max(7, min(90, _int(settings_v2.get("forecast_cycle_days"), 30)))
    payment_lag = prep_days + approval_days + terms_days

    scope = v1._resolve_scope(db, pid)
    contractor_label = " - ".join(
        x for x in (_text(scope.get("contractor_code")), _text(scope.get("contractor_name"))) if x
    ) or "Workspace mặc định"

    budgets, tasks_by_ref = _budget_and_task_data(db, pid)
    with db.connect() as connection:
        claim_groups, latest_certified_total, latest_claim_code = _latest_claim_certified_groups(connection, pid)
    certified_map = _certified_share_by_budget(budgets, claim_groups, latest_certified_total, tasks_by_ref)

    rows: list[dict[str, Any]] = []
    diagnostics = {
        "boq_rows": len(budgets), "linked_rows": 0, "unlinked_rows": 0,
        "unlinked_value": 0.0, "forecast_value": 0.0,
        "latest_claim_code": latest_claim_code, "latest_certified_total": latest_certified_total,
        "payment_lag_days": payment_lag, "task_rows": [],
    }

    for budget_row in budgets:
        budget_id = _int(budget_row.get("id"))
        budget_total = max(0.0, _float(budget_row.get("budget_total")))
        if budget_total <= 0:
            continue
        task_ref = _text(budget_row.get("task_ref"))
        task = tasks_by_ref.get(task_ref)
        if not task:
            diagnostics["unlinked_rows"] += 1
            diagnostics["unlinked_value"] += budget_total
            continue

        diagnostics["linked_rows"] += 1
        actual = _actual_progress(task)
        planned_today = _planned_progress(task, today)
        projected_finish = _projected_finish(task, today)
        certified, certified_method = certified_map.get(budget_id, (0.0, "Chưa có Claim lũy kế"))
        if actual >= 100.0 and certified >= budget_total - 1e-6:
            continue
        if not projected_finish:
            diagnostics["unlinked_rows"] += 1
            diagnostics["unlinked_value"] += budget_total
            continue

        earned_now = min(budget_total, budget_total * actual / 100.0)
        accounted = min(budget_total, certified)
        backlog = max(0.0, earned_now - accounted)
        task_generated = 0.0

        if backlog > 1e-6:
            source_key = f"BOQ:{budget_id}:EARNED:{today.isoformat()}"
            base_prob = max(0.0, min(1.0, _float(settings_v2.get("prob_earned_unclaimed"), 0.70)))
            auto_date = today + timedelta(days=payment_lag)
            auto = {
                "source_type": "SCHEDULE_BOQ", "source_key": source_key,
                "claim_code": f"BOQ-{budget_id}/KL-CHƯA-CLAIM",
                "contractor": _text(budget_row.get("contractor")) or contractor_label,
                "contract_no": "", "package_name": "",
                "status": "Khối lượng đã thực hiện chưa vào Claim",
                "requested_amount": 0.0, "approved_amount": 0.0, "certified_amount": certified,
                "disbursed_amount": 0.0, "target_amount": backlog, "outstanding": backlog,
                "planned_amount": backlog, "base_probability": base_prob, "probability": base_prob,
                "probability_reason": "KL đã thực hiện chưa Claim", "forecast_date": auto_date,
                "due_date": auto_date, "due_source": f"KL hiện tại + {payment_lag} ngày",
                "overdue_days": 0, "expected_amount": backlog * base_prob,
                "retention_reference": 0.0, "advance_recovery_reference": 0.0,
                "deductions_reference": 0.0, "actual_date": None,
                "note": f"{budget_row.get('boq_item','')} | task={task_ref}", "has_override": False,
                "boq_id": budget_id, "boq_item": _text(budget_row.get("boq_item")),
                "task_ref": task_ref, "task_name": _text(task.get("name")),
                "task_actual_progress": actual, "task_planned_progress": planned_today,
                "task_projected_finish": projected_finish, "certified_method": certified_method,
                "generated_kind": "EARNED_UNCLAIMED",
            }
            row = _apply_override(auto, overrides.get(("SCHEDULE_BOQ", source_key), {}), scenario, settings_v1)
            rows.append(row)
            task_generated += backlog
            accounted = max(accounted, earned_now)

        if accounted < budget_total - 1e-6:
            cutoff = today + timedelta(days=cycle_days)
            cuts: list[date] = []
            final_cut = min(projected_finish, max_cutoff)
            while cutoff < final_cut:
                cuts.append(cutoff)
                cutoff += timedelta(days=cycle_days)
            if final_cut >= today and (not cuts or cuts[-1] != final_cut):
                cuts.append(final_cut)

            prior_cumulative = accounted
            for cutoff in cuts:
                progress = _projected_progress(task, cutoff, today, projected_finish)
                target_cumulative = min(budget_total, budget_total * progress / 100.0)
                increment = max(0.0, target_cumulative - prior_cumulative)
                if increment <= 1e-6:
                    continue
                source_key = f"BOQ:{budget_id}:CYCLE:{cutoff.isoformat()}"
                base_prob = max(0.0, min(1.0, _float(settings_v2.get("prob_schedule_boq"), 0.50)))
                auto_date = cutoff + timedelta(days=payment_lag)
                auto = {
                    "source_type": "SCHEDULE_BOQ", "source_key": source_key,
                    "claim_code": f"BOQ-{budget_id}/{cutoff:%Y%m%d}",
                    "contractor": _text(budget_row.get("contractor")) or contractor_label,
                    "contract_no": "", "package_name": "", "status": "Dự báo BOQ theo tiến độ",
                    "requested_amount": 0.0, "approved_amount": 0.0, "certified_amount": certified,
                    "disbursed_amount": 0.0, "target_amount": increment, "outstanding": increment,
                    "planned_amount": increment, "base_probability": base_prob, "probability": base_prob,
                    "probability_reason": "Tiến độ + BOQ", "forecast_date": auto_date,
                    "due_date": auto_date, "due_source": f"Mốc tiến độ {cutoff:%d/%m/%Y} + {payment_lag} ngày",
                    "overdue_days": 0, "expected_amount": increment * base_prob,
                    "retention_reference": 0.0, "advance_recovery_reference": 0.0,
                    "deductions_reference": 0.0, "actual_date": None,
                    "note": f"{budget_row.get('boq_item','')} | task={task_ref}", "has_override": False,
                    "boq_id": budget_id, "boq_item": _text(budget_row.get("boq_item")),
                    "task_ref": task_ref, "task_name": _text(task.get("name")),
                    "task_actual_progress": actual, "task_planned_progress": planned_today,
                    "task_projected_finish": projected_finish, "certified_method": certified_method,
                    "generated_kind": "FUTURE_SCHEDULE",
                }
                row = _apply_override(auto, overrides.get(("SCHEDULE_BOQ", source_key), {}), scenario, settings_v1)
                rows.append(row)
                task_generated += increment
                prior_cumulative = target_cumulative

        if task_generated > 1e-6:
            diagnostics["task_rows"].append({
                "boq_id": budget_id, "task_ref": task_ref, "task_name": _text(task.get("name")),
                "boq_item": _text(budget_row.get("boq_item")), "budget_total": budget_total,
                "certified": certified, "certified_method": certified_method,
                "actual_progress": actual, "planned_progress": planned_today,
                "planned_finish": _date_text(task.get("end_date")),
                "projected_finish": _date_text(projected_finish), "forecast_remaining": task_generated,
            })
            diagnostics["forecast_value"] += task_generated

    rows.sort(key=lambda r: (r.get("forecast_date") or today, _text(r.get("claim_code"))))
    return rows, diagnostics


_ORIGINAL_BUILD = None
_ORIGINAL_RENDER = None


def _build_forecast_rows_v2(db, workspace_project_id: int, *, scenario: str = "Base") -> list[dict[str, Any]]:
    global _ORIGINAL_BUILD
    base = _ORIGINAL_BUILD(db, int(workspace_project_id), scenario=scenario) if _ORIGINAL_BUILD else []
    future, _ = build_schedule_boq_rows(db, int(workspace_project_id), scenario=scenario)
    rows = list(base) + list(future)
    rows.sort(key=lambda r: (r.get("forecast_date") or date.today(), _text(r.get("source_type")), _text(r.get("source_key"))))
    return rows


def _render_schedule_diagnostics(st, diagnostics: dict[str, Any]) -> None:
    import pandas as pd

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("BOQ có liên kết tiến độ", f"{_int(diagnostics.get('linked_rows')):,} dòng")
    c2.metric("BOQ chưa liên kết", f"{_int(diagnostics.get('unlinked_rows')):,} dòng")
    c3.metric("Giá trị chưa liên kết", _money(diagnostics.get("unlinked_value")))
    c4.metric("Forecast từ BOQ", _money(diagnostics.get("forecast_value")))

    latest = _text(diagnostics.get("latest_claim_code")) or "Chưa có Claim"
    st.caption(
        f"Mốc trừ chống cộng trùng: {latest}; giá trị chứng nhận lũy kế "
        f"{_money(diagnostics.get('latest_certified_total'))}. "
        f"Độ trễ từ mốc khối lượng đến thanh toán: {_int(diagnostics.get('payment_lag_days'))} ngày."
    )
    data = diagnostics.get("task_rows") or []
    if data:
        view = pd.DataFrame([{
            "BOQ ID": r["boq_id"], "Task": r["task_name"], "Hạng mục BOQ": r["boq_item"],
            "Giá trị BOQ": r["budget_total"], "Đã chứng nhận": r["certified"],
            "Cách đối chiếu Claim": r["certified_method"], "TT (%)": r["actual_progress"],
            "KH hôm nay (%)": r["planned_progress"], "Kết thúc KH": r["planned_finish"],
            "Kết thúc dự báo": r["projected_finish"], "Giá trị forecast còn lại": r["forecast_remaining"],
        } for r in data])
        st.dataframe(view, hide_index=True, use_container_width=True)
    else:
        st.info("Chưa có BOQ liên kết công việc đủ dữ liệu để sinh forecast V2.")
    if _int(diagnostics.get("unlinked_rows")) > 0:
        st.warning(
            "Các dòng BOQ chưa gắn Task được loại khỏi forecast V2 để tránh tự gán ngày thanh toán sai. "
            "Hãy liên kết Task cho BOQ nếu muốn các giá trị này tham gia dự trù."
        )


def _render_detail_v2(st, db, pid: int, rows: list[dict[str, Any]], *, identity: Any, can_update: bool) -> None:
    import pandas as pd
    import qlda.runtime_core.cashflow_forecast_v1 as v1

    if not rows:
        st.info("Chưa có dữ liệu forecast.")
        return
    table = pd.DataFrame([{
        "Nguồn": r.get("source_type", ""), "Claim/BOQ": r.get("claim_code", ""),
        "Nhà thầu": r.get("contractor", ""), "Trạng thái": r.get("status", ""),
        "Task": r.get("task_name", ""), "BOQ": r.get("boq_item", ""),
        "Ngày đến hạn": _date_text(r.get("due_date")), "Ngày forecast": _date_text(r.get("forecast_date")),
        "Còn/Forecast": _float(r.get("outstanding")), "Xác suất": f"{_float(r.get('probability')) * 100:.0f}%",
        "Expected": _float(r.get("expected_amount")), "Đã giải ngân": _float(r.get("disbursed_amount")),
        "Nguồn ngày": r.get("due_source", ""), "Ghi chú": r.get("note", ""),
    } for r in rows])
    st.dataframe(table, hide_index=True, use_container_width=True)
    st.download_button(
        "⬇️ Xuất CSV forecast V2", data=table.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"cashflow_forecast_v2_{pid}_{date.today():%Y%m%d}.csv",
        mime="text/csv", use_container_width=True,
    )

    st.markdown("#### Điều chỉnh một khoản forecast")
    labels = {
        f"{r['source_type']}|{r['source_key']}": (
            f"{r.get('claim_code') or r.get('source_key')} • {r.get('contractor','')} • {_money(r.get('outstanding'))}"
        )
        for r in rows if _text(r.get("source_key"))
    }
    keys = list(labels)
    if not keys:
        return
    selected_key = st.selectbox(
        "Khoản cần điều chỉnh", keys, format_func=lambda key: labels.get(key, key),
        key=f"cashflow_v2_edit_select_{pid}",
    )
    current = next(r for r in rows if f"{r['source_type']}|{r['source_key']}" == selected_key)
    auto_date = not bool(current.get("has_override")) or _text(current.get("due_source")) != "Điều chỉnh thủ công"
    with st.form(f"cashflow_v2_override_{pid}_{current['source_type']}_{current['source_key']}"):
        use_auto_date = st.checkbox("Dùng ngày forecast tự động", value=auto_date)
        forecast_date_value = st.date_input(
            "Ngày forecast thanh toán", value=current.get("forecast_date") or date.today(), disabled=use_auto_date,
        )
        use_source_amount = st.checkbox(
            "Dùng Plan amount tự động",
            value=abs(_float(current.get("planned_amount")) - _float(current.get("outstanding"))) < 1e-6,
        )
        planned_amount = st.number_input(
            "Plan amount (VND)", min_value=0.0, value=float(current.get("planned_amount") or 0),
            step=1_000_000.0, disabled=use_source_amount,
        )
        auto_prob = st.checkbox(
            "Dùng xác suất tự động theo nguồn", value=_text(current.get("probability_reason")) != "Điều chỉnh thủ công",
        )
        prob_pct = st.number_input(
            "Xác suất Base (%)", min_value=0.0, max_value=100.0,
            value=float(current.get("base_probability") or 0) * 100.0, step=5.0, disabled=auto_prob,
        )
        note = st.text_area("Ghi chú forecast", value=_text(current.get("note")))
        save = st.form_submit_button("💾 Lưu điều chỉnh", disabled=not can_update, use_container_width=True)
        if save:
            v1.save_override(
                db, pid, current["source_type"], current["source_key"],
                forecast_date=None if use_auto_date else forecast_date_value,
                planned_amount=0.0 if use_source_amount else planned_amount,
                probability=-1.0 if auto_prob else prob_pct / 100.0,
                note=note, actor=identity,
            )
            st.success("Đã lưu điều chỉnh forecast V2.")
            st.rerun()
    if st.button(
        "↩️ Xóa điều chỉnh, dùng lại dữ liệu tự động",
        key=f"cashflow_v2_clear_{pid}_{current['source_type']}_{current['source_key']}",
        disabled=(not can_update or not current.get("has_override")), use_container_width=True,
    ):
        v1.clear_override(db, pid, current["source_type"], current["source_key"])
        st.success("Đã trả khoản forecast về chế độ tự động.")
        st.rerun()


def _render_settings_v2(st, db, pid: int, *, identity: Any, can_update: bool) -> None:
    import qlda.runtime_core.cashflow_forecast_v1 as v1

    s1 = v1.get_settings(db, pid)
    s2 = get_settings(db, pid)
    st.markdown("#### Giả định thanh toán chung")
    st.caption("Claim/Payment đã có dùng xác suất V1; BOQ + tiến độ tương lai dùng giả định V2 bên dưới.")
    with st.form(f"cashflow_v2_settings_{pid}"):
        terms = st.number_input(
            "Điều khoản thanh toán sau khi duyệt (ngày)", min_value=0, max_value=365,
            value=_int(s1.get("payment_terms_days"), 30), step=1,
        )
        c1, c2, c3 = st.columns(3)
        prep = c1.number_input(
            "Chuẩn bị/trình IPC (ngày)", min_value=0, max_value=90,
            value=_int(s2.get("claim_preparation_days"), 5), step=1,
        )
        approval = c2.number_input(
            "Kiểm tra/phê duyệt IPC (ngày)", min_value=0, max_value=180,
            value=_int(s2.get("approval_days"), 7), step=1,
        )
        cycle = c3.number_input(
            "Chu kỳ dự kiến lập Claim (ngày)", min_value=7, max_value=90,
            value=_int(s2.get("forecast_cycle_days"), 30), step=1,
        )
        c1, c2 = st.columns(2)
        earned = c1.number_input(
            "KL đã làm nhưng chưa vào Claim (%)", min_value=0.0, max_value=100.0,
            value=_float(s2.get("prob_earned_unclaimed"), 0.70) * 100.0, step=5.0,
        )
        future = c2.number_input(
            "BOQ + tiến độ tương lai (%)", min_value=0.0, max_value=100.0,
            value=_float(s2.get("prob_schedule_boq"), 0.50) * 100.0, step=5.0,
        )
        c1, c2, c3 = st.columns(3)
        draft = c1.number_input(
            "Claim nháp/chuẩn bị (%)", min_value=0.0, max_value=100.0,
            value=_float(s1.get("prob_draft"), 0.60) * 100.0, step=5.0,
        )
        submitted = c2.number_input(
            "Claim đã trình/đang duyệt (%)", min_value=0.0, max_value=100.0,
            value=_float(s1.get("prob_submitted"), 0.85) * 100.0, step=5.0,
        )
        approved = c3.number_input(
            "Claim đã duyệt (%)", min_value=0.0, max_value=100.0,
            value=_float(s1.get("prob_approved"), 1.0) * 100.0, step=5.0,
        )
        c1, c2 = st.columns(2)
        opt = c1.number_input(
            "Optimistic: cộng xác suất (%)", min_value=-100.0, max_value=100.0,
            value=_float(s1.get("optimistic_delta"), 0.15) * 100.0, step=5.0,
        )
        con = c2.number_input(
            "Conservative: cộng/trừ xác suất (%)", min_value=-100.0, max_value=100.0,
            value=_float(s1.get("conservative_delta"), -0.20) * 100.0, step=5.0,
        )
        save = st.form_submit_button("💾 Lưu giả định V2", disabled=not can_update, use_container_width=True)
        if save:
            v1.save_settings(
                db, pid,
                {"payment_terms_days": terms, "prob_draft": draft / 100.0,
                 "prob_submitted": submitted / 100.0, "prob_approved": approved / 100.0,
                 "optimistic_delta": opt / 100.0, "conservative_delta": con / 100.0},
                actor=identity,
            )
            save_settings(
                db, pid,
                {"claim_preparation_days": prep, "approval_days": approval,
                 "forecast_cycle_days": cycle, "prob_earned_unclaimed": earned / 100.0,
                 "prob_schedule_boq": future / 100.0},
                actor=identity,
            )
            st.success("Đã lưu giả định forecast V2.")
            st.rerun()


def render_cashflow_forecast_v2(
    st, db, workspace_project_id: int, *, identity: Any = None,
    can_update: bool = False, is_admin: bool = False,
) -> None:
    del is_admin
    import qlda.runtime_core.cashflow_forecast_v1 as v1

    pid = int(workspace_project_id)
    v1.ensure_schema(db)
    ensure_schema(db)
    with st.expander("💸 Dự trù dòng tiền V2 • Claim + BOQ + Tiến độ", expanded=True):
        st.caption(
            f"Phạm vi: {v1._scope_label(db, pid)}. V2 giữ nguyên Claim/Payment V1 và bổ sung "
            "dòng tiền tương lai từ BOQ liên kết tiến độ. Giá trị đã nằm trong Claim lũy kế được trừ trước để tránh cộng trùng."
        )
        c1, c2, c3 = st.columns([1.2, 1, 1.2])
        horizon_label = c1.selectbox(
            "Kỳ forecast", list(v1.HORIZONS.keys()), index=2, key=f"cashflow_v2_horizon_{pid}",
        )
        scenario = c2.selectbox(
            "Scenario", list(v1.SCENARIOS), index=0, key=f"cashflow_v2_scenario_{pid}",
        )
        start = date.today()
        days = v1.HORIZONS[horizon_label]
        if days > 0:
            end = start + timedelta(days=days)
            c3.date_input("Đến ngày", value=end, disabled=True, key=f"cashflow_v2_end_view_{pid}")
        else:
            end = c3.date_input(
                "Đến ngày tùy chọn", value=start + timedelta(days=365), min_value=start,
                max_value=start + timedelta(days=365), key=f"cashflow_v2_custom_end_{pid}",
            )
            if end < start:
                end = start

        rows = v1.build_forecast_rows(db, pid, scenario=scenario)
        _, diagnostics = build_schedule_boq_rows(db, pid, scenario=scenario)
        tabs = st.tabs(["Dashboard", "Chi tiết forecast", "BOQ & tiến độ", "Giả định V2"])
        with tabs[0]:
            v1._render_dashboard(st, rows, start, end, scenario)
            source_totals: dict[str, float] = {}
            for row in rows:
                fdate = row.get("forecast_date")
                if isinstance(fdate, date) and start <= fdate <= end:
                    source = _text(row.get("source_type")) or "Khác"
                    source_totals[source] = source_totals.get(source, 0.0) + max(0.0, _float(row.get("expected_amount")))
            if source_totals:
                st.markdown("#### Expected cash theo nguồn")
                st.dataframe(
                    [{"Nguồn": key, "Expected cash": value} for key, value in sorted(source_totals.items())],
                    hide_index=True, use_container_width=True,
                )
        with tabs[1]:
            _render_detail_v2(st, db, pid, rows, identity=identity, can_update=can_update)
        with tabs[2]:
            _render_schedule_diagnostics(st, diagnostics)
        with tabs[3]:
            _render_settings_v2(st, db, pid, identity=identity, can_update=can_update)


def _register_postgres_table() -> None:
    try:
        import qlda.runtime_core.project_database as pg
        order = list(getattr(pg, "TABLE_ORDER", ()))
        if SETTINGS_TABLE not in order:
            order.append(SETTINGS_TABLE)
            pg.TABLE_ORDER = tuple(order)
            pg._ID_TABLES = set(pg.TABLE_ORDER)
    except Exception:
        pass


def install_cashflow_forecast_v2() -> None:
    """Upgrade the installed V1 panel to V2 without replacing existing data."""
    global _ORIGINAL_BUILD, _ORIGINAL_RENDER
    import qlda.runtime_core.cashflow_forecast_v1 as v1

    if getattr(v1, "_qlda_cashflow_forecast_v2_installed", False):
        return
    _register_postgres_table()
    _ORIGINAL_BUILD = v1.build_forecast_rows
    _ORIGINAL_RENDER = v1.render_cashflow_forecast_v1
    v1.build_forecast_rows = _build_forecast_rows_v2
    v1.render_cashflow_forecast_v1 = render_cashflow_forecast_v2
    v1._qlda_cashflow_forecast_v2_installed = True
    v1._qlda_cashflow_forecast_v2_marker = PATCH_MARKER


__all__ = [
    "ensure_schema", "get_settings", "save_settings", "build_schedule_boq_rows",
    "render_cashflow_forecast_v2", "install_cashflow_forecast_v2",
]
