from __future__ import annotations

"""QLDA V7.1 clean-architecture package.

Dependency rule:
    domain <- application <- infrastructure/presentation

V7.1 replaces compatibility adapters for sessions, local files, durable jobs and
search with native infrastructure implementations while preserving the existing
PostgreSQL schemas, VPS storage layout and public HTTP contracts.
"""

__version__ = "7.1"
ARCHITECTURE = "clean-architecture"
SERVICE_LAYER = "application-use-cases"
HTTP_API = "fastapi"
CLEAN_CORE = True
NATIVE_ADAPTERS = ("sessions", "files", "jobs", "search")
LEGACY_ADAPTERS = ("project-access", "ai", "excel")
