from __future__ import annotations

"""Source-faithful production semantics for Contractor Data Hub.

The SME tower workbook repeats labels such as "I. Thi công lắp đặt phần thô"
under CĂN HỘ / HÀNH LANG / TRỤC ĐỨNG.  Those rows are distinct because their
source-row identity is distinct.  It also provides a real TỔNG column in the
main floor matrix.  This patch keeps that total as a structured dimension so
UI/AI can use the source-defined total instead of inventing an average.
"""

from typing import Iterable

PATCH_MARKER = "V7 CONTRACTOR DATA SOURCE SEMANTICS V1"


def _norm(value: object) -> str:
    import re
    import unicodedata

    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("đ", "d")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _is_total_header(value: object) -> bool:
    return _norm(value) in {"tong", "total"}


def _stop_before_summary(rows: list[list[object]], header_idx: int) -> int:
    """Return exclusive row index for the primary floor matrix."""
    stop_at = len(rows)
    for idx in range(header_idx + 1, len(rows)):
        normalized = [_norm(cell) for cell in rows[idx]]
        if any(text == "tong san luong" or text.startswith("tong san luong ") for text in normalized if text):
            stop_at = idx
            break
    return stop_at


def install_contractor_data_source_semantics() -> None:
    import qlda.application.contractor_data_hub.service as hub_service
    import qlda.application.google_sheets.service as sheet_service

    if getattr(hub_service, "_qlda_source_semantics_v1", False):
        return

    previous_normalize = hub_service.normalize_production_sheet

    def normalize_source_exact(worksheet: str, values: Iterable[Iterable[object]]):
        rows = [list(row) for row in values]
        base = list(previous_normalize(worksheet, rows))
        if not rows:
            return base

        try:
            header_idx, headers, progress_columns, layout = sheet_service._progress_header_row(rows)
        except Exception:
            return base
        if str(layout) != "FLOOR_MATRIX":
            return base

        total_columns = [idx for idx, header in enumerate(headers) if _is_total_header(header)]
        if not total_columns:
            return base

        # The source matrix places its row total immediately after the last floor.
        last_progress_col = max((idx for idx, _ in progress_columns), default=-1)
        total_col = next((idx for idx in total_columns if idx > last_progress_col), total_columns[-1])
        work_col = sheet_service._work_item_column(headers, {idx for idx, _ in progress_columns})
        stop_at = _stop_before_summary(rows, header_idx)

        # Avoid re-adding totals if a future canonical parser learns this natively.
        existing = {
            (int(getattr(row, "source_row", 0) or 0), str(getattr(row, "zone", "") or "").strip().upper())
            for row in base
        }

        last_work_item = ""
        for zero_idx in range(header_idx + 1, stop_at):
            row = rows[zero_idx]
            source_row = zero_idx + 1
            raw_work = row[work_col] if work_col < len(row) else None
            work_item = str(raw_work or "").strip()

            raw_total = row[total_col] if total_col < len(row) else None
            pct = sheet_service._percent(raw_total)
            if pct is None or pct < 0.0 or pct > 100.0:
                if work_item:
                    last_work_item = work_item
                continue
            if not work_item:
                work_item = last_work_item
            if not work_item:
                continue
            last_work_item = work_item

            key = (source_row, "TỔNG")
            if key in existing:
                continue
            base.append(
                sheet_service.ProductionRow(
                    worksheet=str(worksheet or ""),
                    work_item=work_item,
                    zone="TỔNG",
                    progress_percent=float(pct),
                    source_row=source_row,
                )
            )
            existing.add(key)

        return base

    # ContractorDataHubService imported the function by name, therefore update
    # both references. Workers import runtime_core before syncing, so this applies
    # equally to Streamlit and background synchronization.
    hub_service.normalize_production_sheet = normalize_source_exact
    sheet_service.normalize_production_sheet = normalize_source_exact
    hub_service._qlda_source_semantics_v1 = True
    hub_service._qlda_source_semantics_marker = PATCH_MARKER


__all__ = ["PATCH_MARKER", "install_contractor_data_source_semantics"]
