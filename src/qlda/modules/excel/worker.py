from __future__ import annotations

import argparse
import gc
import os
import signal
import socket
import time
from pathlib import Path
from typing import Any, Callable

from qlda.bootstrap import get_application, get_database

PATCH_VERSION = "V7.5 NATIVE IMPORT ENGINE WORKER"
_STOP = False


def _stop(*_args: Any) -> None:
    global _STOP
    _STOP = True


def _worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def _make_db():
    return get_database()


def scan_workbook_path(
    path: str | Path,
    *,
    progress: Callable[[int, str, str], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    return get_application().excel.scan_workbook(path, progress=progress, cancelled=cancelled)


def _process_job(job: dict[str, Any]) -> dict[str, Any]:
    services = get_application()
    job_id = int(job["id"])
    file_row, path = services.files.local_path(str(job.get("file_id") or ""))

    def report(percent: int, stage: str, sheet: str = "") -> None:
        services.jobs.update_progress(job_id, percent, stage, sheet)

    def is_cancelled() -> bool:
        return _STOP or services.jobs.cancel_requested(job_id)

    summary = services.excel.process_job(
        job,
        path,
        file_row,
        progress=report,
        cancelled=is_cancelled,
    )
    print(
        f"Excel job #{job_id} service={summary.get('job_type', job.get('job_type'))} "
        f"workspace={summary.get('workspace_project_id', '')} "
        f"written={summary.get('written_rows', summary.get('inserted', ''))} "
        f"status={summary.get('verification_status', 'DONE')}",
        flush=True,
    )
    return summary


def run_once(worker_id: str) -> bool:
    services = get_application()
    limits = services.jobs.resource_limits()
    ram = services.jobs.memory_used_percent()
    if ram >= float(limits["hold_percent"]):
        state = "PAUSED_RAM" if ram >= float(limits["pause_percent"]) else "HOLD_RAM"
        services.jobs.heartbeat(worker_id, status=f"{state}:{ram:.1f}%")
        return False

    job = services.jobs.claim_next(worker_id)
    if not job:
        services.jobs.heartbeat(worker_id, status="IDLE")
        return False

    job_id = int(job["id"])
    services.jobs.heartbeat(worker_id, status="RUNNING", current_job_id=job_id)
    print(f"Excel job #{job_id} started type={job.get('job_type')} file={job.get('file_name')}", flush=True)
    try:
        summary = _process_job(job)
        if services.jobs.cancel_requested(job_id) or _STOP:
            services.jobs.fail(job_id, "Job đã được yêu cầu hủy.", retry=False)
        else:
            services.jobs.complete(job_id, summary)
            print(f"Excel job #{job_id} DONE", flush=True)
    except InterruptedError as exc:
        services.jobs.fail(job_id, str(exc), retry=False)
        print(f"Excel job #{job_id} CANCELLED: {exc}", flush=True)
    except Exception as exc:
        services.jobs.fail(job_id, f"{type(exc).__name__}: {exc}", retry=True)
        print(f"Excel job #{job_id} ERROR: {type(exc).__name__}: {exc}", flush=True)
    finally:
        services.jobs.heartbeat(worker_id, status="IDLE")
        gc.collect()
    return True


def _recycle_after_job() -> bool:
    value = str(os.environ.get("QLDA_EXCEL_RECYCLE_AFTER_JOB", "1") or "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def main() -> None:
    parser = argparse.ArgumentParser(description="QLDA V7.5 native import-engine worker")
    parser.add_argument("--once", action="store_true", help="Process at most one job then exit")
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=float(os.environ.get("QLDA_EXCEL_POLL_SECONDS", "2")),
    )
    args = parser.parse_args()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    services = get_application()
    services.jobs.ensure_schema()
    try:
        recovered = services.jobs.recover_stale()
        if recovered:
            print(f"Recovered {recovered} stale Excel job(s).", flush=True)
    except Exception as exc:
        print(f"Excel stale-job recovery warning: {exc}", flush=True)

    worker_id = _worker_id()
    services.jobs.heartbeat(worker_id, status="STARTING")
    poll = max(0.5, min(float(args.poll_seconds), 30.0))

    while not _STOP:
        processed = run_once(worker_id)
        if args.once:
            break
        if processed and _recycle_after_job():
            break
        if not processed:
            time.sleep(poll)

    services.jobs.heartbeat(worker_id, status="STOPPED")
    print(f"{PATCH_VERSION} worker stopped: {worker_id}", flush=True)


if __name__ == "__main__":
    main()
