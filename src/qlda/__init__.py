from __future__ import annotations

"""QLDA V7.5 clean-architecture package.

Dependency rule:
    domain <- application <- infrastructure/presentation

V7.5 packages the BOQ/IPC/VO/Schedule import engines inside ``src/qlda`` and
retires the V6.25/V6.26 service/module facades from the production worker path.
All application ports remain native. Proven V6.22 semantic helpers are retained
only below the import-engine boundary for Streamlit/AI compatibility.
"""

__version__ = "7.5"
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
IMPORT_ENGINE_LAYER = "native-packaged"
