from __future__ import annotations

import functools
from typing import Any


PATCH_MARKER = "V6.22 IPC CLAIM FAST PATH V1"


def _cell(values: tuple[Any, ...], index: int, default: Any = "") -> Any:
    if index < 0 or index >= len(values):
        return default
    value = values[index]
    return default if value is None else value


def _fast_parse_gtht(ws) -> list[dict[str, Any]]:
    """Parse GTHT in one sequential pass.

    openpyxl ReadOnlyWorksheet random access (ws['A123']) re-scans XML and becomes
    effectively O(rows²) when repeated thousands of times. The legacy IPC parser
    did ~30 random reads for every GTHT row, which can take tens of minutes.
    This implementation streams A:AD once and keeps the same normalized output.
    """
    if ws is None:
        return []

    import ipc_claim_v622 as ipc

    max_row = int(ws.max_row or 0)
    if max_row <= 0:
        return []

    header_row = None
    for row_no, values in enumerate(
        ws.iter_rows(min_row=1, max_row=min(max_row, 45), min_col=1, max_col=2, values_only=True),
        start=1,
    ):
        if "ten cong tac" in ipc._norm(_cell(tuple(values), 1)):
            header_row = row_no
            break
    if header_row is None:
        return []

    start_row = header_row + 4
    out: list[dict[str, Any]] = []
    for row_no, raw_values in enumerate(
        ws.iter_rows(min_row=start_row, max_row=max_row, min_col=1, max_col=30, values_only=True),
        start=start_row,
    ):
        values = tuple(raw_values)
        description = str(_cell(values, 1) or "").strip()  # B
        if not description:
            continue
        if ipc._is_total_label(description):
            if "tong cong" in ipc._norm(description):
                break
            continue

        num = ipc._to_number
        seq = num(_cell(values, 0))
        contract_qty = num(_cell(values, 2))
        material_current_qty = num(_cell(values, 12)) or 0
        material_cumulative_qty = num(_cell(values, 13)) or 0
        installation_current_pct = num(_cell(values, 15)) or 0
        installation_cumulative_pct = num(_cell(values, 16)) or 0
        material_current_value = num(_cell(values, 18)) or 0
        material_cumulative_value = num(_cell(values, 19)) or 0
        installation_current_value = num(_cell(values, 21)) or 0
        installation_cumulative_value = num(_cell(values, 22)) or 0
        deduction_current = num(_cell(values, 24)) or 0
        deduction_cumulative = num(_cell(values, 25)) or 0

        is_detail = bool((seq is not None and seq > 0) or (contract_qty is not None and contract_qty > 0))
        if not is_detail:
            continue

        out.append({
            "sheet_name": ws.title,
            "row_no": row_no,
            "seq": seq,
            "boq_item": description,
            "contract_qty": float(contract_qty or 0),
            "unit": str(_cell(values, 3) or "").strip(),
            "spec": str(_cell(values, 4) or "").strip(),
            "item_code": str(_cell(values, 5) or "").strip(),
            "brand": str(_cell(values, 6) or "").strip(),
            "origin": str(_cell(values, 7) or "").strip(),
            "material_unit_price": float(num(_cell(values, 8)) or 0),
            "labor_unit_price": float(num(_cell(values, 9)) or 0),
            "contract_amount": float(num(_cell(values, 10)) or 0),
            "material_previous_qty": float(num(_cell(values, 11)) or 0),
            "material_current_qty": float(material_current_qty),
            "material_cumulative_qty": float(material_cumulative_qty),
            "installation_previous_pct": float(num(_cell(values, 14)) or 0),
            "installation_current_pct": float(installation_current_pct),
            "installation_cumulative_pct": float(installation_cumulative_pct),
            "material_previous_value": float(num(_cell(values, 17)) or 0),
            "material_current_value": float(material_current_value),
            "material_cumulative_value": float(material_cumulative_value),
            "installation_previous_value": float(num(_cell(values, 20)) or 0),
            "installation_current_value": float(installation_current_value),
            "installation_cumulative_value": float(installation_cumulative_value),
            "deduction_previous": float(num(_cell(values, 23)) or 0),
            "deduction_current": float(deduction_current),
            "deduction_cumulative": float(deduction_cumulative),
            "current_value": float(material_current_value + installation_current_value - deduction_current),
            "cumulative_value": float(material_cumulative_value + installation_cumulative_value - deduction_cumulative),
            "completion_ratio": float(num(_cell(values, 26)) or 0),
            "note": str(_cell(values, 27) or "").strip(),
            "cost_code": str(_cell(values, 28) or "").strip(),
            "system": str(_cell(values, 29) or "").strip(),
        })
    return out


def install_ipc_claim_fast_path() -> None:
    import ipc_claim_v622 as ipc

    if getattr(ipc, "_qlda_ipc_fast_path_installed", False):
        return

    ipc._parse_gtht = _fast_parse_gtht
    original_parse = ipc.parse_ipc_workbook

    # Cache the parsed workbook by its bytes+filename across Streamlit reruns.
    # Clicking tabs/forms no longer reparses the same 2-100 MB Excel file.
    try:
        import streamlit as st

        @st.cache_data(show_spinner=False, max_entries=4, ttl=3600)
        def cached_parse(data: bytes, filename: str = "IPC.xlsx"):
            return original_parse(data, filename)
    except Exception:
        @functools.lru_cache(maxsize=4)
        def cached_parse(data: bytes, filename: str = "IPC.xlsx"):
            return original_parse(data, filename)

    ipc.parse_ipc_workbook = cached_parse
    ipc._qlda_ipc_fast_path_installed = True
    ipc._qlda_ipc_fast_path_marker = PATCH_MARKER
