from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from qlda.presentation.api.dependencies import Principal, require_roles
from qlda.presentation.api.schemas import (
    AIAskRequest,
    AILegalRequest,
    AIProjectRequest,
    AIReportRequest,
    AITestRequest,
)
from qlda.services import AIApplicationError, AIService, ProjectAccessService

router = APIRouter(prefix="/api/v1/ai", tags=["ai"])


def _scope(principal: Principal, project_id: int) -> tuple[int, int | None]:
    scope = ProjectAccessService.require_project(principal.user, project_id)
    workspace_scope = None if scope.is_master_scope else scope.workspace_project_id
    return scope.requested_project_id, workspace_scope


def _ai_error(exc: AIApplicationError) -> HTTPException:
    return HTTPException(
        status_code=503 if exc.retryable else 400,
        detail={
            "code": exc.code,
            "message": str(exc),
            "retryable": exc.retryable,
            "action": exc.action,
        },
    )


@router.post("/ask")
def ask_ai(
    request: AIAskRequest,
    principal: Principal = Depends(require_roles("read", "update", "admin")),
):
    project_id, workspace_scope = _scope(principal, request.project_id)
    try:
        answer = AIService.ask(
            project_id,
            request.question,
            provider=request.provider,
            history=request.history,
            status_date=request.status_date,
            use_web=request.use_web,
            workspace_scope=workspace_scope,
        )
    except AIApplicationError as exc:
        raise _ai_error(exc) from exc
    return {"ok": True, "answer": answer, "provider": request.provider}


@router.post("/schedule-risk")
def schedule_risk(
    request: AIProjectRequest,
    principal: Principal = Depends(require_roles("read", "update", "admin")),
):
    project_id, workspace_scope = _scope(principal, request.project_id)
    try:
        answer = AIService.schedule_risk(
            project_id,
            provider=request.provider,
            status_date=request.status_date,
            workspace_scope=workspace_scope,
        )
    except AIApplicationError as exc:
        raise _ai_error(exc) from exc
    return {"ok": True, "answer": answer, "provider": request.provider}


@router.post("/report")
def draft_report(
    request: AIReportRequest,
    principal: Principal = Depends(require_roles("read", "update", "admin")),
):
    project_id, workspace_scope = _scope(principal, request.project_id)
    try:
        answer = AIService.draft_report(
            project_id,
            provider=request.provider,
            period=request.period,
            status_date=request.status_date,
            workspace_scope=workspace_scope,
        )
    except AIApplicationError as exc:
        raise _ai_error(exc) from exc
    return {"ok": True, "answer": answer, "provider": request.provider}


@router.post("/legal")
def legal_qa(
    request: AILegalRequest,
    principal: Principal = Depends(require_roles("read", "update", "admin")),
):
    project_id, workspace_scope = _scope(principal, request.project_id)
    try:
        answer = AIService.legal_qa(
            project_id,
            request.question,
            provider=request.provider,
            status_date=request.status_date,
            use_web=request.use_web,
            workspace_scope=workspace_scope,
        )
    except AIApplicationError as exc:
        raise _ai_error(exc) from exc
    return {"ok": True, "answer": answer, "provider": request.provider}


@router.post("/test")
def test_ai(
    request: AITestRequest,
    principal: Principal = Depends(require_roles("admin")),
):
    del principal
    try:
        result = AIService.test_connection(provider=request.provider)
    except AIApplicationError as exc:
        raise _ai_error(exc) from exc
    return {"ok": True, "result": result, "provider": request.provider}
