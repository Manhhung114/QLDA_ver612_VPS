from __future__ import annotations

from threading import RLock
from typing import Any

from .loop_engine import ClosedLoopEngine, ClosedLoopRepository
from .runtime import get_autonomy_platform, get_autonomy_repository, run_project_supervisor


class RuntimeClosedLoopRepository(ClosedLoopRepository):
    """Production repository guard: missing rows stay empty/fail-closed."""

    def get_loop(self, *, project_id: int, loop_id: str) -> dict[str, Any]:
        row = super().get_loop(project_id=int(project_id), loop_id=str(loop_id))
        return row if str(row.get("loop_id") or "") else {}

    def latest_loop(self, *, project_id: int, open_only: bool = False) -> dict[str, Any]:
        row = super().latest_loop(project_id=int(project_id), open_only=bool(open_only))
        return row if str(row.get("loop_id") or "") else {}


class RuntimeClosedLoopEngine(ClosedLoopEngine):
    """Production Act gate with persistent dedupe and Data Integrity fail-close."""

    _INTEGRITY_RECOVERY_TOOLS = {"check_data_integrity", "sync_google_data", "generate_report"}

    def execute_ready(
        self,
        *,
        project_id: int,
        loop_id: str,
        actor: str,
        role: str = "admin",
        dry_run: bool = True,
    ) -> dict[str, Any]:
        loop = self.repository.get_loop(project_id=int(project_id), loop_id=str(loop_id))
        if not loop:
            raise ValueError("Không tìm thấy closed loop trong workspace hiện tại")
        if str(loop.get("status") or "") == "CLOSED":
            return loop

        approved_steps = self.automation_repository.approved_steps(
            project_id=int(project_id), plan_id=str(loop_id)
        )
        actions = list(loop.get("actions") or [])
        already_executed = {
            str(item.get("step_id") or "")
            for item in actions
            if str(item.get("status") or "") == "SUCCESS"
        }
        integrity = dict((loop.get("sensed") or {}).get("integrity") or {})
        integrity_valid = bool(integrity.get("valid", False))
        recommendations = [dict(x) for x in list(loop.get("recommendations") or [])]

        for recommendation in recommendations:
            step_id = str(recommendation.get("step_id") or "")
            tool_name = str(recommendation.get("tool") or "")
            status = str(recommendation.get("status") or "")

            if step_id in already_executed:
                recommendation["status"] = "ALREADY_EXECUTED"
                continue
            if status in {"NEEDS_INPUT", "FORBIDDEN", "UNAVAILABLE", "ALREADY_EXECUTED"}:
                continue
            if not dry_run and not integrity_valid and tool_name not in self._INTEGRITY_RECOVERY_TOOLS:
                recommendation["status"] = "BLOCKED_DATA_INTEGRITY"
                actions.append({
                    "step_id": step_id,
                    "tool": tool_name,
                    "status": "BLOCKED_DATA_INTEGRITY",
                    "dry_run": False,
                    "approved": False,
                    "error": "AI_DATA_VALID=FALSE; action ghi dữ liệu bị chặn.",
                })
                continue

            requires_approval = bool(recommendation.get("requires_approval"))
            approved = step_id in approved_steps
            if requires_approval and not approved:
                self.automation_repository.request_approval(
                    project_id=int(project_id),
                    plan_id=str(loop_id),
                    step_id=step_id,
                    tool_name=tool_name,
                    requested_by=str(actor),
                )
                recommendation["status"] = "PENDING_APPROVAL"
                continue

            try:
                output = self.platform.tools.execute(
                    tool_name,
                    project_id=int(project_id),
                    actor=str(actor),
                    role=str(role),
                    arguments=dict(recommendation.get("arguments") or {}),
                    approved=approved,
                    dry_run=bool(dry_run),
                )
                run_status = "DRY_RUN" if dry_run else "SUCCESS"
                recommendation["status"] = run_status
                actions.append({
                    "step_id": step_id,
                    "tool": tool_name,
                    "status": run_status,
                    "dry_run": bool(dry_run),
                    "approved": bool(approved),
                    "output": output,
                })
            except Exception as exc:
                recommendation["status"] = "FAILED"
                actions.append({
                    "step_id": step_id,
                    "tool": tool_name,
                    "status": "FAILED",
                    "dry_run": bool(dry_run),
                    "approved": bool(approved),
                    "error": str(exc),
                })

        loop["recommendations"] = recommendations
        loop["actions"] = actions[-200:]
        statuses = {str(x.get("status") or "") for x in recommendations}
        if "PENDING_APPROVAL" in statuses:
            loop["current_stage"] = "APPROVE"
        elif "BLOCKED_DATA_INTEGRITY" in statuses:
            loop["current_stage"] = "SENSE"
        else:
            loop["current_stage"] = "VERIFY"
        loop["learning"] = self.repository.learning_stats(project_id=int(project_id))
        self.repository.save_loop(loop)
        return self.repository.get_loop(project_id=int(project_id), loop_id=str(loop_id))


_LOCK = RLock()
_ENGINES: dict[int, RuntimeClosedLoopEngine] = {}
_REPOSITORIES: dict[int, RuntimeClosedLoopRepository] = {}


def get_closed_loop_repository(db) -> RuntimeClosedLoopRepository:
    key = id(db)
    with _LOCK:
        repository = _REPOSITORIES.get(key)
        if repository is None:
            repository = RuntimeClosedLoopRepository(db.connect)
            repository.ensure_schema()
            _REPOSITORIES[key] = repository
        return repository


def get_closed_loop_engine(db) -> RuntimeClosedLoopEngine:
    key = id(db)
    with _LOCK:
        engine = _ENGINES.get(key)
        if engine is None:
            engine = RuntimeClosedLoopEngine(
                platform=get_autonomy_platform(db),
                automation_repository=get_autonomy_repository(db),
                repository=get_closed_loop_repository(db),
            )
            _ENGINES[key] = engine
        return engine


def run_closed_loop_cycle(
    db,
    project_id: int,
    *,
    actor: str = "AI Supervisor",
    role: str = "admin",
    execute: bool = False,
    dry_run: bool = True,
    extra_indicators: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one complete observation cycle for exactly one workspace.

    ``run_project_supervisor`` is the single Sense/Analyze/Recommend capture point.
    This function therefore reads that just-captured loop instead of capturing a
    second time. Execution remains opt-in and passes RBAC/Approval/Audit/Data-
    Integrity gates. ``dry_run`` defaults to True on purpose.
    """
    tenant_id = int(project_id)
    result = run_project_supervisor(
        db,
        tenant_id,
        actor=str(actor),
        extra_indicators=extra_indicators,
    )
    engine = get_closed_loop_engine(db)
    loop = get_closed_loop_repository(db).latest_loop(project_id=tenant_id)
    if not loop:
        # Fail-soft fallback for callers that supply an older Supervisor runtime.
        loop = engine.capture_supervisor_result(result, actor=str(actor), role=str(role))
    if execute and str(loop.get("status") or "") != "CLOSED":
        loop = engine.execute_ready(
            project_id=tenant_id,
            loop_id=str(loop.get("loop_id") or ""),
            actor=str(actor),
            role=str(role),
            dry_run=bool(dry_run),
        )
    return {
        "project_id": tenant_id,
        "supervisor": result,
        "loop": loop,
        "learning": engine.learning_summary(project_id=tenant_id),
    }


def latest_closed_loop(db, project_id: int) -> dict[str, Any]:
    return get_closed_loop_repository(db).latest_loop(project_id=int(project_id))


def add_closed_loop_feedback(
    db,
    project_id: int,
    loop_id: str,
    *,
    actor: str,
    rating: str,
    note: str = "",
    finding_code: str = "",
    tool_name: str = "",
) -> dict[str, Any]:
    return get_closed_loop_engine(db).add_feedback(
        project_id=int(project_id),
        loop_id=str(loop_id),
        actor=str(actor),
        rating=str(rating),
        note=str(note),
        finding_code=str(finding_code),
        tool_name=str(tool_name),
    )


__all__ = [
    "RuntimeClosedLoopEngine",
    "RuntimeClosedLoopRepository",
    "add_closed_loop_feedback",
    "get_closed_loop_engine",
    "get_closed_loop_repository",
    "latest_closed_loop",
    "run_closed_loop_cycle",
]
