from __future__ import annotations

from fastapi import APIRouter

import qlda

router = APIRouter(tags=["health"])


@router.get("/api/health")
def health() -> dict[str, object]:
    return {
        "ok": True,
        "service": "qlda-api",
        "version": qlda.__version__,
        "architecture": qlda.ARCHITECTURE,
        "service_layer": qlda.SERVICE_LAYER,
    }
