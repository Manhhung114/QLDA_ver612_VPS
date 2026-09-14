from __future__ import annotations

"""QLDA V7.6 clean-architecture package.

V7.6 closes the legacy-to-native conversion. Production Streamlit, FastAPI,
Excel worker, AI, project access, files, jobs, search and import engines run from
``src/qlda`` only. Historical V6 implementation labels are no longer part of the
production runtime contract.
"""

__version__ = "7.6"
ARCHITECTURE = "clean-architecture"
SERVICE_LAYER = "application-use-cases"
HTTP_API = "fastapi"
CLEAN_CORE = True
LEGACY_RUNTIME = False
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
STREAMLIT_ENTRYPOINT = "qlda.presentation.streamlit.app"
