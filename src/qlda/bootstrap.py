from __future__ import annotations

"""QLDA V7.1 composition root.

Native infrastructure now owns identity/session, local files, durable Excel jobs
and project search. The remaining V6.x anti-corruption adapters are limited to
project access policy, AI context and Excel import pipelines.
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
    from qlda.infrastructure.legacy_adapters import (
        LegacyAIAdapter,
        LegacyExcelImportAdapter,
        LegacyProjectAccessAdapter,
    )
    from qlda.infrastructure.native_files import NativeFileAdapter
    from qlda.infrastructure.native_jobs import NativeJobAdapter
    from qlda.infrastructure.native_search import NativeSearchAdapter
    from qlda.infrastructure.native_session import NativeSessionAdapter

    return ApplicationServices(
        sessions=SessionUseCases(NativeSessionAdapter()),
        access=ProjectAccessUseCases(LegacyProjectAccessAdapter()),
        files=FileUseCases(NativeFileAdapter()),
        jobs=JobUseCases(NativeJobAdapter()),
        ai=AIUseCases(LegacyAIAdapter()),
        search=SearchUseCases(NativeSearchAdapter()),
        excel=ExcelImportUseCases(LegacyExcelImportAdapter()),
    )


def reset_application() -> None:
    """Clear the singleton composition; useful for tests/reconfiguration."""
    get_application.cache_clear()


def get_database():
    """Compatibility escape hatch owned by the composition root, not use cases."""
    from qlda.infrastructure.database import make_database

    return make_database()
