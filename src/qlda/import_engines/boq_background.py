from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any, Callable

PATCH_VERSION = "V6.24.2 BOQ BACKGROUND PIPELINE"

ProgressFn = Callable[[int, str, str], None]
CancelFn = Callable[[], bool]


def _sha256_file(path: Path, chunk_size: int = 4 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _max_file_bytes() -> int:
    try:
        mb = int(os.environ.get("QLDA_BOQ_WORKER_MAX_FILE_MB", "512") or 512)
    except Exception:
        mb = 512
    return max(25, min(mb, 2048)) * 1024 * 1024


def _component_totals(result: dict[str, Any]) -> None:
    detail = list(result.get("detail_items") or [])
    material_rows = [
        item for item in detail
        if item.get("material_unit_price") is not None or item.get("material_cost") is not None
    ]
    labor_rows = [
        item for item in detail
        if item.get("labor_unit_price") is not None or item.get("labor_cost") is not None
    ]
    full_rows = [
        item for item in detail
        if item.get("material_unit_price") is not None and item.get("labor_unit_price") is not None
    ]
    result["material_component_line_count"] = len(material_rows)
    result["labor_component_line_count"] = len(labor_rows)
    result["full_component_line_count"] = len(full_rows)
    result["material_cost_total"] = float(sum(float(item.get("material_cost") or 0) for item in material_rows))
    result["labor_cost_total"] = float(sum(float(item.get("labor_cost") or 0) for item in labor_rows))

    discrepancy_count = 0
    for item in full_rows:
        split_price = float(item.get("material_unit_price") or 0) + float(item.get("labor_unit_price") or 0)
        all_in = float(item.get("unit_price") or 0)
        tolerance = max(1.0, abs(all_in) * 1e-8)
        if abs(split_price - all_in) > tolerance:
            discrepancy_count += 1
    result["component_discrepancy_count"] = discrepancy_count

    warnings = result.setdefault("warnings", [])
    if material_rows or labor_rows:
        warnings.append(
            f"Đã nhận diện thành phần đơn giá: vật tư {len(material_rows):,} dòng, "
            f"nhân công {len(labor_rows):,} dòng."
        )
    if discrepancy_count:
        warnings.append(
            f"Có {discrepancy_count:,} dòng mà Đơn giá vật tư + Đơn giá nhân công lệch đơn giá tổng; "
            "hệ thống giữ nguyên tổng tiền gốc của BOQ."
        )


def parse_boq_path(
    path: str | Path,
    filename: str = "BOQ.xlsx",
    *,
    progress: ProgressFn | None = None,
    cancelled: CancelFn | None = None,
) -> dict[str, Any]:
    """Parse BOQ directly from the VPS file path.

    The XLSX/XLMS ZIP is opened by openpyxl in read-only mode. The source file is
    never copied into Streamlit memory and is never read as one giant bytes object.
    The returned structure intentionally matches ``parse_boq_workbook`` so all
    existing BOQ save, persistence and AI semantics remain reusable.
    """
    from openpyxl import load_workbook

    # Install the same split-price semantics used by the web app. This patches
    # boq._parse_sheet and boq.save_boq_summary_to_project in-process.
    from qlda.runtime_core.boq_cost_components import install_boq_cost_components
    from qlda.runtime_core.boq_claim_terms import install_boq_claim_terms

    install_boq_cost_components()
    install_boq_claim_terms()

    import qlda.runtime_core.boq_multisheet as boq
    source = Path(path)
    if not source.exists() or not source.is_file():
        raise FileNotFoundError(f"Không tìm thấy file BOQ trên VPS: {source}")
    suffix = source.suffix.lower()
    if suffix not in {".xlsx", ".xlsm"}:
        raise boq.BOQWorkbookError("Background BOQ chỉ hỗ trợ file .xlsx hoặc .xlsm.")
    size = int(source.stat().st_size)
    limit = _max_file_bytes()
    if size <= 0:
        raise boq.BOQWorkbookError("File BOQ đang trống.")
    if size > limit:
        raise boq.BOQWorkbookError(
            f"File BOQ {size / 1024 / 1024:.1f} MB vượt giới hạn worker "
            f"{limit / 1024 / 1024:.0f} MB."
        )

    if cancelled and cancelled():
        raise InterruptedError("Job BOQ đã được yêu cầu hủy.")
    if progress:
        progress(8, "Đang kiểm tra file BOQ", "")
    batch_id = _sha256_file(source)[:16]

    if progress:
        progress(12, "Đang mở workbook BOQ từ SSD", "")
    try:
        wb = load_workbook(str(source), data_only=True, read_only=True, keep_links=False)
    except Exception as exc:
        raise boq.BOQWorkbookError(f"Không đọc được workbook Excel: {exc}") from exc

    try:
        visible_sheets = [
            ws for ws in wb.worksheets
            if getattr(ws, "sheet_state", "visible") == "visible"
        ]
        if not visible_sheets:
            raise boq.BOQWorkbookError("Workbook BOQ không có sheet hiển thị.")

        snapshots: dict[str, dict[str, Any]] = {}
        summary_sheet = None
        parsed: list[dict[str, Any]] = []
        total = max(1, len(visible_sheets))

        for index, ws in enumerate(visible_sheets, start=1):
            if cancelled and cancelled():
                raise InterruptedError("Job BOQ đã được yêu cầu hủy.")
            pct = 15 + int((index - 1) * 55 / total)
            if progress:
                progress(pct, "Đang phân tích BOQ", str(ws.title))

            # Preview is intentionally capped by the existing V6.22 constants.
            # It is persisted for cross-account viewing but never contains the
            # complete huge worksheet when the source is very large.
            snapshots[ws.title] = boq._sheet_snapshot(ws)

            if summary_sheet is None and boq._is_summary_sheet_name(ws.title):
                summary_sheet = boq._extract_summary_sheet(ws)

            item = boq._parse_sheet(ws)
            if item:
                parsed.append(item)

        if not parsed:
            raise boq.BOQWorkbookError(
                "Không nhận diện được sheet BOQ chi tiết. Cần có cột Nội dung công việc và Thành tiền, "
                "hoặc bộ Khối lượng + Đơn giá."
            )

        summary = [
            {
                "sheet": item["sheet"],
                "line_count": int(item["line_count"]),
                "budget_total": float(item["budget_total"]),
                "header_row": int(item["header_row"]),
            }
            for item in parsed
        ]
        detail_items: list[dict[str, Any]] = []
        for sheet_data in parsed:
            sheet_name = str(sheet_data["sheet"])
            for item in sheet_data.get("items") or []:
                detail_items.append({"sheet": sheet_name, **item})

        detail_total = float(sum(float(item.get("budget_total") or 0) for item in detail_items))
        warnings: list[str] = []
        if summary_sheet:
            warnings.append(
                f"Sheet tổng hợp '{summary_sheet['sheet']}' được hiển thị riêng và không cộng lặp vào BOQ chi tiết."
            )
            before_tax = summary_sheet.get("before_tax_total")
            if before_tax is not None:
                tolerance = max(1.0, abs(float(before_tax)) * 1e-8)
                if abs(detail_total - float(before_tax)) > tolerance:
                    warnings.append(
                        "Tổng các dòng BOQ chi tiết lệch so với 'Cộng giá trị trước thuế' trong sheet tổng hợp: "
                        f"{detail_total:,.0f} so với {float(before_tax):,.0f} VND."
                    )

        before_tax_total = (
            float(summary_sheet["before_tax_total"])
            if summary_sheet and summary_sheet.get("before_tax_total") is not None
            else detail_total
        )
        vat_total = (
            float(summary_sheet["vat_total"])
            if summary_sheet and summary_sheet.get("vat_total") is not None
            else 0.0
        )
        after_tax_total = (
            float(summary_sheet["after_tax_total"])
            if summary_sheet and summary_sheet.get("after_tax_total") is not None
            else before_tax_total + vat_total
        )

        result = {
            "filename": Path(str(filename or source.name)).name,
            "batch_id": batch_id,
            "workbook_sheet_names": [ws.title for ws in visible_sheets],
            "workbook_sheets": snapshots,
            "summary_sheet": summary_sheet,
            "detected_sheets": [item["sheet"] for item in parsed],
            "summary": summary,
            "detail_items": detail_items,
            "detail_line_count": len(detail_items),
            "detail_grand_total": detail_total,
            "before_tax_total": before_tax_total,
            "vat_total": vat_total,
            "after_tax_total": after_tax_total,
            "grand_total": after_tax_total,
            "warnings": warnings,
            "pipeline": PATCH_VERSION,
            "source_file_size": size,
        }
        _component_totals(result)
        if progress:
            progress(72, "Đã phân tích xong BOQ", "")
        return result
    finally:
        wb.close()
