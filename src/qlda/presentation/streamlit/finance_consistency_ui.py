from __future__ import annotations

"""Presentation integration for consolidated finance consistency policy."""

from qlda.runtime_core import finance_consistency as policy


def install_finance_consistency_ui() -> None:
    policy.install_finance_consistency_core()
    import qlda.runtime_core.project_cost_management as pcm

    if not getattr(pcm, "_qlda_finance_consistency_v23_budget_ui", False):
        pcm.render_budget_baseline = policy._render_budget_baseline_simplified
        pcm._qlda_finance_consistency_v23_budget_ui = True
    policy._install_legacy_bac_metric()
    policy._install_overview_adjusted_budget_metric()


__all__ = ["install_finance_consistency_ui"]
