from __future__ import annotations

from threading import RLock
from typing import Any

from .loop_engine import ClosedLoopEngine, ClosedLoopRepository
from .runtime import get_autonomy_platform, get_autonomy_repository, run_project_supervisor


_LOCK = RLock()
_ENGINES: dict[int, ClosedLoopEngine] = {}
_REPOSITORIES: dict[int, ClosedLoopRepository] = {}


def get_closed_loop_repository(db) -> ClosedLoopRepository:
    key = id(db)
    with _LOCK:
        repository = _REPOSITORIES.get(key)
        if repository is None:
            repository = ClosedLoopRepository(db.connect)
            repository.ensure_schema()
            _REPOSITORIES[key] = repository
        return repository


def get_closed_loop_engine(db) -> ClosedLoopEngine:
    key = id(db)
    with _LOCK:
        engine = _ENGINES.get(key)
        if engine is None:
            engine = ClosedLoopEngine(
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
    """Run a complete observation cycle for exactly one workspace.

    The supervisor performs Sense + Analyze + Recommend. The engine then Verify/Learn
    against the previous observation. Execution is opt-in and still passes through
    ToolRegistry RBAC/approval/audit gates. ``dry_run`` defaults to True on purpose.
    """
    tenant_id = int(project_id)
    result = run_project_supervisor(
        db,
        tenant_id,
        actor=str(actor),
        extra_indicators=extra_indicators,
    )
    engine = get_closed_loop_engine(db)
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
    "add_closed_loop_feedback",
    "get_closed_loop_engine",
    "get_closed_loop_repository",
    "latest_closed_loop",
    "run_closed_loop_cycle",
]
