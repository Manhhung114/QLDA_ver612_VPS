from __future__ import annotations

"""QLDA V7.6 application composition root.

Application ports are wired to native infrastructure adapters. Database creation
belongs to the infrastructure adapters that need it; the composition root no
longer exposes a legacy/compatibility database escape hatch.
"""

from functools import lru_cache

from qlda.application import (
    AIUseCases,
    ApplicationServices,
    ExcelImportUseCases,
    FileUseCases,
    JobUseCases,
    ProjectAccessUseCases,
    SearchUseCases,
    SessionUseCases,
)


@lru_cache(maxsize=1)
def get_application() -> ApplicationServices:
    from qlda.infrastructure.native_ai import NativeAIAdapter
    from qlda.infrastructure.native_excel import NativeExcelImportAdapter
    from qlda.infrastructure.native_files import NativeFileAdapter
    from qlda.infrastructure.native_jobs import NativeJobAdapter
    from qlda.infrastructure.native_project_access import NativeProjectAccessAdapter
    from qlda.infrastructure.native_search import NativeSearchAdapter
    from qlda.infrastructure.native_session import NativeSessionAdapter

    return ApplicationServices(
        sessions=SessionUseCases(NativeSessionAdapter()),
        access=ProjectAccessUseCases(NativeProjectAccessAdapter()),
        files=FileUseCases(NativeFileAdapter()),
        jobs=JobUseCases(NativeJobAdapter()),
        ai=AIUseCases(NativeAIAdapter()),
        search=SearchUseCases(NativeSearchAdapter()),
        excel=ExcelImportUseCases(NativeExcelImportAdapter()),
    )


def reset_application() -> None:
    get_application.cache_clear()
