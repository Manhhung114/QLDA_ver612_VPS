from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from typing import Any, Callable


PATCH_VERSION = "V6.24.4 VO BATCHED PERSISTENCE"
VERIFY_COMPLETE = "HOÀN TẤT"
VERIFY_INCOMPLETE = "CHƯA ĐỦ"

ProgressFn = Callable[[int, str, str], None]
CancelFn = Callable[[], bool]


@contextmanager
def _atomic(connection):
    try:
        yield connection
    except BaseException:
        connection.rollback()
        raise


def _batch_size(value: int | None = None) -> int:
    if value is None:
        try:
            value = int(os.environ.get("QLDA_VO_DB_BATCH_SIZE", "500") or 500)
        except Exception:
            value = 500
    return max(50, min(int(value), 2000))


def _rowdict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    try:
        return {str(key): row[key] for key in row.keys()}
    except Exception:
        try:
            return dict(row)
        except Exception:
            return {}


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


def save_vo_result_batched(
    db,
    project_id: int,
    result: dict[str, Any],
    *,
    progress: ProgressFn | None = None,
    cancelled: CancelFn | None = None,
    batch_size: int | None = None,
) -> dict[str, Any]:
    """Atomically save one VO revision and verify every detail row in PostgreSQL."""
    import qlda.runtime_core.vo_claim as core
    pid = int(project_id)
    if pid <= 0:
        raise ValueError("project_id VO không hợp lệ.")
    vo_code = str(result.get("vo_code") or "").strip()
    if not vo_code:
        raise core.VOWorkbookError("VO chưa có mã.")
    detail_items = list(result.get("detail_items") or [])
    prepared_rows = len(detail_items)
    scanned_rows = int(result.get("detail_line_count") or prepared_rows)
    expected_rows = scanned_rows
    if expected_rows <= 0:
        raise core.VOWorkbookError("VO CHƯA ĐỦ: không có dòng chi tiết để ghi PostgreSQL.")
    if scanned_rows != prepared_rows:
        raise core.VOWorkbookError(
            f"VO chưa nhất quán trước khi ghi PostgreSQL: scanned={scanned_rows:,}, prepared={prepared_rows:,}."
        )

    metadata = dict(result.get("metadata") or {})
    summary = dict(result.get("summary") or {})
    filename = str(result.get("filename") or "VO.xlsx")
    batch_id = str(result.get("batch_id") or "").strip()
    if not batch_id:
        raise core.VOWorkbookError("VO thiếu batch_id/SHA của file gốc.")
    payload = core._encode_result(result)
    now = core._now()
    chunk_size = _batch_size(batch_size)
    inserted_rows = 0

    with db.connect() as connection, _atomic(connection):
        core._ensure_tables(connection)
        if cancelled and cancelled():
            raise InterruptedError("Job VO đã được yêu cầu hủy trước khi ghi database.")
        old_row = connection.execute(
            f"SELECT * FROM {core.VO_TABLE} WHERE project_id=? AND vo_code=?",
            (pid, vo_code),
        ).fetchone()
        old = _rowdict(old_row)
        vo_id = str(old.get("vo_id") or uuid.uuid4().hex)
        old_batch = str(old.get("batch_id") or "")
        latest = int(old.get("latest_revision") or 0)
        revision_no = 0 if old_row is None else latest + 1 if old_batch != batch_id else latest
        order = {
            "vo_id": vo_id,
            "project_id": pid,
            "vo_no": int(result.get("vo_no") or 0),
            "vo_code": vo_code,
            "filename": filename,
            "batch_id": batch_id,
            "revision_label": str(metadata.get("revision_label") or f"R{revision_no}"),
            "vo_date": str(metadata.get("vo_date") or old.get("vo_date") or ""),
            "project_name": str(metadata.get("project") or ""),
            "package_name": str(metadata.get("package") or ""),
            "subtotal_before_vat": float(summary.get("subtotal_before_vat") or 0),
            "vat_amount": float(summary.get("vat_amount") or 0),
            "total_after_vat": float(summary.get("total_after_vat") or 0),
            "increase_amount": float(summary.get("increase_amount") or 0),
            "decrease_amount": float(summary.get("decrease_amount") or 0),
            "proposed_amount": float(summary.get("total_after_vat") or 0),
            "approved_amount": float(old.get("approved_amount") or 0),
            "funding_source": str(old.get("funding_source") or ""),
            "status": str(old.get("status") or "Dự thảo"),
            "latest_revision": int(revision_no),
            "note": str(old.get("note") or ""),
            "created_at": str(old.get("created_at") or now),
            "updated_at": now,
        }

        connection.execute(f"DELETE FROM {core.VO_TABLE} WHERE vo_id=?", (vo_id,))
        fields = list(order)
        connection.execute(
            f"INSERT INTO {core.VO_TABLE}({','.join(fields)}) VALUES({','.join('?' for _ in fields)})",
            tuple(order[field] for field in fields),
        )
        connection.execute(f"DELETE FROM {core.WORKBOOK_TABLE} WHERE vo_id=?", (vo_id,))
        connection.execute(
            f"INSERT INTO {core.WORKBOOK_TABLE}(vo_id,project_id,filename,batch_id,payload,updated_at) "
            "VALUES(?,?,?,?,?,?)",
            (vo_id, pid, filename, batch_id, payload, now),
        )
        if old_row is None or old_batch != batch_id:
            connection.execute(
                f"INSERT INTO {core.REVISIONS_TABLE}("
                "revision_id,vo_id,project_id,revision_no,revision_label,filename,batch_id,payload,created_at"
                ") VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    uuid.uuid4().hex,
                    vo_id,
                    pid,
                    revision_no,
                    str(metadata.get("revision_label") or f"R{revision_no}"),
                    filename,
                    batch_id,
                    payload,
                    now,
                ),
            )

        connection.execute(f"DELETE FROM {core.ITEMS_TABLE} WHERE vo_id=?", (vo_id,))
        item_fields = [
            "vo_id",
            "project_id",
            "sheet_name",
            "row_no",
            "seq",
            "description",
            "unit",
            "contract_qty",
            "actual_qty",
            "increase_qty",
            "decrease_qty",
            "variation_qty",
            "spec",
            "item_code",
            "brand",
            "origin",
            "material_unit_price",
            "labor_unit_price",
            "unit_price_total",
            "variation_amount",
            "variation_kind",
            "note",
        ]
        sql = (
            f"INSERT INTO {core.ITEMS_TABLE}({','.join(item_fields)}) "
            f"VALUES({','.join('?' for _ in item_fields)})"
        )
        for start in range(0, expected_rows, chunk_size):
            if cancelled and cancelled():
                raise InterruptedError("Job VO đã được yêu cầu hủy; transaction đã rollback.")
            stop = min(expected_rows, start + chunk_size)
            params = []
            for item in detail_items[start:stop]:
                row = {**item, "vo_id": vo_id, "project_id": pid}
                params.append(tuple(row.get(field, "") for field in item_fields))
            connection.executemany(sql, params)
            inserted_rows = stop
            if progress:
                progress(
                    min(92, 76 + int(inserted_rows * 16 / max(1, expected_rows))),
                    f"Đang ghi {vo_code} vào PostgreSQL · {inserted_rows:,}/{expected_rows:,} dòng",
                    vo_code,
                )

        written_rows = _scalar_int(
            connection.execute(
                f"SELECT COUNT(*) AS row_count FROM {core.ITEMS_TABLE} WHERE vo_id=?",
                (vo_id,),
            ).fetchone()
        )
        failed_rows = abs(expected_rows - written_rows)
        complete = (
            expected_rows > 0
            and prepared_rows == expected_rows
            and inserted_rows == expected_rows
            and written_rows == expected_rows
        )
        verification_status = VERIFY_COMPLETE if complete else VERIFY_INCOMPLETE
        if progress:
            progress(
                93,
                f"Xác minh {vo_code} · expected={expected_rows:,} · scanned={scanned_rows:,} · "
                f"written={written_rows:,} · failed={failed_rows:,} · {verification_status}",
                vo_code,
            )
        if not complete:
            raise core.VOWorkbookError(
                f"Xác minh số dòng {vo_code} PostgreSQL CHƯA ĐỦ: expected={expected_rows:,}, "
                f"scanned={scanned_rows:,}, prepared={prepared_rows:,}, inserted={inserted_rows:,}, "
                f"written={written_rows:,}, failed={failed_rows:,}. "
                "Transaction đã rollback; revision VO trước vẫn được giữ nguyên."
            )
        core._sync_cost_variations(connection, order)

    return {
        "pipeline": PATCH_VERSION,
        "vo_id": vo_id,
        "vo_code": vo_code,
        "revision_no": int(revision_no),
        "batch_id": batch_id,
        "expected_rows": expected_rows,
        "scanned_rows": scanned_rows,
        "prepared_rows": prepared_rows,
        "inserted_rows": inserted_rows,
        "written_rows": written_rows,
        "failed_rows": failed_rows,
        "verification_status": verification_status,
        "verified_postgresql": verification_status == VERIFY_COMPLETE,
        "proposed_amount": float(summary.get("total_after_vat") or 0),
    }
