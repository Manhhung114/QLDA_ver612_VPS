from __future__ import annotations

import os
from datetime import datetime
from threading import RLock
from typing import Any, Callable
from zoneinfo import ZoneInfo

from qlda.autonomy.ai_planner import StructuredAIPlanner
from qlda.autonomy.digital_twin import TwinState
from qlda.autonomy.events import DomainEvent
from qlda.autonomy.persistence import AutomationRepository
from qlda.autonomy.platform import AutomationPlatform, build_platform
from qlda.autonomy.qlda_adapters import QLDAAutomationAdapters

_LOCK = RLock()
_PLATFORMS: dict[int, AutomationPlatform] = {}
_REPOSITORIES: dict[int, AutomationRepository] = {}
_VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def _env_bool(name: str, default: bool) -> bool:
    raw = str(os.environ.get(name, "") or "").strip().lower()
    if not raw:
        return bool(default)
    return raw in {"1", "true", "yes", "on"}


def _install_common_ai_planner(platform: AutomationPlatform) -> None:
    """Use the app-wide assistant provider for planning; fall back safely on error."""
    if not _env_bool("QLDA_AUTONOMY_AI_PLANNER_ENABLED", True):
        return
    provider = str(os.environ.get("QLDA_AUTONOMY_AI_PROVIDER", "openai") or "openai").strip().lower()

    def complete(project_id: int, prompt: str) -> str:
        from qlda.bootstrap import get_application

        return get_application().ai.ask(
            int(project_id),
            prompt,
            provider=provider,
            history=[],
            use_web=False,
        )

    platform.orchestrator.planner = StructuredAIPlanner(
        complete,
        platform.tools.list_specs(),
        fallback=platform.orchestrator.planner,
    )


def _native_google_sync(db) -> Callable[..., Any]:
    """Return the production Google sync adapter used by the AI ToolRegistry.

    This is the same read-only Contractor Data Hub service used by the UI/worker.
    Public links work without OAuth; private sources reuse the persisted project
    OAuth connection and persist refreshed tokens afterward.
    """

    def sync(*, project_id: int, actor: str = "", workspace_ids=None, **_: Any) -> dict[str, Any]:
        from qlda.application.contractor_data_hub import ContractorDataHubService
        from qlda.infrastructure.google_sheets.drive import GoogleWorkspaceClient
        from qlda.runtime_core.google_connection_store import (
            load_project_connection,
            save_project_connection,
        )
        from qlda.runtime_core.google_oauth_settings import apply_to_environment

        apply_to_environment()
        stored = load_project_connection(int(project_id))
        token_state = dict(stored.get("token_state") or {})
        client = GoogleWorkspaceClient(token_state) if token_state else None
        service = ContractorDataHubService(db, client=client)
        result = service.sync_project(
            int(project_id),
            workspace_ids=workspace_ids,
            trigger_type="AI_AUTOMATION",
        )
        if client is not None and client.authorized:
            save_project_connection(
                int(project_id),
                client.token_state(),
                account_email=str(stored.get("account_email") or ""),
                account_name=str(stored.get("account_name") or ""),
                scopes=[GoogleWorkspaceClient.SHEETS_SCOPE, GoogleWorkspaceClient.DRIVE_SCOPE],
            )
        return {
            "project_id": int(project_id),
            "actor": actor,
            "oauth_connected": bool(token_state),
            **dict(result or {}),
        }

    return sync


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
            google_sync=google_sync or _native_google_sync(db),
            notify=notify,
            approve_ipc=approve_ipc,
            approve_vo=approve_vo,
        )
        platform = build_platform(handlers=adapters.handlers(), audit_sink=repository.audit)
        _install_common_ai_planner(platform)

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
    """V8.1 supervisor run with V9 twin update, snapshot and durable events."""
    platform = get_autonomy_platform(db)
    repository = get_autonomy_repository(db)
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
    twin = TwinState(
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
    platform.digital_twin.update(twin)

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

    result = {
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
        "twin": {
            "schedule_progress": twin.schedule_progress,
            "production_progress": twin.production_progress,
            "cost_progress": twin.cost_progress,
            "cash_exposure": twin.cash_exposure,
            "data_integrity_score": twin.data_integrity_score,
        },
        "local_day": datetime.now(_VN_TZ).date().isoformat(),
    }
    repository.save_snapshot(
        project_id=int(project_id),
        snapshot_type="DAILY_SUPERVISOR",
        payload=result,
    )
    return result


def run_daily_supervisor_if_due(
    db,
    project_id: int,
    *,
    actor: str = "AI Supervisor",
    extra_indicators: dict[str, Any] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Run at most once per Vietnam-local day unless `force=True`."""
    repository = get_autonomy_repository(db)
    today = datetime.now(_VN_TZ).date().isoformat()
    latest = repository.latest_snapshot(project_id=int(project_id), snapshot_type="DAILY_SUPERVISOR")
    payload = latest.get("payload") if isinstance(latest, dict) else None
    if not force and isinstance(payload, dict) and str(payload.get("local_day") or "") == today:
        return {"project_id": int(project_id), "skipped": True, "reason": "already_ran_today", "snapshot": payload}
    return run_project_supervisor(
        db,
        int(project_id),
        actor=actor,
        extra_indicators=extra_indicators,
    )


__all__ = [
    "get_autonomy_platform",
    "get_autonomy_repository",
    "run_project_supervisor",
    "run_daily_supervisor_if_due",
]
