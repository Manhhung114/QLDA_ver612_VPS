from __future__ import annotations

"""V7.6 packaged runtime composition.

Three idempotent initialization levels keep non-UI processes lean:
- database: PostgreSQL compatibility API + resilience/performance only;
- business/AI: proven workflow/import/AI semantics;
- runtime: Streamlit secrets/UI/storage integration.

All imports resolve inside ``src/qlda``. No repository-root loader or generated
source execution is used by these entrypoints.
"""

from threading import RLock

_LOCK = RLock()
_DB_READY = False
_BUSINESS_READY = False
_AI_READY = False
_UI_READY = False


def initialize_database_runtime() -> None:
    global _DB_READY
    if _DB_READY:
        return
    with _LOCK:
        if _DB_READY:
            return
        import qlda.runtime_core.project_database as project_database
        from qlda.runtime_core.vps_postgres_resilience import install_vps_postgres_resilience
        from qlda.runtime_core.performance_postgres_v1 import install_performance_postgres_v1

        install_vps_postgres_resilience(project_database)
        project_database.install_postgres_backend()
        install_performance_postgres_v1(project_database)
        _DB_READY = True


def initialize_business_runtime() -> None:
    global _BUSINESS_READY
    if _BUSINESS_READY:
        return
    with _LOCK:
        if _BUSINESS_READY:
            return
        initialize_database_runtime()

        from qlda.runtime_core.runtime_optimizations import install_runtime
        from qlda.runtime_core.local_runtime import install_local_vps_runtime
        install_runtime()
        install_local_vps_runtime()

        from qlda.runtime_core.runtime_settings_bridge import install_runtime_settings_bridge
        from qlda.runtime_core.single_session import install_single_session
        install_runtime_settings_bridge()
        install_single_session()

        from qlda.runtime_core.contractor_workspace import install_contractor_workspace
        from qlda.runtime_core.contractor_sidebar_admin import install_contractor_sidebar_admin
        from qlda.runtime_core.contractor_workspace_reset import install_contractor_workspace_reset
        from qlda.runtime_core.contractor_access_control import install_contractor_access_control
        from qlda.runtime_core.default_workspace_admin_guard import install_default_workspace_admin_guard
        install_contractor_workspace()
        install_contractor_sidebar_admin()
        install_contractor_workspace_reset()
        install_contractor_access_control()
        install_default_workspace_admin_guard()

        from qlda.runtime_core.work_tasks_v1 import install_work_tasks_v1
        from qlda.runtime_core.work_task_email_notification import install_work_task_email_notification
        from qlda.runtime_core.contractor_current_label import install_contractor_current_label
        from qlda.runtime_core.work_task_delete import install_work_task_delete
        from qlda.runtime_core.contract_duration import install_contract_duration_v622
        install_work_tasks_v1()
        install_work_task_email_notification()
        install_contractor_current_label()
        install_work_task_delete()
        install_contract_duration_v622()

        # Keep BOQ/IPC parser behavior identical for Streamlit and worker paths.
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

        _BUSINESS_READY = True


def initialize_ai_runtime() -> None:
    global _AI_READY
    if _AI_READY:
        return
    with _LOCK:
        if _AI_READY:
            return
        initialize_business_runtime()

        from qlda.runtime_core.contract_ai_large_pdf import install_contract_ai_large_pdf_v622
        from qlda.runtime_core.gemini_resilience import install_gemini_resilience
        from qlda.runtime_core.ai_live_context import install_ai_live_context
        from qlda.runtime_core.ai_claim_context import install_ai_claim_context
        from qlda.runtime_core.ai_vo_context import install_ai_vo_context
        from qlda.runtime_core.ai_streaming import install_ai_streaming
        install_contract_ai_large_pdf_v622()
        install_gemini_resilience()
        install_ai_live_context()
        install_ai_claim_context()
        install_ai_vo_context()
        install_ai_streaming()

        from qlda.runtime_core.boq_ai_fullscan import install_boq_ai_fullscan
        from qlda.runtime_core.claim_component_fullscan import install_claim_component_fullscan
        from qlda.runtime_core.claim_material_period_guard import install_claim_material_period_guard
        from qlda.runtime_core.project_remaining_components import install_project_remaining_components
        from qlda.runtime_core.contractor_ai_context import install_contractor_ai_context
        from qlda.runtime_core.contractor_access_control import (
            capture_single_contractor_ai_context,
            install_ai_access_guard,
        )
        install_boq_ai_fullscan()
        install_claim_component_fullscan()
        install_claim_material_period_guard()
        install_project_remaining_components()
        capture_single_contractor_ai_context()
        install_contractor_ai_context()
        install_ai_access_guard()

        # Shared assistant reads the final composed project context, searchable PDFs,
        # scanned PDFs, owner-supplied materials, unpaid IPC cash plan and project costs.
        from qlda.runtime_core.ai_vps_pdf_fullscan import install_ai_vps_pdf_fullscan
        from qlda.runtime_core.ai_vps_pdf_vision import install_ai_vps_pdf_vision
        from qlda.runtime_core.owner_material_ai_context import install_owner_material_ai_context
        from qlda.runtime_core.cashflow_ai_context import install_cashflow_ai_context
        from qlda.runtime_core.finance_consistency import install_finance_consistency_core
        from qlda.runtime_core.project_cost_ai_context import install_project_cost_ai_context
        install_ai_vps_pdf_fullscan()
        install_ai_vps_pdf_vision()
        install_owner_material_ai_context()
        install_cashflow_ai_context()
        # Cleanup V2.3: one core policy replaces the signed-adjustment, VO-value
        # and after-tax Project Cost patch chain.
        install_finance_consistency_core()
        install_project_cost_ai_context()

        # Contractor Data Hub no longer owns a separate AI. Its Google Sheet/Drive
        # records are injected into the same app-wide assistant context and keep
        # the existing contractor RBAC ContextVar boundary.
        from qlda.runtime_core.contractor_data_shared_ai import install_contractor_data_shared_ai_context
        install_contractor_data_shared_ai_context()
        _AI_READY = True


def initialize_runtime() -> None:
    """Full Streamlit runtime initialization."""
    global _UI_READY
    if _UI_READY:
        return
    with _LOCK:
        if _UI_READY:
            return
        from qlda.runtime_core.streamlit_secrets import apply_streamlit_secrets_to_env
        apply_streamlit_secrets_to_env()
        initialize_ai_runtime()

        from qlda.runtime_core.upload_ui_200mb_policy import install_upload_ui_200mb_policy
        install_upload_ui_200mb_policy()

        from qlda.runtime_core.document_management_vps_ui import install_document_management_vps_ui
        install_document_management_vps_ui()

        # Existing document/drawing forms keep the database handle in app globals.
        # Install the V4 reopen bridge after the VPS UI wrapper so attachment and
        # refresh clicks always resolve the selected record and create a fresh ticket.
        from qlda.runtime_core.attachment_upload_reopen_fix import install_attachment_upload_reopen_fix
        install_attachment_upload_reopen_fix()

        from qlda.runtime_core.owner_supplied_materials import install_owner_supplied_material_erp
        install_owner_supplied_material_erp()

        from qlda.runtime_core.finance_title_policy import install_finance_title_policy
        install_finance_title_policy()

        # Project Cost owns the six-sheet Finance navigation.
        from qlda.runtime_core.project_cost_management import install_project_cost_management
        install_project_cost_management()

        # Cleanup V2.3: a single UI consistency policy owns after-tax BAC,
        # simplified budget cards, executive adjusted budget and VO consistency.
        from qlda.runtime_core.finance_consistency import install_finance_consistency_ui
        install_finance_consistency_ui()

        from qlda.runtime_core.money_display_format import install_money_display_format
        install_money_display_format()

        from qlda.runtime_core.ui_expander_default_collapsed import install_expanders_default_collapsed
        install_expanders_default_collapsed()

        from qlda.runtime_core.document_selection_autopen import install_document_selection_autopen
        install_document_selection_autopen()

        # Production Data Hub overview must expose every synchronized Google
        # worksheet, even when a tab has raw rows but is not normalized yet.
        from qlda.runtime_core.production_progress_overview_patch import install_production_progress_overview_patch
        install_production_progress_overview_patch()

        # Remove the Data Hub's standalone AI tab. The common app assistant is the
        # single AI entry point and already receives this Data Hub's live context.
        from qlda.runtime_core.contractor_data_shared_ai import install_production_progress_shared_ai_ui
        install_production_progress_shared_ai_ui()

        _UI_READY = True
