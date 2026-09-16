from __future__ import annotations

"""Compatibility facade for BOQ workbook snapshot persistence.

Cleanup V1 keeps one implementation only: ``qlda.import_engines.boq_snapshot``.
The Streamlit/runtime path historically imported this module, so the public API is
re-exported here instead of maintaining a second byte-for-byte copy.

This preserves all existing imports while making the packaged import engine the
single source of truth for persisted BOQ workbook snapshots.
"""

from qlda.import_engines.boq_snapshot import (
    PAYLOAD_PREFIX,
    TABLE_NAME,
    delete_saved_boq_workbook,
    format_table_number,
    load_saved_boq_workbook,
    save_saved_boq_workbook,
)

__all__ = [
    "TABLE_NAME",
    "PAYLOAD_PREFIX",
    "format_table_number",
    "save_saved_boq_workbook",
    "load_saved_boq_workbook",
    "delete_saved_boq_workbook",
]
