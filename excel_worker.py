from __future__ import annotations

import gc
import os
import signal
import socket
import sys
import time
import traceback
import threading
from pathlib import Path
from typing import Any

from excel_jobs import (
    PATCH_VERSION,
    claim_next_job,
    complete_job,
    fail_job,
    heartbeat_job,
    requeue_stale_jobs,
    update_job,
)

_STOP = False


def _on_signal(signum, _frame):
    global _STOP
    _STOP = True
    print(f"Excel worker received signal {signum}; stopping safely.", flush=True)


def _memory_used_percent() -> float:
    try:
        values: dict[str, int] = {}
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            key, rest = line.split(":", 1)
            values[key] = int(rest.strip().split()[0])
        total = float(values.get("MemTotal", 0))
        available = float(values.get("MemAvailable", 0))
        if total <= 0:
            return 0.0
        return max(0.0, min(100.0, (total - available) * 100.0 / total))
    except Exception:
        return 0.0


def _make_db():
    import postgres_backend_v622 as pg

    pg.install_postgres_backend()
    from cloud_db import CloudDatabase

    return CloudDatabase(Path(os.environ.get("QLDA_WORKER_DB_LABEL", "/opt/qlda/shared/qlda-worker.db")))


def _process_boq(job: dict[str, Any]) -> dict[str, Any]:
    from local_vps_backend_v622 import local_file_path

    # Install the same BOQ semantics used by the web process before parsing.
    from boq_cost_components_v622 import install_boq_cost_components
    from boq_claim_terms_v622 import install_boq_claim_terms
    from multicore_excel_v622 import install_multicore_excel

    install_boq_cost_components()
    install_boq_claim_terms()
    install_multicore_excel()

    import boq_multisheet_v622 as boq
    from boq_persistence_v622 import save_saved_boq_workbook

    job_id = int(job["id"])
    update_job(job_id, progress=5, current_step="Đang mở file BOQ từ ổ đĩa VPS")
    row, path = local_file_path(str(job.get("file_id") or ""))
    if not path.exists():
        raise FileNotFoundError(f"File nguồn không còn trên VPS: {path}")

    # V6.24.1 moves workbook memory away from Streamlit first. V6.24.2 will
    # replace this worker-local bytes read with row streaming/batched persistence.
    size = int(path.stat().st_size)
    max_worker_mb = int(os.environ.get("QLDA_EXCEL_WORKER_MAX_FILE_MB", "512") or 512)
    if size > max(10, max_worker_mb) * 1024 * 1024:
        raise ValueError(f"File {size/1024/1024:.1f} MB vượt giới hạn worker {max_worker_mb} MB.")

    update_job(job_id, progress=12, current_step=f"Đang đọc {size/1024/1024:.1f} MB trong worker riêng")
    data = path.read_bytes()
    update_job(job_id, progress=25, current_step="Đang phân tích cấu trúc workbook BOQ")
    result = boq.parse_boq_workbook(data, str(row.get("name") or job.get("source_name") or "BOQ.xlsx"))
    del data
    gc.collect()

    update_job(job_id, progress=72, current_step="Đang lưu BOQ vào PostgreSQL")
    db = _make_db()
    options = dict(job.get("options") or {})
    stats = boq.save_boq_summary_to_project(
        db,
        int(job["project_id"]),
        result,
        replace_existing_excel=bool(options.get("replace_existing_excel", True)),
    )
    update_job(job_id, progress=90, current_step="Đang lưu snapshot workbook dùng chung")
    save_saved_boq_workbook(db, int(job["project_id"]), result)

    summary = {
        "job_type": "BOQ_IMPORT",
        "filename": str(result.get("filename") or row.get("name") or "BOQ.xlsx"),
        "inserted": int(stats.get("inserted") or 0),
        "deleted": int(stats.get("deleted") or 0),
        "detail_line_count": int(result.get("detail_line_count") or 0),
        "before_tax_total": float(stats.get("before_tax_total") or 0),
        "after_tax_total": float(stats.get("after_tax_total") or 0),
        "batch_id": str(stats.get("batch_id") or result.get("batch_id") or ""),
    }
    del result
    gc.collect()
    return summary


class _Heartbeat:
    def __init__(self, job_id: int, seconds: float = 30.0):
        self.job_id = int(job_id)
        self.seconds = max(10.0, float(seconds))
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True, name=f"excel-heartbeat-{job_id}")

    def _run(self):
        while not self.stop_event.wait(self.seconds):
            try:
                heartbeat_job(self.job_id)
            except Exception as exc:
                print(f"Excel heartbeat warning job #{self.job_id}: {exc}", flush=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, exc_type, exc, tb):
        self.stop_event.set()
        self.thread.join(timeout=2.0)
        return False


def process_job(job: dict[str, Any]) -> dict[str, Any]:
    job_type = str(job.get("job_type") or "").strip().upper()
    if job_type == "BOQ_IMPORT":
        return _process_boq(job)
    raise ValueError(f"Worker chưa hỗ trợ job type {job_type!r}.")


def main() -> int:
    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    poll_seconds = max(1.0, float(os.environ.get("QLDA_EXCEL_WORKER_POLL_SECONDS", "3") or 3))
    pause_pct = max(70.0, min(95.0, float(os.environ.get("QLDA_EXCEL_WORKER_PAUSE_RAM_PCT", "85") or 85)))
    print(f"{PATCH_VERSION} worker={worker_id} poll={poll_seconds}s pause_ram={pause_pct:.0f}%", flush=True)

    try:
        requeue_stale_jobs()
    except Exception as exc:
        print(f"Warning: cannot requeue stale Excel jobs: {exc}", flush=True)

    while not _STOP:
        used = _memory_used_percent()
        if used >= pause_pct:
            print(f"Excel worker paused: RAM {used:.1f}% >= {pause_pct:.1f}%", flush=True)
            time.sleep(max(5.0, poll_seconds))
            continue
        try:
            job = claim_next_job(worker_id)
        except Exception as exc:
            print(f"Excel worker queue error: {exc}", flush=True)
            time.sleep(max(5.0, poll_seconds))
            continue
        if not job:
            time.sleep(poll_seconds)
            continue

        job_id = int(job["id"])
        print(
            f"Excel job #{job_id} started type={job.get('job_type')} file={job.get('source_name')}",
            flush=True,
        )
        try:
            with _Heartbeat(job_id):
                result = process_job(job)
            complete_job(job_id, result)
            print(f"Excel job #{job_id} DONE: {result}", flush=True)
        except Exception as exc:
            fail_job(job_id, f"{type(exc).__name__}: {exc}")
            print(f"Excel job #{job_id} FAILED: {exc}", file=sys.stderr, flush=True)
            traceback.print_exc()

        # One heavy job per process. Let systemd start a fresh Python process so
        # CPython/openpyxl arenas are returned to the OS instead of accumulating.
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
