from __future__ import annotations

"""QLDA V7.0 composition root.

Only this outer-layer module wires application use cases to infrastructure
adapters. Domain/application code remains independent from frameworks, DBs and
legacy compatibility modules.
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
        LegacyFileAdapter,
        LegacyJobAdapter,
        LegacyProjectAccessAdapter,
        LegacySearchAdapter,
        LegacySessionAdapter,
    )

    return ApplicationServices(
        sessions=SessionUseCases(LegacySessionAdapter()),
        access=ProjectAccessUseCases(LegacyProjectAccessAdapter()),
        files=FileUseCases(LegacyFileAdapter()),
        jobs=JobUseCases(LegacyJobAdapter()),
        ai=AIUseCases(LegacyAIAdapter()),
        search=SearchUseCases(LegacySearchAdapter()),
        excel=ExcelImportUseCases(LegacyExcelImportAdapter()),
    )


def reset_application() -> None:
    """Clear the singleton composition; mainly useful for tests/reconfiguration."""

    get_application.cache_clear()


def get_database():
    """Compatibility escape hatch owned by the composition root, not use cases."""

    from qlda.infrastructure.database import make_database

    return make_database()
