"""Compatibility facade; signed finance logic is consolidated in finance_consistency."""
from qlda.runtime_core.finance_consistency import (
    install_finance_consistency_core as install_project_cost_signed_adjustments,
)

__all__ = ["install_project_cost_signed_adjustments"]
