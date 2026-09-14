from __future__ import annotations

"""QLDA V7.3 clean-architecture package.

Dependency rule:
    domain <- application <- infrastructure/presentation

V7.3 adds a native AI infrastructure boundary. Session, project access, files,
jobs, search and AI are now wired without the deprecated V6 service-layer
facades. Only the business-heavy Excel import pipeline remains behind the
anti-corruption adapter while it is migrated incrementally.
"""

__version__ = "7.3"
ARCHITECTURE = "clean-architecture"
SERVICE_LAYER = "application-use-cases"
HTTP_API = "fastapi"
CLEAN_CORE = True
NATIVE_ADAPTERS = ("sessions", "project-access", "files", "jobs", "search", "ai")
LEGACY_ADAPTERS = ("excel",)
