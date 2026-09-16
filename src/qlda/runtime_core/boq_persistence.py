from __future__ import annotations

"""Compatibility facade for BOQ workbook snapshot persistence.

Cleanup V1 keeps one implementation only: ``qlda.import_engines.boq_snapshot``.
The Streamlit/runtime path historically imported this module, so the API is
re-exported here instead of maintaining a second byte-for-byte copy.

Some regression helpers historically used the private encode/decode functions
from this module.  They are intentionally re-exported as aliases as well so the
facade preserves behavior while the packaged import engine remains the single
implementation.
"""

from qlda.import_engines.boq_snapshot import (
    PAYLOAD_PREFIX,
    TABLE_NAME,
    _decode_result,
    _encode_result,
    _ensure_table,
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
    "_ensure_table",
    "_encode_result",
    "_decode_result",
]
