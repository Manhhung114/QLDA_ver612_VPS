from __future__ import annotations

"""QLDA modular monolith package.

V6.25 established module boundaries. V6.26 added the application Service Layer.
V6.27 adds a thin FastAPI transport for jobs, files, AI and search while keeping
business rules inside services/modules.
"""

__version__ = "6.27"
ARCHITECTURE = "modular-monolith"
SERVICE_LAYER = "application-services"
HTTP_API = "fastapi"
