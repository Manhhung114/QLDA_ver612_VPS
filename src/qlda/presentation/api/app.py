from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse

import qlda
from qlda.presentation.api.routers import ai, autonomy, files, health, jobs, search


def create_app() -> FastAPI:
    application = FastAPI(
        title="QLDA API",
        description="QLDA packaged HTTP API for jobs, files, AI, search and the V7.7→V9.0 automation platform.",
        version=qlda.__version__,
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
    )

    @application.exception_handler(FileNotFoundError)
    async def not_found_handler(_request, exc: FileNotFoundError):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @application.exception_handler(PermissionError)
    async def forbidden_handler(_request, exc: PermissionError):
        return JSONResponse(status_code=403, content={"detail": str(exc)})

    @application.exception_handler(ValueError)
    async def bad_request_handler(_request, exc: ValueError):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    application.include_router(health.router)
    application.include_router(jobs.router)
    application.include_router(files.router)
    application.include_router(ai.router)
    application.include_router(autonomy.router)
    application.include_router(search.router)
    return application


app = create_app()
