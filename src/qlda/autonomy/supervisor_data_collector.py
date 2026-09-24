from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, timedelta
from typing import Any


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


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in (
        "%Y-%m-%d",
        "%Y-%m-%d %H:%M:%S",
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%d/%m/%Y %H:%M:%S",
    ):
        try:
            return datetime.strptime(text[:19] if "%H" in fmt else text[:10], fmt).date()
        except Exception:
            pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except Exception:
        return None


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y",
    ):
        try:
            return datetime.strptime(text[:19] if "%H" in fmt else text[:10], fmt)
        except Exception:
            pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=None)
    except Exception:
        return None


def _table_exists(connection, table: str) -> bool:
    try:
        connection.execute(f"SELECT 1 FROM {table} LIMIT 1")
        return True
    except Exception:
        return False


def _is_closed(status: Any) -> bool:
    value = _norm(status)
    if not value:
        return False
    closed_terms = (
        "dong",
        "da dong",
        "hoan thanh",
        "da hoan thanh",
        "closed",
        "resolved",
        "huy",
        "cancelled",
        "canceled",
        "da xac nhan",
        "approved",
    )
    return any(term == value or term in value for term in closed_terms)


def _is_rejected(status: Any) -> bool:
    value = _norm(status)
    rejected_terms = (
        "tu choi",
        "khong dat",
        "rejected",
        "failed",
        "yeu cau chinh sua",
        "chua dat",
    )
    return any(term in value for term in rejected_terms)


def _doc_ref(row: dict[str, Any]) -> str:
    for key in ("code", "doc_code", "record_code", "subject", "id"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    return ""


def _collect_document_signals(connection, workspace_id: int, today: date) -> tuple[dict[str, Any], dict[str, Any]]:
    indicators = {
        "ncr_overdue": 0,
        "rfi_overdue": 0,
        "inspection_rejected": 0,
    }
    evidence: dict[str, list[dict[str, Any]]] = {
        "ncr_overdue": [],
        "rfi_overdue": [],
        "inspection_rejected": [],
    }
    if not _table_exists(connection, "documents"):
        return indicators, evidence

    try:
        rows = connection.execute(
            "SELECT * FROM documents WHERE project_id=?",
            (int(workspace_id),),
        ).fetchall()
    except Exception:
        return indicators, evidence

    acceptance_types = {
        "bbht",
        "bbnt",
        "ins",
        "inspection",
        "nghiem thu",
        "nghiemthu",
        "ntcv",
        "ntvl",
    }

    for raw in rows:
        row = _rowdict(raw)
        doc_type = _norm(row.get("doc_type"))
        status = row.get("status")
        due = _parse_date(row.get("due_date"))
        closed = _is_closed(status)
        ref = _doc_ref(row)

        if due and due < today and not closed:
            item = {
                "ref": ref,
                "status": str(status or ""),
                "due_date": due.isoformat(),
                "days_overdue": (today - due).days,
            }
            if doc_type == "ncr":
                indicators["ncr_overdue"] += 1
                if len(evidence["ncr_overdue"]) < 20:
                    evidence["ncr_overdue"].append(item)
            elif doc_type == "rfi":
                indicators["rfi_overdue"] += 1
                if len(evidence["rfi_overdue"]) < 20:
                    evidence["rfi_overdue"].append(item)

        if doc_type in acceptance_types and _is_rejected(status):
            indicators["inspection_rejected"] += 1
            if len(evidence["inspection_rejected"]) < 20:
                evidence["inspection_rejected"].append(
                    {
                        "ref": ref,
                        "status": str(status or ""),
                        "due_date": due.isoformat() if due else "",
                    }
                )

    return indicators, evidence


def _collect_production_signals(
    connection,
    workspace_id: int,
    *,
    schedule: dict[str, Any],
    now: datetime,
) -> tuple[dict[str, Any], dict[str, Any]]:
    result = {
        "production_source_count": 0,
        "production_point_count": 0,
        "production_monitor_ready": False,
        "production_expected_to_move": False,
        "production_changed_24h": None,
        "production_stale_hours": None,
    }
    evidence: dict[str, Any] = {
        "sources": [],
        "last_sync": "",
        "last_change": "",
        "monitor_reason": "",
    }

    required = ("contractor_data_records", "contractor_data_sources", "contractor_data_snapshots")
    if not all(_table_exists(connection, name) for name in required):
        evidence["monitor_reason"] = "production_tables_missing"
        return result, evidence

    try:
        source_rows = connection.execute(
            """SELECT DISTINCT r.source_id
            FROM contractor_data_records r
            WHERE r.workspace_project_id=? AND UPPER(r.record_type)='PRODUCTION'""",
            (int(workspace_id),),
        ).fetchall()
    except Exception:
        source_rows = []

    source_ids = [str(_rowdict(row).get("source_id") or "") for row in source_rows]
    source_ids = [value for value in source_ids if value]
    if not source_ids:
        evidence["monitor_reason"] = "no_production_records"
        return result, evidence

    result["production_source_count"] = len(source_ids)
    try:
        count_row = connection.execute(
            """SELECT COUNT(*) AS n FROM contractor_data_records
            WHERE workspace_project_id=? AND UPPER(record_type)='PRODUCTION'""",
            (int(workspace_id),),
        ).fetchone()
        result["production_point_count"] = int(_rowdict(count_row).get("n") or 0)
    except Exception:
        pass

    placeholders = ",".join("?" for _ in source_ids)
    try:
        rows = connection.execute(
            f"""SELECT source_id,name,last_sync,last_error,enabled
            FROM contractor_data_sources
            WHERE source_id IN ({placeholders})""",
            source_ids,
        ).fetchall()
    except Exception:
        rows = []

    source_map = {str(_rowdict(row).get("source_id") or ""): _rowdict(row) for row in rows}
    latest_sync: datetime | None = None
    has_error = False
    for source_id in source_ids:
        item = source_map.get(source_id, {})
        parsed = _parse_datetime(item.get("last_sync"))
        if parsed and (latest_sync is None or parsed > latest_sync):
            latest_sync = parsed
        error = str(item.get("last_error") or "").strip()
        has_error = has_error or bool(error)
        evidence["sources"].append(
            {
                "source_id": source_id,
                "name": str(item.get("name") or ""),
                "last_sync": str(item.get("last_sync") or ""),
                "last_error": error,
            }
        )

    try:
        snap_rows = connection.execute(
            f"""SELECT source_id,MAX(captured_at) AS last_change
            FROM contractor_data_snapshots
            WHERE workspace_project_id=? AND source_id IN ({placeholders})
            GROUP BY source_id""",
            [int(workspace_id), *source_ids],
        ).fetchall()
    except Exception:
        snap_rows = []

    latest_change: datetime | None = None
    for raw in snap_rows:
        parsed = _parse_datetime(_rowdict(raw).get("last_change"))
        if parsed and (latest_change is None or parsed > latest_change):
            latest_change = parsed

    expected = (
        float(schedule.get("delay_percent") or 0) > 0.1
        or int(schedule.get("delayed_tasks") or 0) > 0
        or (
            0 < float(schedule.get("planned_progress") or 0) < 100
            and float(schedule.get("actual_progress") or 0) < 100
        )
    )
    result["production_expected_to_move"] = bool(expected)

    if latest_sync:
        stale_hours = max(0.0, (now - latest_sync).total_seconds() / 3600.0)
        result["production_stale_hours"] = round(stale_hours, 1)
        evidence["last_sync"] = latest_sync.isoformat(sep=" ", timespec="seconds")
    if latest_change:
        evidence["last_change"] = latest_change.isoformat(sep=" ", timespec="seconds")

    recent_sync = bool(latest_sync and (now - latest_sync) <= timedelta(hours=30))
    result["production_monitor_ready"] = bool(recent_sync and not has_error)
    if result["production_monitor_ready"] and latest_change:
        result["production_changed_24h"] = bool((now - latest_change) <= timedelta(hours=24))
        evidence["monitor_reason"] = "synced_and_checksum_history_available"
    elif has_error:
        evidence["monitor_reason"] = "source_error"
    elif not recent_sync:
        evidence["monitor_reason"] = "source_not_synced_recently"
    else:
        evidence["monitor_reason"] = "no_snapshot_change_history"

    return result, evidence


def collect_supervisor_data(
    db,
    project_id: int,
    *,
    status: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Collect auditable Supervisor indicators directly from the contractor workspace.

    The collector is read-only and tenant-scoped. It never infers an overdue item
    without a real due date and never fabricates a production percentage. Production
    stall detection uses source freshness + snapshot checksum history instead.
    """
    workspace_id = int(project_id)
    current = (now or datetime.now()).replace(tzinfo=None)
    today = current.date()
    status = dict(status or {})
    schedule = dict(status.get("schedule") or {})

    with db.connect() as connection:
        document_indicators, document_evidence = _collect_document_signals(
            connection, workspace_id, today
        )
        production_indicators, production_evidence = _collect_production_signals(
            connection,
            workspace_id,
            schedule=schedule,
            now=current,
        )

    indicators = {
        **document_indicators,
        **production_indicators,
    }
    return {
        "workspace_project_id": workspace_id,
        "collected_at": current.isoformat(sep=" ", timespec="seconds"),
        "indicators": indicators,
        "evidence": {
            "documents": document_evidence,
            "production": production_evidence,
        },
    }


__all__ = ["collect_supervisor_data"]
