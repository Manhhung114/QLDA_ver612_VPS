from __future__ import annotations

from fastapi import APIRouter, Depends

from qlda.presentation.api.dependencies import Principal, require_roles
from qlda.presentation.api.schemas import SearchRequest
from qlda.services import ProjectAccessService, SearchService

router = APIRouter(prefix="/api/v1/search", tags=["search"])


@router.post("")
def search(
    request: SearchRequest,
    principal: Principal = Depends(require_roles("read", "update", "admin")),
):
    scope = ProjectAccessService.require_project(principal.user, request.project_id)
    rows = SearchService.search(
        scope.workspace_project_id,
        request.query,
        kinds=request.kinds,
        limit=request.limit,
    )
    return {
        "ok": True,
        "project_id": scope.workspace_project_id,
        "query": request.query,
        "count": len(rows),
        "results": rows,
    }
