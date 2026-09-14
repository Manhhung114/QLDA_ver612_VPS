from __future__ import annotations

"""QLDA V7.0 clean-architecture package.

Dependency rule:
    domain <- application <- infrastructure/presentation

The composition root in :mod:`qlda.bootstrap` wires V7 use cases to the proven
V6.x compatibility runtime while legacy code is progressively retired.
"""

__version__ = "7.0"
ARCHITECTURE = "clean-architecture"
SERVICE_LAYER = "application-use-cases"
HTTP_API = "fastapi"
CLEAN_CORE = True
