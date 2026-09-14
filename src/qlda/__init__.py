from __future__ import annotations

"""QLDA V7.4 clean-architecture package.

Dependency rule:
    domain <- application <- infrastructure/presentation

V7.4 completes native application-port wiring with native Excel import
orchestration. Root-level V6 parser/persistence engines remain lazy
compatibility engines behind infrastructure, but no legacy application adapter
is left in the composition root.
"""

__version__ = "7.4"
ARCHITECTURE = "clean-architecture"
SERVICE_LAYER = "application-use-cases"
HTTP_API = "fastapi"
CLEAN_CORE = True
NATIVE_ADAPTERS = (
    "sessions",
    "project-access",
    "files",
    "jobs",
    "search",
    "ai",
    "excel",
)
LEGACY_ADAPTERS = ()
