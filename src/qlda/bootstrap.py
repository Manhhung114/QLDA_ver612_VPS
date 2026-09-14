from __future__ import annotations

"""QLDA V7.4 composition root.

All application ports are now wired to native V7 infrastructure adapters.
Excel import orchestration is native as of V7.4; proven V6 parser/persistence
engines remain lazy compatibility engines behind the native infrastructure
boundary while they are migrated independently.
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
    """Clear the singleton composition; useful for tests/reconfiguration."""
    get_application.cache_clear()


def get_database():
    """Compatibility escape hatch owned by the composition root, not use cases."""
    from qlda.infrastructure.database import make_database

    return make_database()
