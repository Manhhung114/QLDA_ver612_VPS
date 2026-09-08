from __future__ import annotations

import contextvars
import re
from pathlib import Path
from typing import Any


PATCH_VERSION = "V6.22 IPC CLAIM NUMBER V3"

# The base IPC parser validates Claim identity before outer parse wrappers can
# post-process the result. Keep a per-call filename hint so adaptive/legacy
# metadata readers can supply a Claim number early without process-global mutable
# state (important when several Streamlit sessions parse concurrently).
_FILENAME_CLAIM_HINT: contextvars.ContextVar[str] = contextvars.ContextVar(
    "qlda_ipc_filename_claim_hint", default=""
)


def _clean_claim_no(value: Any) -> str:
    """Normalized number for comparisons only; display/storage may keep 01/02/etc."""
    text = str(value or "").strip()
    if not text:
        return ""
    match = re.search(r"\d+", text)
    if not match:
        return text
    try:
        return str(int(match.group(0)))
    except Exception:
        return match.group(0).lstrip("0") or "0"


def claim_no_from_filename(filename: str) -> str:
    """Extract the explicit IPC/Claim number from a filename.

    Examples:
      (SME213) IPC#6 (11102025).xlsx -> 6
      IPC 03 Final.xlsx             -> 3
      IPC03.xlsx                    -> 3
      Claim-12 Rev1.xlsm            -> 12

    Revision/date numbers elsewhere in the filename are deliberately ignored.
    """
    name = Path(str(filename or "")).stem
    patterns = (
        r"(?i)(?:^|[^A-Z0-9])IPC\s*(?:#|NO\.?|NUMBER|[-_])?\s*0*(\d{1,4})(?:\D|$)",
        r"(?i)(?:^|[^A-Z0-9])CLAIM\s*(?:#|NO\.?|NUMBER|[-_])?\s*0*(\d{1,4})(?:\D|$)",
    )
    for pattern in patterns:
        match = re.search(pattern, name)
        if match:
            return str(int(match.group(1)))
    return ""


def _apply_filename_identity(result: dict[str, Any], filename: str) -> dict[str, Any]:
    parsed = dict(result or {})
    file_no = claim_no_from_filename(filename)
    if not file_no:
        return parsed

    metadata = dict(parsed.get("metadata") or {})
    raw_internal = str(parsed.get("claim_no") or "").strip()
    raw_metadata = str(metadata.get("claim_no") or "").strip()
    source_raw = raw_internal or raw_metadata
    source_no = _clean_claim_no(source_raw)

    # If the workbook and filename identify the same Claim, preserve the workbook
    # formatting (e.g. 01, 04). This keeps existing DB keys/revision/period sync
    # compatible. Only a true numeric mismatch is overridden by the filename.
    if source_no and source_no == file_no:
        target_raw = source_raw
        target_no = source_no
        source_kind = "filename_confirmed"
    else:
        target_raw = file_no
        target_no = file_no
        source_kind = "filename_override"

    parsed["claim_no"] = target_raw
    parsed["claim_code"] = f"IPC-{target_no.zfill(2)}"
    metadata["claim_no"] = target_raw
    parsed["metadata"] = metadata
    parsed["claim_number_source"] = source_kind
    parsed["claim_number_internal"] = source_raw

    warnings = list(parsed.get("warnings") or [])
    if source_no and source_no != file_no:
        warning = (
            f"CẢNH BÁO SỐ CLAIM: file Excel bên trong đang khai báo Claim {source_raw}, "
            f"nhưng tên file ghi IPC#{file_no}. Hệ thống ưu tiên tên file và sẽ lưu vào IPC-{file_no.zfill(2)}. "
            "Hãy kiểm tra số Claim trước khi bấm Lưu."
        )
        if warning not in warnings:
            warnings.insert(0, warning)
    parsed["warnings"] = warnings
    return parsed


def install_ipc_claim_number_fix() -> None:
    """Use explicit IPC/Claim number in filename as the final save target.

    IPC workbooks are commonly copied from the previous period and the declaration
    cell may still contain the old Claim number. An explicit filename such as
    'IPC#6 ...xlsx' is therefore used to prevent accidentally overwriting IPC-05.

    V3 also supplies the filename number *during* base parsing when an adaptive
    form has no semantic Claim label, while preserving legacy zero-padded values
    such as 01/04 whenever the workbook and filename agree numerically.
    """
    import ipc_claim_v622 as ipc

    if getattr(ipc, "_qlda_ipc_claim_number_fix_installed", False):
        return

    original_metadata = ipc._metadata_from_declaration

    def metadata_with_claim_identity(ws):
        raw = original_metadata(ws)
        metadata = dict(raw or {})
        current_raw = str(metadata.get("claim_no") or "").strip()
        current = _clean_claim_no(current_raw)

        # Rule: semantic content first, legacy fixed cell second, filename hint
        # third. Fixed cells are compatibility fallback only.
        if not current and ws is not None:
            try:
                legacy_raw = str(ipc._safe_cell(ws, "B12") or "").strip()
                legacy = _clean_claim_no(legacy_raw)
            except Exception:
                legacy_raw = ""
                legacy = ""
            if legacy:
                metadata["claim_no"] = legacy_raw
                adaptive = dict(metadata.get("_adaptive") or {})
                sources = dict(adaptive.get("sources") or {})
                fields = dict(adaptive.get("field_confidence") or {})
                sources["claim_no"] = "B12 (legacy fallback)"
                fields["claim_no"] = max(float(fields.get("claim_no") or 0), 0.55)
                adaptive["sources"] = sources
                adaptive["field_confidence"] = fields
                metadata["_adaptive"] = adaptive
                current = legacy

        if not current:
            hint = _clean_claim_no(_FILENAME_CLAIM_HINT.get(""))
            if hint:
                metadata["claim_no"] = hint
                adaptive = dict(metadata.get("_adaptive") or {})
                sources = dict(adaptive.get("sources") or {})
                fields = dict(adaptive.get("field_confidence") or {})
                sources["claim_no"] = "filename hint"
                fields["claim_no"] = max(float(fields.get("claim_no") or 0), 0.80)
                adaptive["sources"] = sources
                adaptive["field_confidence"] = fields
                metadata["_adaptive"] = adaptive
        return metadata

    ipc._metadata_from_declaration = metadata_with_claim_identity

    original_parse = ipc.parse_ipc_workbook

    def parse_ipc_workbook_number_safe(data: bytes, filename: str = "IPC.xlsx"):
        file_no = claim_no_from_filename(filename)
        token = _FILENAME_CLAIM_HINT.set(file_no)
        try:
            result = original_parse(data, filename)
        finally:
            _FILENAME_CLAIM_HINT.reset(token)
        return _apply_filename_identity(result, filename)

    ipc.parse_ipc_workbook = parse_ipc_workbook_number_safe
    ipc._qlda_ipc_claim_number_fix_installed = True
    ipc._qlda_ipc_claim_number_fix_marker = PATCH_VERSION
