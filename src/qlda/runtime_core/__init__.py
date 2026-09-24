"""QLDA V7.6 packaged production runtime core."""

# Install the Contractor Data Hub completeness guard as soon as the packaged
# runtime_core namespace is imported. This keeps Streamlit and background sync
# workers on the same row-ingestion semantics without duplicating boot logic.
from qlda.runtime_core.contractor_data_complete_rows import install_contractor_data_complete_rows

install_contractor_data_complete_rows()

# The Data Hub UI is composed later by initialize_runtime(): first the all-sheet
# overview patch, then the shared-AI UI patch. Wrap the full runtime initializer
# so the Admin visibility policy is installed last and cannot be overwritten by
# those earlier UI composition steps.
from qlda.runtime_core import bootstrap as _bootstrap

if not getattr(_bootstrap, "_qlda_admin_visibility_bootstrap_v1", False):
    _original_initialize_runtime = _bootstrap.initialize_runtime

    def _initialize_runtime_with_admin_visibility() -> None:
        _original_initialize_runtime()
        from qlda.runtime_core.contractor_data_admin_visibility import (
            install_contractor_data_admin_visibility,
        )

        install_contractor_data_admin_visibility()

    _bootstrap.initialize_runtime = _initialize_runtime_with_admin_visibility
    _bootstrap._qlda_admin_visibility_bootstrap_v1 = True
