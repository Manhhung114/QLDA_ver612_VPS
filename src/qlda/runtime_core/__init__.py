"""QLDA V7.6 packaged production runtime core."""

# Install Data Hub ingestion semantics as soon as runtime_core is imported so
# Streamlit and background workers parse Google Sheets identically.
from qlda.runtime_core.contractor_data_complete_rows import install_contractor_data_complete_rows
from qlda.runtime_core.contractor_data_source_semantics import install_contractor_data_source_semantics

install_contractor_data_complete_rows()
install_contractor_data_source_semantics()

# Compose late UI/AI policies around bootstrap without duplicating its large
# initializer. Order matters:
# 1) bootstrap installs the base shared-AI + all-worksheet UI;
# 2) source-exact renderer replaces the old mean-based pivot;
# 3) autonomy appends its contractor-isolated project-supervisor control center;
# 4) V9.1→V9.6 Admin UI appends advanced automation controls;
# 5) Admin visibility installs last so Data Hub management/warnings stay Admin-only.
from qlda.runtime_core import bootstrap as _bootstrap

if not getattr(_bootstrap, "_qlda_source_exact_bootstrap_v2", False):
    _original_initialize_ai_runtime = _bootstrap.initialize_ai_runtime
    _original_initialize_runtime = _bootstrap.initialize_runtime

    def _initialize_ai_runtime_with_official_totals() -> None:
        # Replace the Data Hub appendix function before bootstrap installs the
        # shared ProjectContextBuilder wrapper. Reinstall afterward as a guard in
        # case import order changes in a future release.
        from qlda.runtime_core.contractor_data_official_ai import install_contractor_data_official_ai

        install_contractor_data_official_ai()
        _original_initialize_ai_runtime()
        install_contractor_data_official_ai()

    def _initialize_runtime_with_source_exact_ui() -> None:
        _original_initialize_runtime()

        from qlda.runtime_core.production_progress_source_exact import (
            install_production_progress_source_exact,
        )
        from qlda.runtime_core.autonomy_overview_patch import install_autonomy_overview_patch
        from qlda.runtime_core.advanced_automation_ui import install_advanced_automation_ui
        from qlda.runtime_core.contractor_data_admin_visibility import (
            install_contractor_data_admin_visibility,
        )

        install_production_progress_source_exact()
        install_autonomy_overview_patch()
        install_advanced_automation_ui()
        install_contractor_data_admin_visibility()

    _bootstrap.initialize_ai_runtime = _initialize_ai_runtime_with_official_totals
    _bootstrap.initialize_runtime = _initialize_runtime_with_source_exact_ui
    _bootstrap._qlda_source_exact_bootstrap_v2 = True
    _bootstrap._qlda_admin_visibility_bootstrap_v1 = True
    _bootstrap._qlda_autonomy_overview_bootstrap_v1 = True
    _bootstrap._qlda_advanced_automation_ui_v1 = True
