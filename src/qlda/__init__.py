from __future__ import annotations

"""QLDA V7.2 clean-architecture package.

Dependency rule:
    domain <- application <- infrastructure/presentation

V7.2 adds native PostgreSQL project/workspace authorization. Compatibility
adapters remain only for AI context and Excel import processing.
"""

__version__ = "7.2"
ARCHITECTURE = "clean-architecture"
SERVICE_LAYER = "application-use-cases"
HTTP_API = "fastapi"
CLEAN_CORE = True
NATIVE_ADAPTERS = ("sessions", "project-access", "files", "jobs", "search")
LEGACY_ADAPTERS = ("ai", "excel")
