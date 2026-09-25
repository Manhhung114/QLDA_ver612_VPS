from __future__ import annotations

"""V7.6+ packaged runtime composition.

Runtime composition is explicit and deterministic:
- importing ``qlda.runtime_core`` has no side effects;
- database adapters that need module arguments are wired here;
- no-argument compatibility features are declared in
  ``qlda.composition.runtime_features`` and installed by stage;
- each stage is idempotent and protected by one composition lock.

This is the compatibility boundary while legacy behavior is migrated into native
Domain/Application/Infrastructure/Presentation modules.  New business features
must not be wired by wrapping these initializer functions.
"""

from threading import RLock

from qlda.composition.runtime_features import RuntimeStage, install_stage

_LOCK = RLock()
_DB_READY = False
_BUSINESS_READY = False
_UI_READY = False


def initialize_database_runtime() -> None:
    """Initialize persistence plus data-ingestion semantics used by all processes."""
    global _DB_READY
    if _DB_READY:
        return
    with _LOCK:
        if _DB_READY:
            return

        # These two adapters require the project_database module as an argument,
        # therefore they remain explicit instead of living in the generic registry.
        import qlda.runtime_core.project_database as project_database
        from qlda.runtime_core.performance_postgres_v1 import install_performance_postgres_v1
        from qlda.runtime_core.vps_postgres_resilience import install_vps_postgres_resilience

        install_vps_postgres_resilience(project_database)
        project_database.install_postgres_backend()
        install_performance_postgres_v1(project_database)

        # Worker processes need Google/Contractor Data Hub parsing semantics even
        # when they never initialize the full business or Streamlit runtime.
        install_stage(RuntimeStage.DATA)
        _DB_READY = True


def initialize_business_runtime() -> None:
    global _BUSINESS_READY
    if _BUSINESS_READY:
        return
    with _LOCK:
        if _BUSINESS_READY:
            return
        initialize_database_runtime()
        install_stage(RuntimeStage.BUSINESS)
        _BUSINESS_READY = True




def initialize_runtime() -> None:
    """Initialize the full Streamlit runtime without import-time monkey-patching."""
    global _UI_READY
    if _UI_READY:
        return
    with _LOCK:
        if _UI_READY:
            return

        from qlda.runtime_core.streamlit_secrets import apply_streamlit_secrets_to_env

        apply_streamlit_secrets_to_env()
        initialize_business_runtime()
        install_stage(RuntimeStage.UI)
        _UI_READY = True


__all__ = [
    "initialize_business_runtime",
    "initialize_database_runtime",
    "initialize_runtime",
]
