from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from qlda.bootstrap import get_application
from qlda.domain.errors import AIApplicationError
from qlda.presentation.api.dependencies import Principal, require_roles
from qlda.presentation.api.schemas import (
    AIAskRequest,
    AILegalRequest,
    AIProjectRequest,
    AIReportRequest,
    AITestRequest,
)
from qlda.modules.contractor_data.worker import build_db

router = APIRouter(prefix="/api/v1/ai", tags=["ai"])


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


def _scope(principal: Principal, project_id: int) -> tuple[int, int]:
    """Resolve exactly one AI tenant.

    Contractor AI is tenant-isolated by ``workspace_project_id``. A master project
    that owns active contractor workspaces cannot be used implicitly for normal AI
    requests, even by Admin. Admin/BĐH must select one contractor workspace first.
    Cross-contractor analysis belongs to a separate explicit Project Control path.
    """
    scope = get_application().access.require_project(principal.user, int(project_id))
    if scope.is_master_scope and _master_has_contractors(int(scope.master_project_id)):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "contractor_ai_workspace_required",
                "message": (
                    "AI nhà thầu chạy độc lập. Hãy chọn một nhà thầu đang làm việc "
                    "trước khi sử dụng Trợ lý AI."
                ),
                "master_project_id": int(scope.master_project_id),
            },
        )
    tenant = int(scope.workspace_project_id or scope.requested_project_id)
    return tenant, tenant


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
        answer = get_application().ai.ask(
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
    return {
        "ok": True,
        "answer": answer,
        "provider": request.provider,
        "workspace_project_id": workspace_scope,
    }


@router.post("/schedule-risk")
def schedule_risk(
    request: AIProjectRequest,
    principal: Principal = Depends(require_roles("read", "update", "admin")),
):
    project_id, workspace_scope = _scope(principal, request.project_id)
    try:
        answer = get_application().ai.schedule_risk(
            project_id,
            provider=request.provider,
            status_date=request.status_date,
            workspace_scope=workspace_scope,
        )
    except AIApplicationError as exc:
        raise _ai_error(exc) from exc
    return {
        "ok": True,
        "answer": answer,
        "provider": request.provider,
        "workspace_project_id": workspace_scope,
    }


@router.post("/report")
def draft_report(
    request: AIReportRequest,
    principal: Principal = Depends(require_roles("read", "update", "admin")),
):
    project_id, workspace_scope = _scope(principal, request.project_id)
    try:
        answer = get_application().ai.draft_report(
            project_id,
            provider=request.provider,
            period=request.period,
            status_date=request.status_date,
            workspace_scope=workspace_scope,
        )
    except AIApplicationError as exc:
        raise _ai_error(exc) from exc
    return {
        "ok": True,
        "answer": answer,
        "provider": request.provider,
        "workspace_project_id": workspace_scope,
    }


@router.post("/legal")
def legal_qa(
    request: AILegalRequest,
    principal: Principal = Depends(require_roles("read", "update", "admin")),
):
    project_id, workspace_scope = _scope(principal, request.project_id)
    try:
        answer = get_application().ai.legal_qa(
            project_id,
            request.question,
            provider=request.provider,
            status_date=request.status_date,
            use_web=request.use_web,
            workspace_scope=workspace_scope,
        )
    except AIApplicationError as exc:
        raise _ai_error(exc) from exc
    return {
        "ok": True,
        "answer": answer,
        "provider": request.provider,
        "workspace_project_id": workspace_scope,
    }


@router.post("/test")
def test_ai(
    request: AITestRequest,
    principal: Principal = Depends(require_roles("admin")),
):
    del principal
    try:
        result = get_application().ai.test_connection(provider=request.provider)
    except AIApplicationError as exc:
        raise _ai_error(exc) from exc
    return {"ok": True, "result": result, "provider": request.provider}
