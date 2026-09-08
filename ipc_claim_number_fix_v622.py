from __future__ import annotations

import re
from pathlib import Path
from typing import Any


PATCH_VERSION = "V6.22 IPC CLAIM NUMBER V1"


def _clean_claim_no(value: Any) -> str:
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
    internal_no = _clean_claim_no(parsed.get("claim_no"))
    if not file_no:
        return parsed

    metadata = dict(parsed.get("metadata") or {})
    metadata_no = _clean_claim_no(metadata.get("claim_no"))
    source_no = internal_no or metadata_no

    parsed["claim_no"] = file_no
    parsed["claim_code"] = f"IPC-{file_no.zfill(2)}"
    metadata["claim_no"] = file_no
    parsed["metadata"] = metadata
    parsed["claim_number_source"] = "filename"
    parsed["claim_number_internal"] = source_no

    warnings = list(parsed.get("warnings") or [])
    if source_no and source_no != file_no:
        warning = (
            f"CẢNH BÁO SỐ CLAIM: file Excel bên trong đang khai báo Claim {source_no}, "
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
    """
    import ipc_claim_v622 as ipc

    if getattr(ipc, "_qlda_ipc_claim_number_fix_installed", False):
        return

    original_parse = ipc.parse_ipc_workbook

    def parse_ipc_workbook_number_safe(data: bytes, filename: str = "IPC.xlsx"):
        result = original_parse(data, filename)
        return _apply_filename_identity(result, filename)

    ipc.parse_ipc_workbook = parse_ipc_workbook_number_safe
    ipc._qlda_ipc_claim_number_fix_installed = True
    ipc._qlda_ipc_claim_number_fix_marker = PATCH_VERSION
