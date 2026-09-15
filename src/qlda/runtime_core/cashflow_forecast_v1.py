from __future__ import annotations

"""V1 dự trù dòng tiền thanh toán cho QLDA.

Nguồn V1 chỉ dùng dữ liệu Claim/IPC và payment tracking hiện có. Module không tự
suy diễn tiến độ/BOQ tương lai; các nguồn đó dành cho V2. Forecast được tính live,
cho phép chọn 30/60/90/120/180/270/365 ngày hoặc ngày kết thúc tùy chọn.

Nguyên tắc kế toán V1:
- Khoản phải trả = max(Approved (nếu có) / Requested / Certified - đã giải ngân, 0).
- Requested/Approved được coi là giá trị thanh toán đã phản ánh khấu trừ trong Claim;
  retention/thu hồi tạm ứng chỉ hiển thị tham chiếu, không trừ lần hai.
- Claim quá hạn chưa trả được đưa về "hôm nay" trong forecast để thể hiện nhu cầu vốn
  tức thời, nhưng vẫn giữ due_date gốc và số ngày quá hạn.
- Expected cash = Forecast outstanding x xác suất theo trạng thái/scenario.
"""

from datetime import date, datetime, timedelta
import calendar
import inspect
import re
import unicodedata
from typing import Any

PATCH_MARKER = "V7.6 CASHFLOW FORECAST V1"
SETTINGS_TABLE = "cashflow_forecast_settings"
OVERRIDES_TABLE = "cashflow_forecast_overrides"

HORIZONS = {
    "30 ngày": 30,
    "60 ngày": 60,
    "90 ngày": 90,
    "120 ngày": 120,
    "180 ngày": 180,
    "270 ngày": 270,
    "365 ngày": 365,
    "Tùy chọn": 0,
}
SCENARIOS = ("Base", "Optimistic", "Conservative")

DEFAULT_SETTINGS = {
    "payment_terms_days": 30,
    "prob_draft": 0.60,
    "prob_submitted": 0.85,
    "prob_approved": 1.00,
    "optimistic_delta": 0.15,
    "conservative_delta": -0.20,
}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


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
    for fmt in (
        "%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d",
    ):
        try:
            return datetime.strptime(text[:19] if "%H" in fmt else text[:10], fmt).date()
        except Exception:
            pass
    return None


def _date_text(value: date | None) -> str:
    return value.strftime("%d/%m/%Y") if isinstance(value, date) else ""


def _money(value: Any) -> str:
    number = _float(value)
    sign = "-" if number < 0 else ""
    number = abs(number)
    if number >= 1_000_000_000:
        return f"{sign}{number / 1_000_000_000:,.2f} tỷ"
    if number >= 1_000_000:
        return f"{sign}{number / 1_000_000:,.1f} triệu"
    return f"{sign}{number:,.0f} đ"


def _actor_name(identity: Any) -> str:
    row = _rowdict(identity)
    return _text(row.get("name") or row.get("email") or "Người dùng")


def _column_names(connection, table: str) -> set[str]:
    try:
        cur = connection.execute(f"SELECT * FROM {table} LIMIT 0")
        desc = getattr(cur, "description", None) or []
        names: set[str] = set()
        for item in desc:
            name = getattr(item, "name", None)
            if name is None:
                try:
                    name = item[0]
                except Exception:
                    name = None
            if name:
                names.add(str(name))
        return names
    except Exception:
        return set()


def _table_exists(connection, table: str) -> bool:
    try:
        connection.execute(f"SELECT 1 FROM {table} LIMIT 1")
        return True
    except Exception:
        return False


def _resolve_scope(db, workspace_project_id: int) -> dict[str, Any]:
    pid = int(workspace_project_id)
    info = {
        "master_project_id": pid,
        "workspace_project_id": pid,
        "contractor_code": "",
        "contractor_name": "",
    }
    try:
        from qlda.runtime_core import contractor_workspace as cw
        with db.connect() as connection:
            cw.ensure_schema_connection(connection)
            row = connection.execute(
                f"SELECT master_project_id,workspace_project_id,contractor_code,contractor_name "
                f"FROM {cw.TABLE_NAME} WHERE workspace_project_id=? LIMIT 1",
                (pid,),
            ).fetchone()
        if row:
            info.update(_rowdict(row))
    except Exception:
        pass
    info["master_project_id"] = _int(info.get("master_project_id"), pid) or pid
    info["workspace_project_id"] = _int(info.get("workspace_project_id"), pid) or pid
    return info


def ensure_schema(db) -> None:
    with db.connect() as connection:
        connection.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {SETTINGS_TABLE}(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                master_project_id INTEGER NOT NULL,
                workspace_project_id INTEGER NOT NULL UNIQUE,
                payment_terms_days INTEGER NOT NULL DEFAULT 30,
                prob_draft REAL NOT NULL DEFAULT 0.60,
                prob_submitted REAL NOT NULL DEFAULT 0.85,
                prob_approved REAL NOT NULL DEFAULT 1.00,
                optimistic_delta REAL NOT NULL DEFAULT 0.15,
                conservative_delta REAL NOT NULL DEFAULT -0.20,
                updated_by TEXT DEFAULT '',
                created_at TEXT DEFAULT '',
                updated_at TEXT DEFAULT ''
            )
            """
        )
        connection.execute(
            f"CREATE INDEX IF NOT EXISTS idx_cashflow_settings_master "
            f"ON {SETTINGS_TABLE}(master_project_id,workspace_project_id)"
        )
        connection.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {OVERRIDES_TABLE}(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                master_project_id INTEGER NOT NULL,
                workspace_project_id INTEGER NOT NULL,
                source_type TEXT NOT NULL,
                source_key TEXT NOT NULL,
                forecast_date TEXT DEFAULT '',
                planned_amount REAL NOT NULL DEFAULT 0,
                probability REAL NOT NULL DEFAULT -1,
                note TEXT DEFAULT '',
                updated_by TEXT DEFAULT '',
                created_at TEXT DEFAULT '',
                updated_at TEXT DEFAULT '',
                UNIQUE(workspace_project_id,source_type,source_key)
            )
            """
        )
        connection.execute(
            f"CREATE INDEX IF NOT EXISTS idx_cashflow_override_workspace "
            f"ON {OVERRIDES_TABLE}(workspace_project_id,source_type,source_key)"
        )


def get_settings(db, workspace_project_id: int) -> dict[str, Any]:
    ensure_schema(db)
    pid = int(workspace_project_id)
    scope = _resolve_scope(db, pid)
    with db.connect() as connection:
        row = connection.execute(
            f"SELECT * FROM {SETTINGS_TABLE} WHERE workspace_project_id=? LIMIT 1", (pid,)
        ).fetchone()
        if row:
            out = _rowdict(row)
        else:
            stamp = _now()
            connection.execute(
                f"""INSERT INTO {SETTINGS_TABLE}(
                       master_project_id,workspace_project_id,payment_terms_days,prob_draft,prob_submitted,
                       prob_approved,optimistic_delta,conservative_delta,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    int(scope["master_project_id"]), pid,
                    int(DEFAULT_SETTINGS["payment_terms_days"]),
                    float(DEFAULT_SETTINGS["prob_draft"]),
                    float(DEFAULT_SETTINGS["prob_submitted"]),
                    float(DEFAULT_SETTINGS["prob_approved"]),
                    float(DEFAULT_SETTINGS["optimistic_delta"]),
                    float(DEFAULT_SETTINGS["conservative_delta"]),
                    stamp, stamp,
                ),
            )
            row = connection.execute(
                f"SELECT * FROM {SETTINGS_TABLE} WHERE workspace_project_id=? LIMIT 1", (pid,)
            ).fetchone()
            out = _rowdict(row)
    for key, value in DEFAULT_SETTINGS.items():
        out.setdefault(key, value)
    return out


def save_settings(db, workspace_project_id: int, values: dict[str, Any], *, actor: Any = None) -> None:
    current = get_settings(db, int(workspace_project_id))
    terms = max(0, min(365, _int(values.get("payment_terms_days"), _int(current.get("payment_terms_days"), 30))))
    draft = max(0.0, min(1.0, _float(values.get("prob_draft"), _float(current.get("prob_draft"), 0.60))))
    submitted = max(0.0, min(1.0, _float(values.get("prob_submitted"), _float(current.get("prob_submitted"), 0.85))))
    approved = max(0.0, min(1.0, _float(values.get("prob_approved"), _float(current.get("prob_approved"), 1.0))))
    optimistic = max(-1.0, min(1.0, _float(values.get("optimistic_delta"), _float(current.get("optimistic_delta"), 0.15))))
    conservative = max(-1.0, min(1.0, _float(values.get("conservative_delta"), _float(current.get("conservative_delta"), -0.20))))
    with db.connect() as connection:
        connection.execute(
            f"""UPDATE {SETTINGS_TABLE} SET payment_terms_days=?,prob_draft=?,prob_submitted=?,prob_approved=?,
                   optimistic_delta=?,conservative_delta=?,updated_by=?,updated_at=? WHERE workspace_project_id=?""",
            (
                terms, draft, submitted, approved, optimistic, conservative,
                _actor_name(actor), _now(), int(workspace_project_id),
            ),
        )


def _load_overrides(db, workspace_project_id: int) -> dict[tuple[str, str], dict[str, Any]]:
    ensure_schema(db)
    with db.connect() as connection:
        rows = connection.execute(
            f"SELECT * FROM {OVERRIDES_TABLE} WHERE workspace_project_id=? ORDER BY id",
            (int(workspace_project_id),),
        ).fetchall()
    return {
        (_text(_rowdict(row).get("source_type")), _text(_rowdict(row).get("source_key"))): _rowdict(row)
        for row in rows
    }


def save_override(
    db,
    workspace_project_id: int,
    source_type: str,
    source_key: str,
    *,
    forecast_date: date | None,
    planned_amount: float,
    probability: float,
    note: str = "",
    actor: Any = None,
) -> None:
    pid = int(workspace_project_id)
    scope = _resolve_scope(db, pid)
    ensure_schema(db)
    stype, skey = _text(source_type), _text(source_key)
    if not stype or not skey:
        raise ValueError("Không xác định được nguồn forecast.")
    stamp = _now()
    fd = forecast_date.isoformat() if isinstance(forecast_date, date) else ""
    planned = max(0.0, _float(planned_amount))
    prob = _float(probability, -1.0)
    if prob >= 0:
        prob = max(0.0, min(1.0, prob))
    who = _actor_name(actor)
    with db.connect() as connection:
        row = connection.execute(
            f"SELECT id FROM {OVERRIDES_TABLE} WHERE workspace_project_id=? AND source_type=? AND source_key=? LIMIT 1",
            (pid, stype, skey),
        ).fetchone()
        if row:
            connection.execute(
                f"""UPDATE {OVERRIDES_TABLE} SET forecast_date=?,planned_amount=?,probability=?,note=?,
                       updated_by=?,updated_at=? WHERE workspace_project_id=? AND source_type=? AND source_key=?""",
                (fd, planned, prob, _text(note), who, stamp, pid, stype, skey),
            )
        else:
            connection.execute(
                f"""INSERT INTO {OVERRIDES_TABLE}(
                       master_project_id,workspace_project_id,source_type,source_key,forecast_date,planned_amount,
                       probability,note,updated_by,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    int(scope["master_project_id"]), pid, stype, skey, fd, planned, prob,
                    _text(note), who, stamp, stamp,
                ),
            )


def clear_override(db, workspace_project_id: int, source_type: str, source_key: str) -> None:
    ensure_schema(db)
    with db.connect() as connection:
        connection.execute(
            f"DELETE FROM {OVERRIDES_TABLE} WHERE workspace_project_id=? AND source_type=? AND source_key=?",
            (int(workspace_project_id), _text(source_type), _text(source_key)),
        )


def _probability_for_status(status: str, settings: dict[str, Any], planned_pct: float = 0.0) -> tuple[float, str]:
    if planned_pct > 0:
        value = planned_pct / 100.0 if planned_pct > 1.0 else planned_pct
        return max(0.0, min(1.0, value)), "Kế hoạch giải ngân"

    q = _norm(status)
    if any(x in q for x in ("da giai ngan", "da thanh toan", "paid", "disbursed", "hoan tat")):
        return 1.0, "Đã thanh toán"
    if any(x in q for x in ("da duyet", "duoc duyet", "chap thuan", "approved", "certified")):
        return max(0.0, min(1.0, _float(settings.get("prob_approved"), 1.0))), "Đã duyệt"
    if any(x in q for x in (
        "da trinh", "cho duyet", "dang duyet", "dang kiem tra", "submitted", "review", "cho thanh toan",
    )):
        return max(0.0, min(1.0, _float(settings.get("prob_submitted"), 0.85))), "Đã trình/đang duyệt"
    if any(x in q for x in ("nhap", "chuan bi", "chua trinh", "draft")):
        return max(0.0, min(1.0, _float(settings.get("prob_draft"), 0.60))), "Nháp/chuẩn bị"
    return max(0.0, min(1.0, _float(settings.get("prob_submitted"), 0.85))), "Mặc định V1"


def _scenario_probability(base: float, scenario: str, settings: dict[str, Any]) -> float:
    delta = 0.0
    if scenario == "Optimistic":
        delta = _float(settings.get("optimistic_delta"), 0.15)
    elif scenario == "Conservative":
        delta = _float(settings.get("conservative_delta"), -0.20)
    return max(0.0, min(1.0, base + delta))


def _derive_due_date(row: dict[str, Any], terms_days: int, override: dict[str, Any]) -> tuple[date, str, date]:
    today = date.today()
    manual = _parse_date(override.get("forecast_date"))
    if manual:
        due = manual
        return max(today, due), "Điều chỉnh thủ công", due

    for key, label in (
        ("payment_due_date", "Hạn thanh toán Claim"),
        ("forecast_payment_date", "Ngày forecast nguồn"),
    ):
        value = _parse_date(row.get(key))
        if value:
            return max(today, value), label, value

    paid_date = _parse_date(row.get("disbursement_date") or row.get("payment_date"))
    if paid_date and _float(row.get("outstanding")) > 0 and paid_date >= today:
        return paid_date, "Ngày giải ngân dự kiến", paid_date

    period_end = _parse_date(row.get("to_date"))
    if period_end:
        due = period_end + timedelta(days=max(0, terms_days))
        return max(today, due), f"Kết thúc kỳ + {terms_days} ngày", due

    updated = _parse_date(row.get("updated_at")) or _parse_date(row.get("created_at"))
    if updated:
        due = updated + timedelta(days=max(0, terms_days))
        return max(today, due), f"Ngày cập nhật + {terms_days} ngày", due

    due = today + timedelta(days=max(0, terms_days))
    return due, f"Hôm nay + {terms_days} ngày", due


def _payment_rows_by_code(connection, project_id: int) -> dict[str, dict[str, Any]]:
    if not _table_exists(connection, "payment_tracking"):
        return {}
    try:
        rows = connection.execute(
            "SELECT * FROM payment_tracking WHERE project_id=? ORDER BY id", (int(project_id),)
        ).fetchall()
    except Exception:
        try:
            rows = connection.execute(
                "SELECT * FROM payment_tracking WHERE project_id=?", (int(project_id),)
            ).fetchall()
        except Exception:
            rows = []
    out: dict[str, dict[str, Any]] = {}
    for raw in rows:
        row = _rowdict(raw)
        code = _text(row.get("payment_code"))
        if code:
            out[code] = row
    return out


def build_forecast_rows(db, workspace_project_id: int, *, scenario: str = "Base") -> list[dict[str, Any]]:
    pid = int(workspace_project_id)
    settings = get_settings(db, pid)
    overrides = _load_overrides(db, pid)
    scope = _resolve_scope(db, pid)
    contractor = " - ".join(x for x in (
        _text(scope.get("contractor_code")), _text(scope.get("contractor_name")),
    ) if x) or "Workspace mặc định"
    today = date.today()
    terms = max(0, _int(settings.get("payment_terms_days"), 30))

    rows: list[dict[str, Any]] = []
    seen_payment_codes: set[str] = set()
    with db.connect() as connection:
        payments = _payment_rows_by_code(connection, pid)
        claims: list[dict[str, Any]] = []
        if _table_exists(connection, "payment_claims"):
            try:
                claims = [_rowdict(r) for r in connection.execute(
                    "SELECT * FROM payment_claims WHERE project_id=? ORDER BY claim_no,updated_at",
                    (pid,),
                ).fetchall()]
            except Exception:
                claims = []

        for claim in claims:
            claim_code = _text(claim.get("claim_code") or claim.get("claim_no"))
            payment = payments.get(claim_code, {})
            if claim_code:
                seen_payment_codes.add(claim_code)
            source_key = _text(claim.get("claim_id")) or claim_code
            override = overrides.get(("CLAIM", source_key), {})

            requested = max(0.0, _float(claim.get("requested_amount")))
            approved = max(0.0, _float(claim.get("approved_amount")))
            certified = max(0.0, _float(claim.get("certified_cumulative")))
            paid = max(
                0.0,
                _float(claim.get("disbursed_amount")),
                _float(payment.get("paid_amount")),
            )
            target = approved if approved > 0 else requested if requested > 0 else certified
            outstanding = max(0.0, target - paid)
            status = _text(claim.get("payment_status") or payment.get("payment_status") or "Nháp")
            planned_pct = _float(payment.get("planned_disbursement_pct"))
            auto_prob, prob_reason = _probability_for_status(status, settings, planned_pct)
            override_prob = _float(override.get("probability"), -1.0)
            base_prob = override_prob if 0.0 <= override_prob <= 1.0 else auto_prob
            probability = _scenario_probability(base_prob, scenario, settings)

            date_row = dict(claim)
            date_row.update({
                "payment_date": payment.get("payment_date"),
                "outstanding": outstanding,
            })
            effective_date, due_source, due_date = _derive_due_date(date_row, terms, override)
            actual_date = _parse_date(claim.get("disbursement_date") or payment.get("payment_date"))
            planned_amount = _float(override.get("planned_amount"))
            if planned_amount <= 0:
                planned_amount = outstanding
            overdue_days = max(0, (today - due_date).days) if outstanding > 0 and due_date < today else 0

            rows.append({
                "source_type": "CLAIM",
                "source_key": source_key,
                "claim_code": claim_code,
                "contractor": _text(claim.get("contractor")) or contractor,
                "contract_no": _text(claim.get("contract_no")),
                "package_name": _text(claim.get("package_name")),
                "status": status,
                "requested_amount": requested,
                "approved_amount": approved,
                "certified_amount": certified,
                "disbursed_amount": paid,
                "target_amount": target,
                "outstanding": outstanding,
                "planned_amount": planned_amount,
                "base_probability": base_prob,
                "probability": probability,
                "probability_reason": "Điều chỉnh thủ công" if 0 <= override_prob <= 1 else prob_reason,
                "forecast_date": effective_date,
                "due_date": due_date,
                "due_source": due_source,
                "overdue_days": overdue_days,
                "expected_amount": outstanding * probability,
                "retention_reference": max(0.0, _float(claim.get("retention_cumulative"))),
                "advance_recovery_reference": max(0.0, _float(claim.get("advance_recovery"))),
                "deductions_reference": max(0.0, _float(claim.get("current_deductions"))),
                "actual_date": actual_date,
                "note": _text(override.get("note") or claim.get("note")),
                "has_override": bool(override),
            })

        # Keep manually-entered payment rows that are not backed by an IPC Claim.
        for code, payment in payments.items():
            if code in seen_payment_codes:
                continue
            source_key = code or _text(payment.get("id"))
            if not source_key:
                continue
            override = overrides.get(("PAYMENT", source_key), {})
            certified = max(0.0, _float(payment.get("certified_cumulative")))
            paid = max(0.0, _float(payment.get("paid_amount")))
            target = certified
            outstanding = max(0.0, target - paid)
            status = _text(payment.get("payment_status") or "Nháp")
            auto_prob, prob_reason = _probability_for_status(
                status, settings, _float(payment.get("planned_disbursement_pct"))
            )
            override_prob = _float(override.get("probability"), -1.0)
            base_prob = override_prob if 0 <= override_prob <= 1 else auto_prob
            probability = _scenario_probability(base_prob, scenario, settings)
            date_row = dict(payment)
            date_row["outstanding"] = outstanding
            effective_date, due_source, due_date = _derive_due_date(date_row, terms, override)
            actual_date = _parse_date(payment.get("payment_date"))
            planned_amount = _float(override.get("planned_amount")) or outstanding
            overdue_days = max(0, (today - due_date).days) if outstanding > 0 and due_date < today else 0
            rows.append({
                "source_type": "PAYMENT",
                "source_key": source_key,
                "claim_code": code,
                "contractor": contractor,
                "contract_no": "",
                "package_name": "",
                "status": status,
                "requested_amount": 0.0,
                "approved_amount": 0.0,
                "certified_amount": certified,
                "disbursed_amount": paid,
                "target_amount": target,
                "outstanding": outstanding,
                "planned_amount": planned_amount,
                "base_probability": base_prob,
                "probability": probability,
                "probability_reason": "Điều chỉnh thủ công" if 0 <= override_prob <= 1 else prob_reason,
                "forecast_date": effective_date,
                "due_date": due_date,
                "due_source": due_source,
                "overdue_days": overdue_days,
                "expected_amount": outstanding * probability,
                "retention_reference": 0.0,
                "advance_recovery_reference": max(0.0, _float(payment.get("advance_recovery"))),
                "deductions_reference": 0.0,
                "actual_date": actual_date,
                "note": _text(override.get("note") or payment.get("note")),
                "has_override": bool(override),
            })

    rows.sort(key=lambda r: (r.get("forecast_date") or today, r.get("claim_code") or ""))
    return rows


def _month_key(value: date) -> str:
    return value.strftime("%Y-%m")


def _month_label(key: str) -> str:
    try:
        dt = datetime.strptime(key, "%Y-%m")
        return dt.strftime("%m/%Y")
    except Exception:
        return key


def _month_range(start: date, end: date) -> list[str]:
    out: list[str] = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        out.append(f"{year:04d}-{month:02d}")
        month += 1
        if month > 12:
            month = 1
            year += 1
    return out


def aggregate_monthly(rows: list[dict[str, Any]], start: date, end: date) -> list[dict[str, Any]]:
    months = _month_range(start, end)
    buckets = {key: {"plan": 0.0, "forecast": 0.0, "expected": 0.0, "actual": 0.0} for key in months}
    start_month = date(start.year, start.month, 1)
    for row in rows:
        fdate = row.get("forecast_date")
        if isinstance(fdate, date) and start <= fdate <= end:
            key = _month_key(fdate)
            if key in buckets:
                buckets[key]["plan"] += max(0.0, _float(row.get("planned_amount")))
                buckets[key]["forecast"] += max(0.0, _float(row.get("outstanding")))
                buckets[key]["expected"] += max(0.0, _float(row.get("expected_amount")))
        adate = row.get("actual_date")
        if isinstance(adate, date) and start_month <= adate <= end:
            key = _month_key(adate)
            if key in buckets:
                buckets[key]["actual"] += max(0.0, _float(row.get("disbursed_amount")))
    return [{"month": key, **buckets[key]} for key in months]


def _sum_horizon(rows: list[dict[str, Any]], days: int) -> float:
    today = date.today()
    end = today + timedelta(days=max(0, days))
    return sum(
        max(0.0, _float(row.get("expected_amount")))
        for row in rows
        if isinstance(row.get("forecast_date"), date) and today <= row["forecast_date"] <= end
    )


def _is_approved_status(status: str) -> bool:
    q = _norm(status)
    return any(x in q for x in ("da duyet", "duoc duyet", "chap thuan", "approved", "certified"))


def _scope_label(db, pid: int) -> str:
    scope = _resolve_scope(db, pid)
    return " - ".join(x for x in (
        _text(scope.get("contractor_code")), _text(scope.get("contractor_name")),
    ) if x) or "Workspace mặc định"


def _render_dashboard(st, rows: list[dict[str, Any]], start: date, end: date, scenario: str) -> None:
    selected = [
        row for row in rows
        if isinstance(row.get("forecast_date"), date) and start <= row["forecast_date"] <= end
    ]
    forecast_total = sum(_float(r.get("outstanding")) for r in selected)
    expected_total = sum(_float(r.get("expected_amount")) for r in selected)
    plan_total = sum(_float(r.get("planned_amount")) for r in selected)
    overdue_total = sum(_float(r.get("outstanding")) for r in rows if _int(r.get("overdue_days")) > 0)
    approved_pending = sum(
        _float(r.get("outstanding")) for r in rows
        if _float(r.get("outstanding")) > 0 and _is_approved_status(_text(r.get("status")))
    )

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("30 ngày", _money(_sum_horizon(rows, 30)))
    c2.metric("60 ngày", _money(_sum_horizon(rows, 60)))
    c3.metric("90 ngày", _money(_sum_horizon(rows, 90)))
    c4.metric("6 tháng", _money(_sum_horizon(rows, 180)))
    c5.metric("12 tháng", _money(_sum_horizon(rows, 365)))

    c1, c2, c3, c4 = st.columns(4)
    c1.metric(f"Forecast • {scenario}", _money(forecast_total))
    c2.metric("Expected cash", _money(expected_total))
    c3.metric("Quá hạn chưa trả", _money(overdue_total))
    c4.metric("Đã duyệt chờ trả", _money(approved_pending))

    if not rows:
        st.info("Chưa có IPC/Claim hoặc dữ liệu thanh toán để lập forecast V1.")
        return

    monthly = aggregate_monthly(rows, start, end)
    if monthly:
        import pandas as pd
        chart = pd.DataFrame([
            {
                "Tháng": _month_label(r["month"]),
                "Plan": r["plan"],
                "Forecast": r["forecast"],
                "Expected": r["expected"],
                "Actual": r["actual"],
            }
            for r in monthly
        ])
        st.markdown("#### Plan vs Forecast vs Expected vs Actual")
        st.bar_chart(chart.set_index("Tháng"), use_container_width=True)
        view = chart.copy()
        for col in ("Plan", "Forecast", "Expected", "Actual"):
            view[col] = view[col].map(lambda x: f"{float(x):,.0f}")
        st.dataframe(view, hide_index=True, use_container_width=True)

    alerts: list[str] = []
    for row in rows:
        if _int(row.get("overdue_days")) > 0 and _float(row.get("outstanding")) > 0:
            alerts.append(
                f"{row.get('claim_code') or row.get('source_key')}: quá hạn {_int(row.get('overdue_days'))} ngày, "
                f"còn {_money(row.get('outstanding'))}."
            )
    if alerts:
        st.warning("⚠️ Khoản cần ưu tiên dòng tiền\n\n" + "\n\n".join(alerts[:10]))


def _render_detail(st, db, pid: int, rows: list[dict[str, Any]], *, identity: Any, can_update: bool) -> None:
    import pandas as pd

    if not rows:
        st.info("Chưa có dữ liệu chi tiết Claim/Payment.")
        return

    table = pd.DataFrame([
        {
            "Nguồn": r["source_type"],
            "Claim/Payment": r["claim_code"],
            "Nhà thầu": r["contractor"],
            "Trạng thái": r["status"],
            "Ngày đến hạn": _date_text(r["due_date"]),
            "Ngày forecast": _date_text(r["forecast_date"]),
            "Quá hạn (ngày)": r["overdue_days"],
            "Đề nghị": r["requested_amount"],
            "Được duyệt": r["approved_amount"],
            "Đã giải ngân": r["disbursed_amount"],
            "Còn phải trả": r["outstanding"],
            "Xác suất": f"{r['probability'] * 100:.0f}%",
            "Expected": r["expected_amount"],
            "Nguồn ngày": r["due_source"],
            "Ghi chú": r["note"],
        }
        for r in rows
    ])
    st.dataframe(table, hide_index=True, use_container_width=True)
    st.download_button(
        "⬇️ Xuất CSV forecast V1",
        data=table.to_csv(index=False).encode("utf-8-sig"),
        file_name=f"cashflow_forecast_v1_{pid}_{date.today():%Y%m%d}.csv",
        mime="text/csv",
        use_container_width=True,
    )

    st.markdown("#### Điều chỉnh một khoản forecast")
    labels = {
        f"{r['source_type']}|{r['source_key']}": (
            f"{r['claim_code'] or r['source_key']} • {r['contractor']} • còn {_money(r['outstanding'])}"
        )
        for r in rows
    }
    keys = list(labels)
    selected_key = st.selectbox(
        "Claim/Payment cần điều chỉnh",
        keys,
        format_func=lambda key: labels.get(key, key),
        key=f"cashflow_v1_edit_select_{pid}",
    )
    current = next(r for r in rows if f"{r['source_type']}|{r['source_key']}" == selected_key)

    auto_date = not bool(current.get("has_override")) or _text(current.get("due_source")) != "Điều chỉnh thủ công"
    with st.form(f"cashflow_v1_override_form_{pid}_{current['source_type']}_{current['source_key']}"):
        use_auto_date = st.checkbox("Dùng ngày forecast tự động", value=auto_date)
        forecast_date_value = st.date_input(
            "Ngày forecast thanh toán",
            value=current["forecast_date"],
            disabled=use_auto_date,
        )
        use_source_amount = st.checkbox(
            "Dùng số tiền từ Claim/Payment",
            value=abs(_float(current.get("planned_amount")) - _float(current.get("outstanding"))) < 1e-6,
        )
        planned_amount = st.number_input(
            "Plan amount (VND)",
            min_value=0.0,
            value=float(current.get("planned_amount") or 0),
            step=1_000_000.0,
            disabled=use_source_amount,
        )
        auto_prob = st.checkbox(
            "Dùng xác suất tự động theo trạng thái",
            value=_text(current.get("probability_reason")) != "Điều chỉnh thủ công",
        )
        prob_pct = st.number_input(
            "Xác suất Base (%)",
            min_value=0.0,
            max_value=100.0,
            value=float(current.get("base_probability") or 0) * 100.0,
            step=5.0,
            disabled=auto_prob,
        )
        note = st.text_area("Ghi chú forecast", value=_text(current.get("note")))
        save = st.form_submit_button("💾 Lưu điều chỉnh", disabled=not can_update, use_container_width=True)
        if save:
            save_override(
                db,
                pid,
                current["source_type"],
                current["source_key"],
                forecast_date=None if use_auto_date else forecast_date_value,
                planned_amount=0.0 if use_source_amount else planned_amount,
                probability=-1.0 if auto_prob else prob_pct / 100.0,
                note=note,
                actor=identity,
            )
            st.success("Đã lưu điều chỉnh forecast.")
            st.rerun()

    if st.button(
        "↩️ Xóa điều chỉnh, dùng lại dữ liệu tự động",
        key=f"cashflow_v1_clear_{pid}_{current['source_type']}_{current['source_key']}",
        disabled=(not can_update or not current.get("has_override")),
        use_container_width=True,
    ):
        clear_override(db, pid, current["source_type"], current["source_key"])
        st.success("Đã trả khoản forecast về chế độ tự động.")
        st.rerun()


def _render_settings(st, db, pid: int, *, identity: Any, can_update: bool) -> None:
    settings = get_settings(db, pid)
    st.caption(
        "V1 chỉ forecast từ Claim/IPC và payment tracking. BOQ + tiến độ để sinh Claim tương lai sẽ triển khai ở V2."
    )
    with st.form(f"cashflow_v1_settings_{pid}"):
        terms = st.number_input(
            "Điều khoản thanh toán mặc định (ngày sau cuối kỳ Claim)",
            min_value=0,
            max_value=365,
            value=_int(settings.get("payment_terms_days"), 30),
            step=1,
        )
        c1, c2, c3 = st.columns(3)
        draft = c1.number_input(
            "Nháp/chuẩn bị (%)", min_value=0.0, max_value=100.0,
            value=_float(settings.get("prob_draft"), 0.60) * 100.0, step=5.0,
        )
        submitted = c2.number_input(
            "Đã trình/đang duyệt (%)", min_value=0.0, max_value=100.0,
            value=_float(settings.get("prob_submitted"), 0.85) * 100.0, step=5.0,
        )
        approved = c3.number_input(
            "Đã duyệt (%)", min_value=0.0, max_value=100.0,
            value=_float(settings.get("prob_approved"), 1.0) * 100.0, step=5.0,
        )
        c1, c2 = st.columns(2)
        opt = c1.number_input(
            "Optimistic: cộng xác suất (%)", min_value=-100.0, max_value=100.0,
            value=_float(settings.get("optimistic_delta"), 0.15) * 100.0, step=5.0,
        )
        con = c2.number_input(
            "Conservative: cộng/trừ xác suất (%)", min_value=-100.0, max_value=100.0,
            value=_float(settings.get("conservative_delta"), -0.20) * 100.0, step=5.0,
        )
        save = st.form_submit_button("💾 Lưu giả định V1", disabled=not can_update, use_container_width=True)
        if save:
            save_settings(
                db,
                pid,
                {
                    "payment_terms_days": terms,
                    "prob_draft": draft / 100.0,
                    "prob_submitted": submitted / 100.0,
                    "prob_approved": approved / 100.0,
                    "optimistic_delta": opt / 100.0,
                    "conservative_delta": con / 100.0,
                },
                actor=identity,
            )
            st.success("Đã lưu giả định forecast V1.")
            st.rerun()


def render_cashflow_forecast_v1(
    st,
    db,
    workspace_project_id: int,
    *,
    identity: Any = None,
    can_update: bool = False,
    is_admin: bool = False,
) -> None:
    del is_admin
    pid = int(workspace_project_id)
    ensure_schema(db)

    with st.expander("💸 Dự trù dòng tiền V1 • Claim / Payment", expanded=True):
        st.caption(
            f"Phạm vi: {_scope_label(db, pid)}. Forecast V1 đọc live IPC/Claim và thanh toán; "
            "không ghi đè dữ liệu Claim gốc."
        )
        c1, c2, c3 = st.columns([1.2, 1, 1.2])
        horizon_label = c1.selectbox(
            "Kỳ forecast",
            list(HORIZONS.keys()),
            index=2,
            key=f"cashflow_v1_horizon_{pid}",
        )
        scenario = c2.selectbox(
            "Scenario",
            list(SCENARIOS),
            index=0,
            key=f"cashflow_v1_scenario_{pid}",
        )
        start = date.today()
        days = HORIZONS[horizon_label]
        if days > 0:
            end = start + timedelta(days=days)
            c3.date_input("Đến ngày", value=end, disabled=True, key=f"cashflow_v1_end_view_{pid}")
        else:
            end = c3.date_input(
                "Đến ngày tùy chọn",
                value=start + timedelta(days=365),
                min_value=start,
                key=f"cashflow_v1_custom_end_{pid}",
            )
            if end < start:
                end = start

        rows = build_forecast_rows(db, pid, scenario=scenario)
        tabs = st.tabs(["Dashboard", "Chi tiết Claim/Payment", "Giả định V1"])
        with tabs[0]:
            _render_dashboard(st, rows, start, end, scenario)
        with tabs[1]:
            _render_detail(st, db, pid, rows, identity=identity, can_update=can_update)
        with tabs[2]:
            _render_settings(st, db, pid, identity=identity, can_update=can_update)


def _register_postgres_tables() -> None:
    try:
        import qlda.runtime_core.project_database as pg
        order = list(getattr(pg, "TABLE_ORDER", ()))
        changed = False
        for table in (SETTINGS_TABLE, OVERRIDES_TABLE):
            if table not in order:
                order.append(table)
                changed = True
        if changed:
            pg.TABLE_ORDER = tuple(order)
            pg._ID_TABLES = set(pg.TABLE_ORDER)
    except Exception:
        pass


def install_cashflow_forecast_v1() -> None:
    """Inject V1 cashflow panel at the top of the existing Quản lý chi phí screen."""
    import streamlit as st

    if getattr(st, "_qlda_cashflow_forecast_v1_installed", False):
        return
    _register_postgres_tables()

    original_subheader = st.subheader

    def subheader_with_cashflow(body, *args, **kwargs):
        result = original_subheader(body, *args, **kwargs)
        if _text(body) != "💰 Quản lý chi phí":
            return result

        frame = inspect.currentframe()
        caller = frame.f_back if frame else None
        try:
            if caller is None or caller.f_code.co_name != "render_cost_management":
                return result
            glb, loc = caller.f_globals, caller.f_locals
            db = glb.get("db")
            pid = _int(loc.get("pid"))
            if db is None or pid <= 0:
                return result
            can_update_fn = glb.get("_can_update")
            is_admin_fn = glb.get("_is_admin")
            identity_fn = glb.get("_cloud_identity")
            render_cashflow_forecast_v1(
                st,
                db,
                pid,
                identity=identity_fn() if callable(identity_fn) else None,
                can_update=bool(can_update_fn()) if callable(can_update_fn) else False,
                is_admin=bool(is_admin_fn()) if callable(is_admin_fn) else False,
            )
        except Exception as exc:
            st.warning(f"Dự trù dòng tiền V1 chưa tải được ở lượt này: {exc}")
        finally:
            del caller
            del frame
        return result

    st.subheader = subheader_with_cashflow
    st._qlda_cashflow_forecast_v1_installed = True
    st._qlda_cashflow_forecast_v1_marker = PATCH_MARKER


__all__ = [
    "ensure_schema",
    "get_settings",
    "save_settings",
    "save_override",
    "clear_override",
    "build_forecast_rows",
    "aggregate_monthly",
    "render_cashflow_forecast_v1",
    "install_cashflow_forecast_v1",
]
