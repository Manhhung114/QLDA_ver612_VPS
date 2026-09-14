from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse

from qlda.bootstrap import get_application
from qlda.presentation.api.dependencies import Principal, current_principal, require_roles
from qlda.presentation.api.schemas import FileCountRequest, FileTicketRequest

router = APIRouter(prefix="/api/v1/files", tags=["files"])


@router.get("")
def list_files(
    project_code: str,
    kind: str,
    subtype: str = "Khac",
    record_code: str = "Chung",
    include_history: bool = False,
    principal: Principal = Depends(current_principal),
):
    services = get_application()
    services.access.require_project_code(principal.user, project_code)
    return services.files.list_record_files(
        principal.token,
        project_code=project_code,
        kind=kind,
        subtype=subtype,
        record_code=record_code,
        include_history=include_history,
    )


@router.post("/counts")
def file_counts(
    request: FileCountRequest,
    principal: Principal = Depends(current_principal),
):
    services = get_application()
    services.access.require_project_code(principal.user, request.project_code)
    return services.files.record_file_counts(
        principal.token,
        project_code=request.project_code,
        kind=request.kind,
        subtype=request.subtype,
        record_codes=request.record_codes,
    )


@router.post("/upload-ticket")
def upload_ticket(
    request: FileTicketRequest,
    principal: Principal = Depends(require_roles("update", "admin")),
):
    services = get_application()
    services.access.require_project_code(principal.user, request.project_code)
    return services.files.make_upload_ticket(
        principal.token,
        project_code=request.project_code,
        kind=request.kind,
        subtype=request.subtype,
        record_code=request.record_code,
        upload_purpose=request.upload_purpose,
        max_bytes=request.max_bytes,
    )


@router.get("/{file_id}")
def file_info(
    file_id: str,
    principal: Principal = Depends(current_principal),
):
    services = get_application()
    info = services.files.info(principal.token, file_id)
    data = dict(info.get("file") or {})
    services.access.require_project_code(principal.user, str(data.get("project_code") or ""))
    return info


@router.get("/{file_id}/download")
def download_file(
    file_id: str,
    principal: Principal = Depends(current_principal),
):
    services = get_application()
    info = services.files.info(principal.token, file_id)
    data = dict(info.get("file") or {})
    services.access.require_project_code(principal.user, str(data.get("project_code") or ""))
    _, path = services.files.local_path(file_id)
    return FileResponse(
        path,
        filename=str(data.get("name") or path.name),
        media_type=str(data.get("mime_type") or "application/octet-stream"),
    )


@router.delete("/{file_id}")
def trash_file(
    file_id: str,
    principal: Principal = Depends(require_roles("update", "admin")),
):
    services = get_application()
    info = services.files.info(principal.token, file_id)
    data = dict(info.get("file") or {})
    services.access.require_project_code(principal.user, str(data.get("project_code") or ""))
    return services.files.trash(principal.token, file_id)
