"""Compatibility import for the native autonomy runtime.

New code must import :mod:`qlda.autonomy.runtime`. This module stays temporarily
so existing UI/worker/API imports keep working during the strangler migration.
"""

from qlda.autonomy.runtime import (
    SUPERVISOR_SCHEMA_VERSION,
    get_advanced_automation,
    get_autonomy_platform,
    get_autonomy_repository,
    run_daily_supervisor_if_due,
    run_project_supervisor,
)

__all__ = [
    "SUPERVISOR_SCHEMA_VERSION",
    "get_autonomy_platform",
    "get_autonomy_repository",
    "get_advanced_automation",
    "run_project_supervisor",
    "run_daily_supervisor_if_due",
]
