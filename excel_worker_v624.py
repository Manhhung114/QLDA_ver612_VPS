from __future__ import annotations

import argparse
import os
import signal
import socket
import time
from pathlib import Path
from typing import Any, Callable

from excel_jobs_v624 import (
    PATCH_VERSION,
    cancel_requested,
    claim_next_job,
    complete_job,
    ensure_schema,
    fail_job,
    memory_used_percent,
    resource_limits,
    update_progress,
    worker_heartbeat,
)

_STOP = False


def _stop(*_args) -> None:
    global _STOP
    _STOP = True


def _worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def scan_workbook_path(
    path: str | Path,
    *,
    progress: Callable[[int, str, str], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Read an xlsx/xlsm from disk in read-only mode without loading file bytes."""
    from openpyxl import load_workbook

    source = Path(path)
    if source.suffix.lower() not in {".xlsx", ".xlsm"}:
        raise ValueError("Background Excel V6.24.1 hiện hỗ trợ .xlsx/.xlsm.")
    if not source.exists() or not source.is_file():
        raise FileNotFoundError(f"Không tìm thấy file Excel trên VPS: {source}")

    if progress:
        progress(5, "Đang mở workbook từ ổ đĩa", "")
    wb = load_workbook(str(source), read_only=True, data_only=True, keep_links=False)
    try:
        sheets = list(wb.worksheets)
        total = max(1, len(sheets))
        result_sheets: list[dict[str, Any]] = []
        for index, ws in enumerate(sheets, start=1):
            if cancelled and cancelled():
                raise InterruptedError("Job đã được yêu cầu hủy.")
            base = 10 + int((index - 1) * 80 / total)
            if progress:
                progress(base, "Đang quét cấu trúc workbook", ws.title)

            nonempty_rows = 0
            nonempty_cells = 0
            sample_rows: list[list[str]] = []
            # iter_rows(values_only=True) streams worksheet XML. Keep only a tiny
            # text sample for diagnostics; business data is never accumulated here.
            for row_no, values in enumerate(ws.iter_rows(values_only=True), start=1):
                if cancelled and row_no % 500 == 0 and cancelled():
                    raise InterruptedError("Job đã được yêu cầu hủy.")
                used = [value for value in values if value not in (None, "")]
                if used:
                    nonempty_rows += 1
                    nonempty_cells += len(used)
                    if len(sample_rows) < 5:
                        sample_rows.append([str(value)[:120] for value in values[:12]])
            result_sheets.append(
                {
                    "name": str(ws.title),
                    "max_row": int(getattr(ws, "max_row", 0) or 0),
                    "max_column": int(getattr(ws, "max_column", 0) or 0),
                    "nonempty_rows": nonempty_rows,
                    "nonempty_cells": nonempty_cells,
                    "sample_rows": sample_rows,
                }
            )
            if progress:
                progress(10 + int(index * 80 / total), "Đang quét cấu trúc workbook", ws.title)

        if progress:
            progress(95, "Đang hoàn tất kiểm tra workbook", "")
        return {
            "pipeline": "V6.24.1 background disk-stream scan",
            "file_name": source.name,
            "file_size": int(source.stat().st_size),
            "sheet_count": len(result_sheets),
            "sheets": result_sheets,
        }
    finally:
        wb.close()


def _process_job(job: dict[str, Any]) -> dict[str, Any]:
    job_id = int(job["id"])
    job_type = str(job.get("job_type") or "").upper()
    from local_vps_backend_v622 import local_file_path

    _row, path = local_file_path(str(job.get("file_id") or ""))

    def report(percent: int, stage: str, sheet: str = "") -> None:
        update_progress(job_id, percent, stage, sheet)

    def is_cancelled() -> bool:
        return _STOP or cancel_requested(job_id)

    # V6.24.1 intentionally activates the infrastructure with a safe scan job.
    # BOQ/IPC/VO business writes are enabled in the following migration steps so
    # production calculations remain unchanged while the queue is proven stable.
    if job_type != "WORKBOOK_SCAN":
        raise RuntimeError(
            f"Pipeline {job_type} chưa được bật ở V6.24.1. "
            "Hạ tầng queue/worker đã sẵn sàng; business parser sẽ được chuyển theo từng bước."
        )
    return scan_workbook_path(path, progress=report, cancelled=is_cancelled)


def run_once(worker_id: str) -> bool:
    limits = resource_limits()
    ram = memory_used_percent()
    if ram >= float(limits["hold_percent"]):
        state = "PAUSED_RAM" if ram >= float(limits["pause_percent"]) else "HOLD_RAM"
        worker_heartbeat(worker_id, status=f"{state}:{ram:.1f}%")
        return False

    job = claim_next_job(worker_id)
    if not job:
        worker_heartbeat(worker_id, status="IDLE")
        return False

    job_id = int(job["id"])
    worker_heartbeat(worker_id, status="RUNNING", current_job_id=job_id)
    try:
        summary = _process_job(job)
        if cancel_requested(job_id) or _STOP:
            fail_job(job_id, "Job đã được yêu cầu hủy.", retry=False)
        else:
            complete_job(job_id, summary)
    except InterruptedError as exc:
        fail_job(job_id, str(exc), retry=False)
    except Exception as exc:
        fail_job(job_id, f"{type(exc).__name__}: {exc}", retry=True)
    finally:
        worker_heartbeat(worker_id, status="IDLE")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="QLDA V6.24 background Excel worker")
    parser.add_argument("--once", action="store_true", help="Process at most one job then exit")
    parser.add_argument("--poll-seconds", type=float, default=float(os.environ.get("QLDA_EXCEL_POLL_SECONDS", "2")))
    args = parser.parse_args()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    ensure_schema()
    wid = _worker_id()
    worker_heartbeat(wid, status="STARTING")
    poll = max(0.5, min(float(args.poll_seconds), 30.0))

    while not _STOP:
        processed = run_once(wid)
        if args.once:
            break
        if not processed:
            time.sleep(poll)

    worker_heartbeat(wid, status="STOPPED")
    print(f"{PATCH_VERSION} worker stopped: {wid}", flush=True)


if __name__ == "__main__":
    main()
