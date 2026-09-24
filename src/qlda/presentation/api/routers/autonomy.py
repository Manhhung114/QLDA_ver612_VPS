from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from qlda.bootstrap import get_application
from qlda.presentation.api.dependencies import Principal, require_roles
from qlda.modules.contractor_data.worker import build_db
from qlda.runtime_core.autonomy_runtime import (
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


class TwinScenarioRequest(BaseModel):
    project_id: int
    scenario: dict[str, Any] = Field(default_factory=dict)


class ApprovalDecisionRequest(BaseModel):
    project_id: int
    plan_id: str
    step_id: str
    approved: bool
    note: str = ""


def _scope(principal: Principal, project_id: int):
    return get_application().access.require_project(principal.user, int(project_id))


def _execution_project_id(scope) -> int:
    # Project-level users act on the master project; contractor-scoped accounts
    # remain confined to their authorized workspace.
    return int(scope.master_project_id if scope.is_master_scope else scope.workspace_project_id)


@router.get("/{project_id}/capabilities")
def capabilities(
    project_id: int,
    principal: Principal = Depends(require_roles("read", "update", "admin")),
):
    scope = _scope(principal, project_id)
    platform = get_autonomy_platform(build_db())
    return {
        "ok": True,
        "project_id": _execution_project_id(scope),
        "master_project_id": int(scope.master_project_id),
        "workspace_project_id": int(scope.workspace_project_id),
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
    plan = platform.orchestrator.create_plan(project_id, request.objective)
    return {"ok": True, "plan": asdict(plan)}


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

    # V7.7 gate is evaluated immediately before execution; write-capable steps
    # are blocked if the current synchronized evidence is not reconciled.
    integrity = platform.tools.execute(
        "check_data_integrity",
        project_id=project_id,
        actor=principal.email,
        role=principal.role,
    )
    plan = platform.orchestrator.create_plan(project_id, request.objective)
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
    return {"ok": True, "result": result}


@router.post("/simulate")
def simulate(
    request: TwinScenarioRequest,
    principal: Principal = Depends(require_roles("read", "update", "admin")),
):
    scope = _scope(principal, request.project_id)
    project_id = _execution_project_id(scope)
    db = build_db()
    platform = get_autonomy_platform(db)
    if platform.digital_twin.get(project_id) is None:
        run_project_supervisor(db, project_id, actor=principal.email or "AI Supervisor")
    result = platform.digital_twin.simulate(project_id, request.scenario)
    return {"ok": True, "scenario": asdict(result)}


@router.get("/{project_id}/approvals")
def pending_approvals(
    project_id: int,
    principal: Principal = Depends(require_roles("admin")),
):
    scope = _scope(principal, project_id)
    target = _execution_project_id(scope)
    return {
        "ok": True,
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
    return {"ok": True, "status": "APPROVED" if request.approved else "REJECTED"}
