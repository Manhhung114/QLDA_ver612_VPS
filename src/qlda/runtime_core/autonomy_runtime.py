from __future__ import annotations

from threading import RLock
from typing import Any, Callable

from qlda.autonomy.digital_twin import TwinState
from qlda.autonomy.events import DomainEvent
from qlda.autonomy.persistence import AutomationRepository
from qlda.autonomy.platform import AutomationPlatform, build_platform
from qlda.autonomy.qlda_adapters import QLDAAutomationAdapters

_LOCK = RLock()
_PLATFORMS: dict[int, AutomationPlatform] = {}
_REPOSITORIES: dict[int, AutomationRepository] = {}


def get_autonomy_platform(
    db,
    *,
    google_sync: Callable[..., Any] | None = None,
    notify: Callable[..., Any] | None = None,
    approve_ipc: Callable[..., Any] | None = None,
    approve_vo: Callable[..., Any] | None = None,
    force_rebuild: bool = False,
) -> AutomationPlatform:
    """Return one durable V7.7→V9.0 automation platform per DB instance."""
    key = id(db)
    with _LOCK:
        if not force_rebuild and key in _PLATFORMS:
            return _PLATFORMS[key]

        repository = AutomationRepository(db.connect)
        repository.ensure_schema()
        adapters = QLDAAutomationAdapters(
            db,
            google_sync=google_sync,
            notify=notify,
            approve_ipc=approve_ipc,
            approve_vo=approve_vo,
        )
        platform = build_platform(handlers=adapters.handlers(), audit_sink=repository.audit)

        # Every drained event is durably recorded. EventBus itself remains small
        # and deterministic while PostgreSQL/SQLite retains the audit history.
        platform.events.subscribe("*", repository.save_event)
        _PLATFORMS[key] = platform
        _REPOSITORIES[key] = repository
        return platform


def get_autonomy_repository(db) -> AutomationRepository:
    get_autonomy_platform(db)
    return _REPOSITORIES[id(db)]


def run_project_supervisor(db, project_id: int, *, actor: str = "AI Supervisor", extra_indicators: dict[str, Any] | None = None) -> dict[str, Any]:
    """V8.1 daily supervisor run with V9 twin update and durable events."""
    platform = get_autonomy_platform(db)
    status = platform.tools.execute(
        "get_project_status",
        project_id=int(project_id),
        actor=actor,
        role="admin",
    )
    integrity = platform.tools.execute(
        "check_data_integrity",
        project_id=int(project_id),
        actor=actor,
        role="admin",
    )
    indicators = {
        "data_integrity_score": float(integrity.get("score") or 0),
        "schedule_delay_percent": float((status.get("schedule") or {}).get("delay_percent") or 0),
        "contract_days_remaining": status.get("contract_days_remaining"),
        "payment_overdue_value": float((status.get("payment") or {}).get("outstanding") or 0),
    }
    indicators.update(dict(extra_indicators or {}))
    health = platform.supervisor.evaluate(int(project_id), indicators)

    schedule = status.get("schedule") or {}
    platform.digital_twin.update(
        TwinState(
            project_id=int(project_id),
            schedule_progress=float(schedule.get("actual_progress") or 0),
            production_progress=float((extra_indicators or {}).get("production_progress", 0) or 0),
            cost_progress=float((extra_indicators or {}).get("cost_progress", 0) or 0),
            quality_open_items=int((extra_indicators or {}).get("quality_open_items", 0) or 0),
            safety_open_items=int((extra_indicators or {}).get("safety_open_items", 0) or 0),
            cash_exposure=float((status.get("payment") or {}).get("outstanding") or 0),
            data_integrity_score=float(integrity.get("score") or 0),
            dimensions={"health_score": health.score},
        )
    )

    for finding in health.findings:
        platform.events.publish(
            DomainEvent(
                event_type=finding.code,
                project_id=int(project_id),
                actor=actor,
                payload={
                    "title": finding.title,
                    "detail": finding.detail,
                    "severity": finding.severity.value,
                    "recommended_action": finding.recommended_action,
                },
            )
        )
    platform.events.drain()
    return {
        "project_id": int(project_id),
        "health_score": health.score,
        "findings": [
            {
                "code": x.code,
                "severity": x.severity.value,
                "title": x.title,
                "detail": x.detail,
                "recommended_action": x.recommended_action,
            }
            for x in health.findings
        ],
        "proposed_actions": platform.supervisor.proposed_actions(health),
        "integrity": integrity,
        "status": status,
    }


__all__ = ["get_autonomy_platform", "get_autonomy_repository", "run_project_supervisor"]
