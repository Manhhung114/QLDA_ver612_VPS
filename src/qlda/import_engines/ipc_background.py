from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any, Callable


PATCH_VERSION = "V6.24.3 IPC BACKGROUND PIPELINE"

ProgressFn = Callable[[int, str, str], None]
CancelFn = Callable[[], bool]


def _sha256_file(path: Path, chunk_size: int = 4 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                return digest.hexdigest()
            digest.update(chunk)


def _max_file_bytes() -> int:
    try:
        value = int(os.environ.get("QLDA_IPC_WORKER_MAX_FILE_MB", "512") or 512)
    except Exception:
        value = 512
    return max(25, min(value, 2048)) * 1024 * 1024


def _install_ipc_parser_semantics() -> None:
    """Install the same parser semantics used by the production Streamlit app."""
    from ipc_claim_fast_v622 import install_ipc_claim_fast_path
    from ipc_claim_summary_fix_v622 import install_ipc_claim_summary_fix
    from ipc_adaptive_parser_v622 import install_ipc_adaptive_parser
    from ipc_payment_semantic_v622 import install_ipc_payment_semantic
    from ipc_claim_number_fix_v622 import install_ipc_claim_number_fix
    from ipc_claim_period_v622 import install_ipc_claim_period_parser_fix
    from boq_claim_terms_v622 import install_boq_claim_terms
    from boq_claim_price_recovery_v622 import install_boq_claim_price_recovery
    from boq_claim_price_header_guard_v622 import install_boq_claim_price_header_guard

    install_ipc_claim_fast_path()
    install_ipc_claim_summary_fix()
    install_ipc_adaptive_parser()
    install_ipc_payment_semantic()
    install_ipc_claim_number_fix()
    install_ipc_claim_period_parser_fix()
    install_boq_claim_terms()
    install_boq_claim_price_recovery()
    install_boq_claim_price_header_guard()


def _decorate_parser_profile(result: dict[str, Any], filename: str) -> dict[str, Any]:
    from ipc_adaptive_parser_v622 import _adaptive_parse_wrapper_factory
    from ipc_claim_number_fix_v622 import _apply_filename_identity

    decorate = _adaptive_parse_wrapper_factory(lambda _data, _filename: result)
    profiled = decorate(b"", filename)
    return _apply_filename_identity(profiled, filename)


def parse_ipc_path(
    path: str | Path,
    filename: str = "IPC.xlsx",
    *,
    progress: ProgressFn | None = None,
    cancelled: CancelFn | None = None,
) -> dict[str, Any]:
    """Parse one IPC directly from its durable VPS path without read_bytes()."""
    from openpyxl import load_workbook

    _install_ipc_parser_semantics()
    import ipc_claim_v622 as ipc

    source = Path(path)
    if not source.exists() or not source.is_file():
        raise FileNotFoundError(f"Không tìm thấy file IPC trên VPS: {source}")
    if source.suffix.lower() not in {".xlsx", ".xlsm"}:
        raise ipc.IPCWorkbookError("IPC Background chỉ hỗ trợ file .xlsx hoặc .xlsm.")
    size = int(source.stat().st_size)
    if size <= 0:
        raise ipc.IPCWorkbookError("File IPC đang trống.")
    limit = _max_file_bytes()
    if size > limit:
        raise ipc.IPCWorkbookError(
            f"File IPC {size / 1024 / 1024:.1f} MB vượt giới hạn worker "
            f"{limit / 1024 / 1024:.0f} MB."
        )
    if cancelled and cancelled():
        raise InterruptedError("Job IPC đã được yêu cầu hủy.")

    if progress:
        progress(8, "Đang kiểm tra file IPC", "")
    source_sha256 = _sha256_file(source)
    batch_id = source_sha256[:20]
    display_name = Path(str(filename or source.name)).name

    if progress:
        progress(12, "Đang mở IPC từ SSD ở chế độ read-only", "")
    try:
        workbook = load_workbook(
            str(source),
            data_only=True,
            read_only=True,
            keep_links=False,
        )
    except Exception as exc:
        raise ipc.IPCWorkbookError(f"Không đọc được workbook IPC: {exc}") from exc

    try:
        visible = [
            sheet for sheet in workbook.worksheets
            if getattr(sheet, "sheet_state", "visible") == "visible"
        ]
        if not visible:
            raise ipc.IPCWorkbookError("Workbook IPC không có sheet hiển thị.")

        snapshots: dict[str, dict[str, Any]] = {}
        total_sheets = max(1, len(visible))
        for index, sheet in enumerate(visible, start=1):
            if cancelled and cancelled():
                raise InterruptedError("Job IPC đã được yêu cầu hủy.")
            if progress:
                progress(
                    15 + int((index - 1) * 30 / total_sheets),
                    "Đang tạo preview IPC",
                    str(sheet.title),
                )
            snapshots[str(sheet.title)] = ipc._sheet_snapshot(sheet)

        if progress:
            progress(48, "Đang nhận diện các sheet IPC theo nội dung", "")
        declaration = ipc._first_sheet(workbook, ("KHAI BÁO", "KHAI BAO"))
        payment = ipc._first_sheet(workbook, ("Thanh toán", "Thanh toan", "Payment"))
        gtht = ipc._first_sheet(workbook, ("GTHT", "Giá trị hoàn thành", "Gia tri hoan thanh"))
        metadata = ipc._metadata_from_declaration(declaration)
        summary = ipc._payment_summary(payment)
        if progress:
            progress(58, "Đang quét toàn bộ chi tiết GTHT", str(getattr(gtht, "title", "")))
        details = ipc._parse_gtht(gtht)
        fallback_claim_no = str(ipc._safe_cell(payment, "L8") or "").strip() if payment is not None else ""
    finally:
        workbook.close()

    claim_no = str(metadata.get("claim_no") or fallback_claim_no or "").strip()
    if not claim_no:
        raise ipc.IPCWorkbookError("Không xác định được số Claim/Thanh toán lần trong file Excel.")
    claim_code = f"IPC-{claim_no.zfill(2)}" if claim_no.isdigit() else f"IPC-{claim_no}"
    warnings: list[str] = []
    if not details:
        warnings.append("CHƯA ĐỦ: chưa nhận diện được dòng chi tiết GTHT; PostgreSQL sẽ không được cập nhật.")
    if not summary.get("requested_amount"):
        warnings.append("Chưa đọc được Giá trị thanh toán kỳ này từ sheet thanh toán.")

    result = {
        "filename": display_name,
        "batch_id": batch_id,
        "claim_no": claim_no,
        "claim_code": claim_code,
        "metadata": metadata,
        "summary": summary,
        "workbook_sheet_names": [str(sheet.title) for sheet in visible],
        "workbook_sheets": snapshots,
        "detail_items": details,
        "detail_line_count": len(details),
        "warnings": warnings,
        "pipeline": PATCH_VERSION,
        "source_file_size": size,
        "source_sha256": source_sha256,
    }
    result = _decorate_parser_profile(result, display_name)
    if progress:
        progress(
            72,
            f"Đã quét {int(result.get('detail_line_count') or 0):,} dòng IPC",
            str(result.get("claim_code") or ""),
        )
    return result
