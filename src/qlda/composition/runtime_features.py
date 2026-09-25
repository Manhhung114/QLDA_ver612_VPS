from __future__ import annotations

"""Deterministic runtime feature composition.

Historically QLDA accumulated behavior by importing ``runtime_core`` and then
wrapping/bootstrap-patching functions from ``runtime_core.__init__``. That made
import order part of the application contract and made production failures hard
to diagnose.

This module turns that hidden behavior into an explicit, inspectable composition
root. Every compatibility installer is named, ordered and attached to one stage.
Importing this module has no side effects; callers must invoke ``install_stage``.

The compatibility installers remain idempotent while native replacements are
migrated into domain/application/presentation packages. New product features
must not be added here as ad-hoc ``*_fix``/``*_patch`` modules; the architecture
guard in CI enforces that rule.
"""

from dataclasses import dataclass
from enum import Enum
from importlib import import_module
from threading import RLock
from typing import Callable


class RuntimeStage(str, Enum):
    DATA = "data"
    BUSINESS = "business"
    AI = "ai"
    UI = "ui"


@dataclass(frozen=True, slots=True)
class RuntimeFeature:
    name: str
    stage: RuntimeStage
    module: str
    installer: str

    def resolve(self) -> Callable[[], object]:
        fn = getattr(import_module(self.module), self.installer)
        if not callable(fn):
            raise TypeError(f"Runtime feature {self.name!r} installer is not callable")
        return fn


FEATURES: tuple[RuntimeFeature, ...] = (
    RuntimeFeature("contractor-data-complete-rows", RuntimeStage.DATA, "qlda.runtime_core.contractor_data_complete_rows", "install_contractor_data_complete_rows"),
    RuntimeFeature("contractor-data-source-semantics", RuntimeStage.DATA, "qlda.runtime_core.contractor_data_source_semantics", "install_contractor_data_source_semantics"),

    RuntimeFeature("runtime-optimizations", RuntimeStage.BUSINESS, "qlda.runtime_core.runtime_optimizations", "install_runtime"),
    RuntimeFeature("local-vps-runtime", RuntimeStage.BUSINESS, "qlda.runtime_core.local_runtime", "install_local_vps_runtime"),
    RuntimeFeature("runtime-settings-bridge", RuntimeStage.BUSINESS, "qlda.runtime_core.runtime_settings_bridge", "install_runtime_settings_bridge"),
    RuntimeFeature("single-session", RuntimeStage.BUSINESS, "qlda.runtime_core.single_session", "install_single_session"),
    RuntimeFeature("contractor-workspace", RuntimeStage.BUSINESS, "qlda.runtime_core.contractor_workspace", "install_contractor_workspace"),
    RuntimeFeature("contractor-sidebar-admin", RuntimeStage.BUSINESS, "qlda.runtime_core.contractor_sidebar_admin", "install_contractor_sidebar_admin"),
    RuntimeFeature("contractor-workspace-reset", RuntimeStage.BUSINESS, "qlda.runtime_core.contractor_workspace_reset", "install_contractor_workspace_reset"),
    RuntimeFeature("contractor-access-control", RuntimeStage.BUSINESS, "qlda.runtime_core.contractor_access_control", "install_contractor_access_control"),
    RuntimeFeature("default-workspace-admin-guard", RuntimeStage.BUSINESS, "qlda.runtime_core.default_workspace_admin_guard", "install_default_workspace_admin_guard"),
    RuntimeFeature("work-tasks", RuntimeStage.BUSINESS, "qlda.runtime_core.work_tasks_v1", "install_work_tasks_v1"),
    RuntimeFeature("work-task-email", RuntimeStage.BUSINESS, "qlda.runtime_core.work_task_email_notification", "install_work_task_email_notification"),
    RuntimeFeature("contractor-current-label", RuntimeStage.BUSINESS, "qlda.runtime_core.contractor_current_label", "install_contractor_current_label"),
    RuntimeFeature("work-task-delete", RuntimeStage.BUSINESS, "qlda.runtime_core.work_task_delete", "install_work_task_delete"),
    RuntimeFeature("contract-duration", RuntimeStage.BUSINESS, "qlda.runtime_core.contract_duration", "install_contract_duration_v622"),
    RuntimeFeature("boq-cost-components", RuntimeStage.BUSINESS, "qlda.runtime_core.boq_cost_components", "install_boq_cost_components"),
    RuntimeFeature("ipc-claim-fast", RuntimeStage.BUSINESS, "qlda.runtime_core.ipc_claim_fast", "install_ipc_claim_fast_path"),
    RuntimeFeature("ipc-claim-summary", RuntimeStage.BUSINESS, "qlda.runtime_core.ipc_claim_summary_fix", "install_ipc_claim_summary_fix"),
    RuntimeFeature("ipc-adaptive-parser", RuntimeStage.BUSINESS, "qlda.runtime_core.ipc_adaptive_parser", "install_ipc_adaptive_parser"),
    RuntimeFeature("ipc-payment-semantic", RuntimeStage.BUSINESS, "qlda.runtime_core.ipc_payment_semantic", "install_ipc_payment_semantic"),
    RuntimeFeature("multicore-excel", RuntimeStage.BUSINESS, "qlda.runtime_core.multicore_excel", "install_multicore_excel"),
    RuntimeFeature("ipc-claim-number", RuntimeStage.BUSINESS, "qlda.runtime_core.ipc_claim_number_fix", "install_ipc_claim_number_fix"),
    RuntimeFeature("boq-claim-terms", RuntimeStage.BUSINESS, "qlda.runtime_core.boq_claim_terms", "install_boq_claim_terms"),
    RuntimeFeature("boq-claim-price-recovery", RuntimeStage.BUSINESS, "qlda.runtime_core.boq_claim_price_recovery", "install_boq_claim_price_recovery"),
    RuntimeFeature("boq-claim-price-header-guard", RuntimeStage.BUSINESS, "qlda.runtime_core.boq_claim_price_header_guard", "install_boq_claim_price_header_guard"),

    RuntimeFeature("contractor-data-official-ai-pre", RuntimeStage.AI, "qlda.runtime_core.contractor_data_official_ai", "install_contractor_data_official_ai"),
    RuntimeFeature("contract-ai-large-pdf", RuntimeStage.AI, "qlda.runtime_core.contract_ai_large_pdf", "install_contract_ai_large_pdf_v622"),
    RuntimeFeature("gemini-resilience", RuntimeStage.AI, "qlda.runtime_core.gemini_resilience", "install_gemini_resilience"),
    RuntimeFeature("ai-live-context", RuntimeStage.AI, "qlda.runtime_core.ai_live_context", "install_ai_live_context"),
    RuntimeFeature("ai-claim-context", RuntimeStage.AI, "qlda.runtime_core.ai_claim_context", "install_ai_claim_context"),
    RuntimeFeature("ai-vo-context", RuntimeStage.AI, "qlda.runtime_core.ai_vo_context", "install_ai_vo_context"),
    RuntimeFeature("ai-streaming", RuntimeStage.AI, "qlda.runtime_core.ai_streaming", "install_ai_streaming"),
    RuntimeFeature("boq-ai-fullscan", RuntimeStage.AI, "qlda.runtime_core.boq_ai_fullscan", "install_boq_ai_fullscan"),
    RuntimeFeature("claim-component-fullscan", RuntimeStage.AI, "qlda.runtime_core.claim_component_fullscan", "install_claim_component_fullscan"),
    RuntimeFeature("claim-material-period-guard", RuntimeStage.AI, "qlda.runtime_core.claim_material_period_guard", "install_claim_material_period_guard"),
    RuntimeFeature("project-remaining-components", RuntimeStage.AI, "qlda.runtime_core.project_remaining_components", "install_project_remaining_components"),
    RuntimeFeature("contractor-ai-capture-scope", RuntimeStage.AI, "qlda.runtime_core.contractor_access_control", "capture_single_contractor_ai_context"),
    RuntimeFeature("contractor-ai-context", RuntimeStage.AI, "qlda.runtime_core.contractor_ai_context", "install_contractor_ai_context"),
    RuntimeFeature("contractor-ai-access-guard", RuntimeStage.AI, "qlda.runtime_core.contractor_access_control", "install_ai_access_guard"),
    RuntimeFeature("ai-vps-pdf-fullscan", RuntimeStage.AI, "qlda.runtime_core.ai_vps_pdf_fullscan", "install_ai_vps_pdf_fullscan"),
    RuntimeFeature("ai-vps-pdf-vision", RuntimeStage.AI, "qlda.runtime_core.ai_vps_pdf_vision", "install_ai_vps_pdf_vision"),
    RuntimeFeature("owner-material-ai-context", RuntimeStage.AI, "qlda.runtime_core.owner_material_ai_context", "install_owner_material_ai_context"),
    RuntimeFeature("cashflow-ai-context", RuntimeStage.AI, "qlda.runtime_core.cashflow_ai_context", "install_cashflow_ai_context"),
    RuntimeFeature("finance-consistency-core", RuntimeStage.AI, "qlda.runtime_core.finance_consistency", "install_finance_consistency_core"),
    RuntimeFeature("project-cost-ai-context", RuntimeStage.AI, "qlda.runtime_core.project_cost_ai_context", "install_project_cost_ai_context"),
    RuntimeFeature("contractor-data-shared-ai", RuntimeStage.AI, "qlda.runtime_core.contractor_data_shared_ai", "install_contractor_data_shared_ai_context"),
    RuntimeFeature("contractor-data-official-ai-post", RuntimeStage.AI, "qlda.runtime_core.contractor_data_official_ai", "install_contractor_data_official_ai"),

    RuntimeFeature("upload-ui-policy", RuntimeStage.UI, "qlda.runtime_core.upload_ui_200mb_policy", "install_upload_ui_200mb_policy"),
    RuntimeFeature("document-management-vps-ui", RuntimeStage.UI, "qlda.runtime_core.document_management_vps_ui", "install_document_management_vps_ui"),
    RuntimeFeature("attachment-upload-reopen", RuntimeStage.UI, "qlda.runtime_core.attachment_upload_reopen_fix", "install_attachment_upload_reopen_fix"),
    RuntimeFeature("owner-supplied-materials", RuntimeStage.UI, "qlda.runtime_core.owner_supplied_materials", "install_owner_supplied_material_erp"),
    RuntimeFeature("finance-title-policy", RuntimeStage.UI, "qlda.presentation.streamlit.finance_title_policy", "install_finance_title_policy"),
    RuntimeFeature("project-cost-management", RuntimeStage.UI, "qlda.runtime_core.project_cost_management", "install_project_cost_management"),
    RuntimeFeature("finance-consistency-ui", RuntimeStage.UI, "qlda.runtime_core.finance_consistency", "install_finance_consistency_ui"),
    RuntimeFeature("money-display-format", RuntimeStage.UI, "qlda.presentation.streamlit.money_display", "install_money_display_format"),
    RuntimeFeature("expander-default-collapsed", RuntimeStage.UI, "qlda.presentation.streamlit.expander_policy", "install_expanders_default_collapsed"),
    RuntimeFeature("document-selection-autopen", RuntimeStage.UI, "qlda.presentation.streamlit.document_selection_autopen", "install_document_selection_autopen"),
    RuntimeFeature("production-progress-overview", RuntimeStage.UI, "qlda.runtime_core.production_progress_overview_patch", "install_production_progress_overview_patch"),
    RuntimeFeature("production-progress-shared-ai", RuntimeStage.UI, "qlda.runtime_core.contractor_data_shared_ai", "install_production_progress_shared_ai_ui"),
    RuntimeFeature("multiselect-tag-style", RuntimeStage.UI, "qlda.presentation.streamlit.multiselect_tag_style", "install_multiselect_tag_style_patch"),
    RuntimeFeature("production-progress-source-exact", RuntimeStage.UI, "qlda.runtime_core.production_progress_source_exact", "install_production_progress_source_exact"),
    RuntimeFeature("autonomy-overview", RuntimeStage.UI, "qlda.presentation.streamlit.autonomy_overview", "install_autonomy_overview_patch"),
    RuntimeFeature("advanced-automation-ui", RuntimeStage.UI, "qlda.presentation.streamlit.advanced_automation", "install_advanced_automation_ui"),
    RuntimeFeature("ai-supervisor-navigation", RuntimeStage.UI, "qlda.presentation.streamlit.ai_supervisor_navigation", "install_ai_supervisor_navigation"),
    RuntimeFeature("contractor-data-admin-visibility", RuntimeStage.UI, "qlda.presentation.streamlit.contractor_data_admin_visibility", "install_contractor_data_admin_visibility"),
)

_LOCK = RLock()
_INSTALLED: list[str] = []
_INSTALLED_SET: set[str] = set()


def install_stage(stage: RuntimeStage | str) -> tuple[str, ...]:
    resolved = RuntimeStage(stage)
    installed_now: list[str] = []
    with _LOCK:
        for feature in FEATURES:
            if feature.stage is not resolved or feature.name in _INSTALLED_SET:
                continue
            feature.resolve()()
            _INSTALLED_SET.add(feature.name)
            _INSTALLED.append(feature.name)
            installed_now.append(feature.name)
    return tuple(installed_now)


def feature_status() -> dict[str, object]:
    with _LOCK:
        return {
            "declared": tuple(feature.name for feature in FEATURES),
            "installed": tuple(_INSTALLED),
            "pending": tuple(feature.name for feature in FEATURES if feature.name not in _INSTALLED_SET),
        }


__all__ = [
    "FEATURES",
    "RuntimeFeature",
    "RuntimeStage",
    "feature_status",
    "install_stage",
]
