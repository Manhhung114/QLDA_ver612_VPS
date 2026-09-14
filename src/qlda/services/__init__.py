from __future__ import annotations

"""Deprecated V6 compatibility Service Layer.

V7.2 runtime entry points use :mod:`qlda.application` through
:mod:`qlda.bootstrap`. Native session/file/job/search facades were retired in
V7.2; only business-heavy compatibility services still needed by AI/Excel and
legacy import processors remain here temporarily.
"""

from qlda.services.access import ProjectAccessService, ProjectScope
from qlda.services.ai import AIApplicationError, AIService
from qlda.services.boq import BOQService
from qlda.services.excel import ExcelImportService
from qlda.services.ipc import IPCService
from qlda.services.schedule import ScheduleService
from qlda.services.vo import VOService

__all__ = [
    "ProjectAccessService",
    "ProjectScope",
    "AIApplicationError",
    "AIService",
    "BOQService",
    "IPCService",
    "VOService",
    "ScheduleService",
    "ExcelImportService",
]
