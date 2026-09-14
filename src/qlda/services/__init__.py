from __future__ import annotations

"""Deprecated V6 compatibility Service Layer.

V7.4 runtime entry points use :mod:`qlda.application` through
:mod:`qlda.bootstrap`, and every application port now has a native
infrastructure adapter. The classes exported here remain only for older callers
and regression compatibility; they are not part of the V7 runtime composition.
"""

from qlda.services.boq import BOQService
from qlda.services.excel import ExcelImportService
from qlda.services.ipc import IPCService
from qlda.services.schedule import ScheduleService
from qlda.services.vo import VOService

__all__ = [
    "BOQService",
    "IPCService",
    "VOService",
    "ScheduleService",
    "ExcelImportService",
]
