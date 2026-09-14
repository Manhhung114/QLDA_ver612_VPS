from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from qlda.runtime_core.boq_cost_components import ensure_cost_component_schema

PATCH_VERSION = "V6.24.2 BOQ BATCHED PERSISTENCE"
AUTO_NOTE_PREFIX = "[QLDA_BOQ_EXCEL]"
VERIFY_COMPLETE = "HOÀN TẤT"
VERIFY_INCOMPLETE = "CHƯA ĐỦ"

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


def _scalar_int(row: Any, key: str = "row_count") -> int:
    if row is None:
        return 0
    try:
        return max(0, int(row[key] or 0))
    except Exception:
        try:
            return max(0, int(row[0] or 0))
        except Exception:
            return 0


def _count_written_batch(connection, project_id: int, batch_id: str) -> int:
    """Count the rows PostgreSQL can actually read for this exact BOQ batch."""
    row = connection.execute(
        "SELECT COUNT(*) AS row_count FROM cost_budgets "
        "WHERE project_id=? AND note LIKE ?",
        (int(project_id), f"{AUTO_NOTE_PREFIX}%|batch={batch_id}"),
    ).fetchone()
    return _scalar_int(row)


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
    import qlda.runtime_core.boq_multisheet as boq
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
    prepared_rows = len(detail_items)
    parsed_rows = int(result.get("detail_line_count") or prepared_rows)
    expected_rows = parsed_rows
    written_rows = 0
    failed_rows = expected_rows
    verification_status = VERIFY_INCOMPLETE
    if parsed_rows != prepared_rows:
        raise boq.BOQWorkbookError(
            "BOQ chưa nhất quán trước khi ghi PostgreSQL: "
            f"đã quét {parsed_rows:,} dòng nhưng chuẩn bị ghi {prepared_rows:,} dòng."
        )

    total = prepared_rows
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

        # Do not trust the client-side loop counter. Read the batch back from
        # PostgreSQL in the same transaction and fail atomically on any gap.
        written_rows = _count_written_batch(connection, pid, batch_id)
        failed_rows = abs(expected_rows - written_rows)
        complete = (
            expected_rows > 0
            and prepared_rows == expected_rows
            and inserted == expected_rows
            and written_rows == expected_rows
        )
        verification_status = VERIFY_COMPLETE if complete else VERIFY_INCOMPLETE
        if progress:
            progress(
                93,
                "Xác minh PostgreSQL · "
                f"expected={expected_rows:,} · scanned={parsed_rows:,} · "
                f"written={written_rows:,} · failed={failed_rows:,} · {verification_status}",
                "",
            )
        if not complete:
            raise boq.BOQWorkbookError(
                "Xác minh số dòng BOQ PostgreSQL CHƯA ĐỦ: "
                f"expected={expected_rows:,}, scanned={parsed_rows:,}, "
                f"prepared={prepared_rows:,}, inserted={inserted:,}, "
                f"written={written_rows:,}, failed={failed_rows:,}. "
                "Transaction đã rollback; BOQ cũ vẫn được giữ nguyên."
            )

    return {
        "pipeline": PATCH_VERSION,
        "inserted": inserted,
        "expected_rows": expected_rows,
        "scanned_rows": parsed_rows,
        "prepared_rows": prepared_rows,
        "written_rows": written_rows,
        "failed_rows": failed_rows,
        "verification_status": verification_status,
        "verified_postgresql": verification_status == VERIFY_COMPLETE,
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
