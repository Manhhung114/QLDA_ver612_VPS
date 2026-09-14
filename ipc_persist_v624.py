from __future__ import annotations

import uuid
from contextlib import contextmanager
from typing import Any, Callable


PATCH_VERSION = "V6.24.3 IPC BATCHED PERSISTENCE"
VERIFY_COMPLETE = "HOÀN TẤT"
VERIFY_INCOMPLETE = "CHƯA ĐỦ"

ProgressFn = Callable[[int, str, str], None]
CancelFn = Callable[[], bool]


@contextmanager
def _atomic(connection):
    """Rollback explicitly because the legacy SQLite context commits in finally."""
    try:
        yield connection
    except BaseException:
        connection.rollback()
        raise


def _batch_size(value: int | None = None) -> int:
    import os

    if value is None:
        try:
            value = int(os.environ.get("QLDA_IPC_DB_BATCH_SIZE", "500") or 500)
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


def _column_names(connection, table: str) -> set[str]:
    cursor = connection.execute(f"SELECT * FROM {table} LIMIT 0")
    description = getattr(cursor, "description", None) or []
    names: set[str] = set()
    for item in description:
        name = getattr(item, "name", None)
        if name is None:
            try:
                name = item[0]
            except Exception:
                name = None
        if name:
            names.add(str(name))
    return names


def save_ipc_result_batched(
    db,
    project_id: int,
    result: dict[str, Any],
    *,
    progress: ProgressFn | None = None,
    cancelled: CancelFn | None = None,
    batch_size: int | None = None,
) -> dict[str, Any]:
    """Atomically save one IPC/revision and verify every detail row in PostgreSQL."""
    import ipc_claim_v622 as ipc

    pid = int(project_id)
    if pid <= 0:
        raise ValueError("project_id IPC không hợp lệ.")
    claim_no = str(result.get("claim_no") or "").strip()
    if not claim_no:
        raise ipc.IPCWorkbookError("IPC chưa có số/kỳ thanh toán.")
    detail_items = list(result.get("detail_items") or [])
    prepared_rows = len(detail_items)
    scanned_rows = int(result.get("detail_line_count") or prepared_rows)
    expected_rows = scanned_rows
    if expected_rows <= 0:
        raise ipc.IPCWorkbookError("IPC CHƯA ĐỦ: không có dòng GTHT chi tiết để ghi PostgreSQL.")
    if scanned_rows != prepared_rows:
        raise ipc.IPCWorkbookError(
            "IPC chưa nhất quán trước khi ghi PostgreSQL: "
            f"scanned={scanned_rows:,}, prepared={prepared_rows:,}."
        )

    claim_code = str(result.get("claim_code") or f"IPC-{claim_no}").strip()
    batch_id = str(result.get("batch_id") or "").strip()
    if not batch_id:
        raise ipc.IPCWorkbookError("IPC thiếu batch_id/SHA của file gốc.")
    filename = str(result.get("filename") or "IPC.xlsx")
    metadata = dict(result.get("metadata") or {})
    summary = dict(result.get("summary") or {})
    payload = ipc._encode_result(result)
    now = ipc._now()
    chunk_size = _batch_size(batch_size)

    inserted_rows = 0
    written_rows = 0
    failed_rows = expected_rows
    verification_status = VERIFY_INCOMPLETE

    with db.connect() as connection, _atomic(connection):
        ipc._ensure_tables(connection)
        claim_columns = _column_names(connection, ipc.CLAIMS_TABLE)
        if "payment_due_date" not in claim_columns:
            connection.execute(
                f"ALTER TABLE {ipc.CLAIMS_TABLE} ADD COLUMN payment_due_date TEXT DEFAULT ''"
            )
            claim_columns.add("payment_due_date")

        if cancelled and cancelled():
            raise InterruptedError("Job IPC đã được yêu cầu hủy trước khi ghi database.")
        old_row = connection.execute(
            f"SELECT * FROM {ipc.CLAIMS_TABLE} WHERE project_id=? AND claim_no=?",
            (pid, claim_no),
        ).fetchone()
        old = _rowdict(old_row)
        claim_id = str(old.get("claim_id") or uuid.uuid4().hex)
        old_batch = str(old.get("batch_id") or "")
        latest_revision = int(old.get("latest_revision") or 0)
        new_revision = latest_revision + 1 if old_batch != batch_id else latest_revision
        if old_row is None:
            new_revision = 0

        claim = {
            "claim_id": claim_id,
            "project_id": pid,
            "claim_no": claim_no,
            "claim_code": claim_code,
            "filename": filename,
            "batch_id": batch_id,
            "contractor": str(metadata.get("contractor") or ""),
            "contract_no": str(metadata.get("contract_no") or ""),
            "package_name": str(metadata.get("package") or ""),
            "from_date": str(metadata.get("from_date") or ""),
            "to_date": str(metadata.get("to_date") or ""),
            "contract_value": float(summary.get("contract_value") or 0),
            "requested_amount": float(summary.get("requested_amount") or 0),
            "approved_amount": float(old.get("approved_amount") or 0),
            "disbursed_amount": float(old.get("disbursed_amount") or 0),
            "certified_cumulative": float(summary.get("cumulative_completed") or summary.get("cumulative_acceptance") or 0),
            "previous_approved": float(summary.get("previous_approved") or 0),
            "retention_cumulative": float(summary.get("cumulative_retention") or 0),
            "advance_amount": float(summary.get("contract_advance") or 0),
            "advance_recovery": float(summary.get("cumulative_advance_recovery") or 0),
            "current_deductions": float(summary.get("current_deductions") or 0),
            "payment_status": str(old.get("payment_status") or "Nháp"),
            "payment_due_date": str(old.get("payment_due_date") or ""),
            "disbursement_date": str(old.get("disbursement_date") or ""),
            "latest_revision": int(new_revision),
            "note": str(old.get("note") or ""),
            "created_at": str(old.get("created_at") or now),
            "updated_at": now,
        }

        connection.execute(f"DELETE FROM {ipc.CLAIMS_TABLE} WHERE claim_id=?", (claim_id,))
        claim_fields = [name for name in claim if name in claim_columns]
        connection.execute(
            f"INSERT INTO {ipc.CLAIMS_TABLE}({','.join(claim_fields)}) "
            f"VALUES({','.join('?' for _ in claim_fields)})",
            tuple(claim[name] for name in claim_fields),
        )

        connection.execute(f"DELETE FROM {ipc.WORKBOOK_TABLE} WHERE claim_id=?", (claim_id,))
        connection.execute(
            f"INSERT INTO {ipc.WORKBOOK_TABLE}(claim_id,project_id,filename,batch_id,payload,updated_at) "
            "VALUES(?,?,?,?,?,?)",
            (claim_id, pid, filename, batch_id, payload, now),
        )
        if old_row is None or old_batch != batch_id:
            connection.execute(
                f"INSERT INTO {ipc.REVISIONS_TABLE}(revision_id,claim_id,project_id,revision_no,filename,batch_id,payload,created_at) "
                "VALUES(?,?,?,?,?,?,?,?)",
                (uuid.uuid4().hex, claim_id, pid, int(new_revision), filename, batch_id, payload, now),
            )

        connection.execute(f"DELETE FROM {ipc.ITEMS_TABLE} WHERE claim_id=?", (claim_id,))
        item_fields = [
            "claim_id", "project_id", "sheet_name", "row_no", "seq", "boq_item", "contract_qty", "unit",
            "spec", "item_code", "brand", "origin", "material_unit_price", "labor_unit_price", "contract_amount",
            "material_previous_qty", "material_current_qty", "material_cumulative_qty", "installation_previous_pct",
            "installation_current_pct", "installation_cumulative_pct", "material_previous_value", "material_current_value",
            "material_cumulative_value", "installation_previous_value", "installation_current_value",
            "installation_cumulative_value", "deduction_previous", "deduction_current", "deduction_cumulative",
            "current_value", "cumulative_value", "completion_ratio", "note", "cost_code", "system",
        ]
        sql = (
            f"INSERT INTO {ipc.ITEMS_TABLE}({','.join(item_fields)}) "
            f"VALUES({','.join('?' for _ in item_fields)})"
        )
        for start in range(0, expected_rows, chunk_size):
            if cancelled and cancelled():
                raise InterruptedError("Job IPC đã được yêu cầu hủy; transaction đã rollback.")
            stop = min(expected_rows, start + chunk_size)
            params = []
            for item in detail_items[start:stop]:
                row = {**item, "claim_id": claim_id, "project_id": pid}
                params.append(tuple(row.get(field, "") for field in item_fields))
            connection.executemany(sql, params)
            inserted_rows = stop
            if progress:
                pct = 76 + int(inserted_rows * 16 / max(1, expected_rows))
                progress(
                    min(pct, 92),
                    f"Đang ghi {claim_code} vào PostgreSQL · {inserted_rows:,}/{expected_rows:,} dòng",
                    claim_code,
                )

        written_rows = _scalar_int(
            connection.execute(
                f"SELECT COUNT(*) AS row_count FROM {ipc.ITEMS_TABLE} WHERE claim_id=?",
                (claim_id,),
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
                f"Xác minh {claim_code} · expected={expected_rows:,} · scanned={scanned_rows:,} · "
                f"written={written_rows:,} · failed={failed_rows:,} · {verification_status}",
                claim_code,
            )
        if not complete:
            raise ipc.IPCWorkbookError(
                f"Xác minh số dòng {claim_code} PostgreSQL CHƯA ĐỦ: "
                f"expected={expected_rows:,}, scanned={scanned_rows:,}, prepared={prepared_rows:,}, "
                f"inserted={inserted_rows:,}, written={written_rows:,}, failed={failed_rows:,}. "
                "Transaction đã rollback; revision IPC trước vẫn được giữ nguyên."
            )

        ipc._sync_payment_tracking(connection, claim)

    return {
        "pipeline": PATCH_VERSION,
        "claim_id": claim_id,
        "claim_no": claim_no,
        "claim_code": claim_code,
        "revision_no": int(new_revision),
        "batch_id": batch_id,
        "expected_rows": expected_rows,
        "scanned_rows": scanned_rows,
        "prepared_rows": prepared_rows,
        "inserted_rows": inserted_rows,
        "written_rows": written_rows,
        "failed_rows": failed_rows,
        "verification_status": verification_status,
        "verified_postgresql": verification_status == VERIFY_COMPLETE,
        "requested_amount": float(summary.get("requested_amount") or 0),
        "certified_cumulative": float(summary.get("cumulative_completed") or summary.get("cumulative_acceptance") or 0),
    }
