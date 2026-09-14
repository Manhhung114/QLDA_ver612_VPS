from __future__ import annotations

"""V7.6 packaged runtime composition.

This preserves proven production semantics while eliminating repository-root
imports, source reconstruction and versioned module names from the production
entrypoints. It is intentionally idempotent because Streamlit reruns modules.
"""

from threading import RLock

_LOCK = RLock()
_READY = False


def initialize_runtime() -> None:
    global _READY
    if _READY:
        return
    with _LOCK:
        if _READY:
            return

        from qlda.runtime_core.streamlit_secrets import apply_streamlit_secrets_to_env
        apply_streamlit_secrets_to_env()

        import qlda.runtime_core.project_database as project_database
        from qlda.runtime_core.vps_postgres_resilience import install_vps_postgres_resilience
        from qlda.runtime_core.performance_postgres_v1 import install_performance_postgres_v1
        install_vps_postgres_resilience(project_database)
        project_database.install_postgres_backend()
        install_performance_postgres_v1(project_database)

        from qlda.runtime_core.runtime_settings_bridge import install_runtime_settings_bridge
        from qlda.runtime_core.single_session import install_single_session
        install_runtime_settings_bridge()
        install_single_session()

        from qlda.runtime_core.contractor_workspace import install_contractor_workspace
        from qlda.runtime_core.contractor_sidebar_admin import install_contractor_sidebar_admin
        from qlda.runtime_core.contractor_workspace_reset import install_contractor_workspace_reset
        from qlda.runtime_core.contractor_access_control import install_contractor_access_control, capture_single_contractor_ai_context, install_ai_access_guard
        from qlda.runtime_core.default_workspace_admin_guard import install_default_workspace_admin_guard
        install_contractor_workspace()
        install_contractor_sidebar_admin()
        install_contractor_workspace_reset()
        install_contractor_access_control()
        install_default_workspace_admin_guard()

        from qlda.runtime_core.work_tasks_v1 import install_work_tasks_v1
        from qlda.runtime_core.contract_duration import install_contract_duration_v622
        from qlda.runtime_core.contract_ai_large_pdf import install_contract_ai_large_pdf_v622
        install_work_tasks_v1()
        install_contract_duration_v622()
        install_contract_ai_large_pdf_v622()

        from qlda.runtime_core.gemini_resilience import install_gemini_resilience
        from qlda.runtime_core.ai_live_context import install_ai_live_context
        from qlda.runtime_core.ai_claim_context import install_ai_claim_context
        from qlda.runtime_core.ai_vo_context import install_ai_vo_context
        install_gemini_resilience()
        install_ai_live_context()
        install_ai_claim_context()
        install_ai_vo_context()

        from qlda.runtime_core.boq_cost_components import install_boq_cost_components
        from qlda.runtime_core.ipc_claim_fast import install_ipc_claim_fast_path
        from qlda.runtime_core.ipc_claim_summary_fix import install_ipc_claim_summary_fix
        from qlda.runtime_core.ipc_adaptive_parser import install_ipc_adaptive_parser
        from qlda.runtime_core.ipc_payment_semantic import install_ipc_payment_semantic
        from qlda.runtime_core.multicore_excel import install_multicore_excel
        from qlda.runtime_core.ipc_claim_number_fix import install_ipc_claim_number_fix
        from qlda.runtime_core.boq_claim_terms import install_boq_claim_terms
        from qlda.runtime_core.boq_claim_price_recovery import install_boq_claim_price_recovery
        from qlda.runtime_core.boq_claim_price_header_guard import install_boq_claim_price_header_guard
        install_boq_cost_components()
        install_ipc_claim_fast_path()
        install_ipc_claim_summary_fix()
        install_ipc_adaptive_parser()
        install_ipc_payment_semantic()
        install_multicore_excel()
        install_ipc_claim_number_fix()
        install_boq_claim_terms()
        install_boq_claim_price_recovery()
        install_boq_claim_price_header_guard()

        from qlda.runtime_core.boq_ai_fullscan import install_boq_ai_fullscan
        from qlda.runtime_core.claim_component_fullscan import install_claim_component_fullscan
        from qlda.runtime_core.claim_material_period_guard import install_claim_material_period_guard
        from qlda.runtime_core.project_remaining_components import install_project_remaining_components
        from qlda.runtime_core.contractor_ai_context import install_contractor_ai_context
        install_boq_ai_fullscan()
        install_claim_component_fullscan()
        install_claim_material_period_guard()
        install_project_remaining_components()
        capture_single_contractor_ai_context()
        install_contractor_ai_context()
        install_ai_access_guard()

        _READY = True
