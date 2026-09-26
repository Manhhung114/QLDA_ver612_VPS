from __future__ import annotations

"""Packaged runtime composition.

Runtime composition is explicit and deterministic. AI provider execution is no
longer a ``runtime_core`` stage: native application/infrastructure boundaries own
context, retrieval, provider calls, tool calling, telemetry and evaluation.
``initialize_ai_runtime`` is retained only as a backwards-compatible bootstrap
alias for callers that still initialize business services before invoking AI.
"""

from threading import RLock

from qlda.composition.runtime_features import RuntimeStage, install_stage

_LOCK = RLock()
_DB_READY = False
_BUSINESS_READY = False
_AI_READY = False
_UI_READY = False


def initialize_database_runtime() -> None:
    """Initialize persistence plus data-ingestion semantics used by all processes."""
    global _DB_READY
    if _DB_READY:
        return
    with _LOCK:
        if _DB_READY:
            return

        import qlda.runtime_core.project_database as project_database
        from qlda.runtime_core.performance_postgres_v1 import install_performance_postgres_v1
        from qlda.runtime_core.vps_postgres_resilience import install_vps_postgres_resilience

        install_vps_postgres_resilience(project_database)
        project_database.install_postgres_backend()
        install_performance_postgres_v1(project_database)
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


def initialize_ai_runtime() -> None:
    """Compatibility alias; AI no longer installs a ``runtime_core`` stage."""
    global _AI_READY
    if _AI_READY:
        return
    with _LOCK:
        if _AI_READY:
            return
        initialize_business_runtime()
        _AI_READY = True


def initialize_runtime() -> None:
    """Initialize database/business/UI compatibility without a legacy AI stage."""
    global _UI_READY
    if _UI_READY:
        return
    with _LOCK:
        if _UI_READY:
            return

        from qlda.runtime_core.streamlit_secrets import apply_streamlit_secrets_to_env
        from qlda.presentation.streamlit.credit_branding import install_credit_branding_v7
        from qlda.presentation.streamlit.legacy_ai_streaming_contract import (
            install_legacy_ai_streaming_contract,
        )

        apply_streamlit_secrets_to_env()
        initialize_business_runtime()
        install_stage(RuntimeStage.UI)
        # Apply the credit treatment after the base V7 theme so this small
        # presentation override wins the CSS cascade on desktop and mobile.
        install_credit_branding_v7()
        # The source-controlled Streamlit shell still calls ask_project_stream on
        # the legacy assistant classes. Restore only this presentation contract;
        # provider execution remains outside RuntimeStage composition.
        install_legacy_ai_streaming_contract()
        _UI_READY = True


__all__ = [
    "initialize_ai_runtime",
    "initialize_business_runtime",
    "initialize_database_runtime",
    "initialize_runtime",
]
