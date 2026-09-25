from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from qlda.bootstrap import get_application
from qlda.presentation.api.dependencies import Principal, require_roles
from qlda.modules.contractor_data.worker import build_db
from qlda.autonomy.runtime import (
    get_autonomy_platform,
    get_autonomy_repository,
    run_project_supervisor,
)

router = APIRouter(prefix="/api/v1/autonomy", tags=["autonomy"])


class PlanRequest(BaseModel):
    project_id: int
    objective: str = Field(min_length=3, max_length=4000)


class ExecuteRequest(BaseModel):
    project_id: int
    objective: str = Field(min_length=3, max_length=4000)
    approvals: list[str] = Field(default_factory=list)
    dry_run: bool = False


class SupervisorRequest(BaseModel):
    project_id: int
    indicators: dict[str, Any] = Field(default_factory=dict)


class ApprovalDecisionRequest(BaseModel):
    project_id: int
    plan_id: str
    step_id: str
    approved: bool
    note: str = ""


def _scope(principal: Principal, project_id: int):
    return get_application().access.require_project(principal.user, int(project_id))


def _master_has_contractors(master_project_id: int) -> bool:
    db = build_db()
    try:
        with db.connect() as connection:
            row = connection.execute(
                """SELECT 1 FROM project_contractors
                WHERE master_project_id=? AND status='Đang hoạt động' LIMIT 1""",
                (int(master_project_id),),
            ).fetchone()
        return bool(row)
    except Exception:
        return False


def _execution_project_id(scope) -> int:
    """Return exactly one contractor AI tenant.

    Master projects with active contractor workspaces are intentionally rejected.
    Admin/BĐH must select the desired contractor workspace. This prevents events,
    snapshots, approvals and AI state from mixing contractors.
    """
    if bool(scope.is_master_scope) and _master_has_contractors(int(scope.master_project_id)):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "contractor_ai_workspace_required",
                "message": (
                    "AI nhà thầu chạy riêng biệt. Hãy chọn một nhà thầu đang làm việc "
                    "trước khi chạy AI Supervisor/Orchestrator."
                ),
                "master_project_id": int(scope.master_project_id),
            },
        )
    return int(scope.workspace_project_id or scope.requested_project_id)


def _planner_context(principal: Principal) -> dict[str, Any]:
    return {
        "default_assignee_email": str(principal.email or ""),
        "default_assignee_name": str((principal.user or {}).get("name") or principal.email or ""),
        "requester_role": str(principal.role or "read"),
    }


@router.get("/{project_id}/capabilities")
def capabilities(
    project_id: int,
    principal: Principal = Depends(require_roles("read", "update", "admin")),
):
    scope = _scope(principal, project_id)
    tenant_id = _execution_project_id(scope)
    platform = get_autonomy_platform(build_db())
    return {
        "ok": True,
        "project_id": tenant_id,
        "master_project_id": int(scope.master_project_id),
        "workspace_project_id": tenant_id,
        "role": principal.role,
        "target_stage": platform.target_stage.value,
        "capabilities": platform.capabilities,
        "tools": [asdict(spec) for spec in platform.tools.list_specs()],
    }


@router.post("/plan")
def create_plan(
    request: PlanRequest,
    principal: Principal = Depends(require_roles("read", "update", "admin")),
):
    scope = _scope(principal, request.project_id)
    project_id = _execution_project_id(scope)
    platform = get_autonomy_platform(build_db())
    plan = platform.orchestrator.create_plan(
        project_id,
        request.objective,
        context=_planner_context(principal),
    )
    return {"ok": True, "workspace_project_id": project_id, "plan": asdict(plan)}


@router.post("/execute")
def execute_plan(
    request: ExecuteRequest,
    principal: Principal = Depends(require_roles("update", "admin")),
):
    scope = _scope(principal, request.project_id)
    project_id = _execution_project_id(scope)
    db = build_db()
    platform = get_autonomy_platform(db)
    repository = get_autonomy_repository(db)

    integrity = platform.tools.execute(
        "check_data_integrity",
        project_id=project_id,
        actor=principal.email,
        role=principal.role,
    )
    plan = platform.orchestrator.create_plan(
        project_id,
        request.objective,
        context=_planner_context(principal),
    )
    approved_steps = repository.approved_steps(project_id=project_id, plan_id=plan.plan_id)
    approved_steps.update(str(x) for x in request.approvals)
    results = platform.orchestrator.execute_plan(
        plan,
        actor=principal.email,
        role=principal.role,
        approvals=approved_steps,
        dry_run=bool(request.dry_run),
        data_valid=bool(integrity.get("valid", False)),
    )

    for result in results:
        if result.status == "PENDING_APPROVAL":
            repository.request_approval(
                project_id=project_id,
                plan_id=plan.plan_id,
                step_id=result.step_id,
                tool_name=result.tool_name,
                requested_by=principal.email,
            )
    return {
        "ok": True,
        "workspace_project_id": project_id,
        "integrity": integrity,
        "plan": asdict(plan),
        "approved_steps": sorted(approved_steps),
        "results": [asdict(x) for x in results],
    }


@router.post("/supervisor")
def supervisor(
    request: SupervisorRequest,
    principal: Principal = Depends(require_roles("read", "update", "admin")),
):
    scope = _scope(principal, request.project_id)
    project_id = _execution_project_id(scope)
    result = run_project_supervisor(
        build_db(),
        project_id,
        actor=principal.email or "AI Supervisor",
        extra_indicators=request.indicators,
    )
    return {"ok": True, "workspace_project_id": project_id, "result": result}


@router.get("/{project_id}/approvals")
def pending_approvals(
    project_id: int,
    principal: Principal = Depends(require_roles("admin")),
):
    scope = _scope(principal, project_id)
    target = _execution_project_id(scope)
    return {
        "ok": True,
        "workspace_project_id": target,
        "approvals": get_autonomy_repository(build_db()).pending_approvals(project_id=target),
    }


@router.post("/approval")
def decide_approval(
    request: ApprovalDecisionRequest,
    principal: Principal = Depends(require_roles("admin")),
):
    scope = _scope(principal, request.project_id)
    target = _execution_project_id(scope)
    repository = get_autonomy_repository(build_db())
    repository.decide_approval(
        project_id=target,
        plan_id=request.plan_id,
        step_id=request.step_id,
        approved=bool(request.approved),
        approved_by=principal.email,
        note=request.note,
    )
    return {
        "ok": True,
        "workspace_project_id": target,
        "status": "APPROVED" if request.approved else "REJECTED",
    }
