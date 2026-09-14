from __future__ import annotations

"""Deprecated V6 compatibility Service Layer.

V7.0 runtime entry points use :mod:`qlda.application` through
:mod:`qlda.bootstrap`. These classes remain available while legacy Streamlit and
module code is migrated behind infrastructure adapters.
"""

from qlda.services.access import ProjectAccessService, ProjectScope
from qlda.services.ai import AIApplicationError, AIService
from qlda.services.auth import AuthenticationError, AuthorizationError, SessionService
from qlda.services.boq import BOQService
from qlda.services.excel import ExcelImportService
from qlda.services.files import FileService
from qlda.services.ipc import IPCService
from qlda.services.jobs import JobService
from qlda.services.schedule import ScheduleService
from qlda.services.search import SearchService
from qlda.services.vo import VOService

__all__ = [
    "AuthenticationError", "AuthorizationError", "SessionService",
    "ProjectAccessService", "ProjectScope", "AIApplicationError", "AIService",
    "SearchService", "BOQService", "IPCService", "VOService", "ScheduleService",
    "ExcelImportService", "FileService", "JobService",
]
