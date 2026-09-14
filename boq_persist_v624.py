from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from boq_cost_components_v622 import ensure_cost_component_schema

PATCH_VERSION = "V6.24.2 BOQ BATCHED PERSISTENCE"
AUTO_NOTE_PREFIX = "[QLDA_BOQ_EXCEL]"

ProgressFn = Callable[[int, str, str], None]
CancelFn = Callable[[], bool]


def _nullable_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except Exception:
        return None


def _batch_size(value: int | None = None) -> int:
    if value is None:
        try:
            value = int(os.environ.get("QLDA_BOQ_DB_BATCH_SIZE", "500") or 500)
        except Exception:
            value = 500
    return max(50, min(int(value), 2000))


def save_boq_result_batched(
    db,
    project_id: int,
    result: dict[str, Any],
    *,
    replace_existing_excel: bool = True,
    progress: ProgressFn | None = None,
    cancelled: CancelFn | None = None,
    batch_size: int | None = None,
) -> dict[str, Any]:
    """Persist BOQ detail rows transactionally in bounded PostgreSQL batches.

    This keeps the exact V6.22 BOQ row vocabulary/note marker while writing the
    V6.22 material/labor component columns in the same INSERT. Cancellation or
    an exception rolls back the whole replacement transaction.
    """
    import boq_multisheet_v622 as boq

    detail_items = list(result.get("detail_items") or [])
    if not detail_items:
        raise boq.BOQWorkbookError("Không có dòng BOQ chi tiết để lưu.")

    pid = int(project_id)
    if pid <= 0:
        raise ValueError("project_id BOQ không hợp lệ.")
    filename = Path(str(result.get("filename") or "BOQ.xlsx")).name.replace("|", "_")
    batch_id = re.sub(r"[^a-fA-F0-9]", "", str(result.get("batch_id") or ""))[:32]
    if not batch_id:
        batch_id = hashlib.sha256(repr(detail_items).encode("utf-8")).hexdigest()[:16]
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    chunk_size = _batch_size(batch_size)

    # The worker installs the same V6.22 component schema before the transaction.
    ensure_cost_component_schema(db)

    sql = (
        "INSERT INTO cost_budgets("
        "project_id,task_ref,boq_item,quantity,unit,unit_price,budget_total,"
        "contract_type,contractor,note,created_at,updated_at,"
        "material_unit_price,labor_unit_price,material_cost,labor_cost"
        ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
    )

    deleted = 0
    inserted = 0
    total = len(detail_items)
    with db.connect() as connection:
        if cancelled and cancelled():
            raise InterruptedError("Job BOQ đã được yêu cầu hủy trước khi ghi database.")
        if replace_existing_excel:
            cur = connection.execute(
                "DELETE FROM cost_budgets WHERE project_id=? AND note LIKE ?",
                (pid, AUTO_NOTE_PREFIX + "%"),
            )
        else:
            cur = connection.execute(
                "DELETE FROM cost_budgets WHERE project_id=? AND note LIKE ?",
                (pid, f"{AUTO_NOTE_PREFIX}%batch={batch_id}%"),
            )
        try:
            deleted = max(0, int(cur.rowcount or 0))
        except Exception:
            deleted = 0

        for start in range(0, total, chunk_size):
            if cancelled and cancelled():
                raise InterruptedError("Job BOQ đã được yêu cầu hủy; transaction đã rollback.")
            stop = min(total, start + chunk_size)
            params = []
            for item in detail_items[start:stop]:
                sheet = str(item.get("sheet") or "BOQ").replace("|", "_")
                row_no = int(item.get("row_no") or 0)
                note = f"{AUTO_NOTE_PREFIX} file={filename}|sheet={sheet}|row={row_no}|batch={batch_id}"
                params.append(
                    (
                        pid,
                        str(item.get("task_ref") or ""),
                        str(item.get("boq_item") or ""),
                        float(item.get("quantity") or 0),
                        str(item.get("unit") or ""),
                        float(item.get("unit_price") or 0),
                        float(item.get("budget_total") or 0),
                        "",
                        "",
                        note,
                        now,
                        now,
                        _nullable_float(item.get("material_unit_price")),
                        _nullable_float(item.get("labor_unit_price")),
                        _nullable_float(item.get("material_cost")),
                        _nullable_float(item.get("labor_cost")),
                    )
                )
            connection.executemany(sql, params)
            inserted = stop
            if progress:
                pct = 76 + int(inserted * 16 / max(1, total))
                progress(min(pct, 92), f"Đang ghi BOQ vào PostgreSQL · {inserted:,}/{total:,} dòng", "")

    return {
        "pipeline": PATCH_VERSION,
        "inserted": inserted,
        "deleted": deleted,
        "batch_id": batch_id,
        "detail_grand_total": float(result.get("detail_grand_total") or 0),
        "before_tax_total": float(result.get("before_tax_total") or 0),
        "vat_total": float(result.get("vat_total") or 0),
        "after_tax_total": float(result.get("after_tax_total") or 0),
        "grand_total": float(result.get("after_tax_total") or result.get("detail_grand_total") or 0),
        "material_cost_total": float(result.get("material_cost_total") or 0),
        "labor_cost_total": float(result.get("labor_cost_total") or 0),
        "material_component_line_count": int(result.get("material_component_line_count") or 0),
        "labor_component_line_count": int(result.get("labor_component_line_count") or 0),
    }
