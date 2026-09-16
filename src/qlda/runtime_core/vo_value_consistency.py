"""Compatibility facade for the consolidated VO single-source policy."""
from qlda.runtime_core.finance_consistency import (
    PATCH_MARKER,
    effective_proposed_value,
    normalize_project_vo_values,
    install_finance_consistency_core as install_vo_value_consistency,
)

__all__ = [
    "PATCH_MARKER",
    "effective_proposed_value",
    "normalize_project_vo_values",
    "install_vo_value_consistency",
]
