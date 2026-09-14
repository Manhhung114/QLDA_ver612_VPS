from __future__ import annotations

import json
import os
import socket
from datetime import datetime, timezone
from typing import Any

PATCH_VERSION = "V6.24 EXCEL JOB QUEUE V1"
ACTIVE_STATES = {"QUEUED", "RUNNING"}
FINAL_STATES = {"DONE", "FAILED", "CANCELLED"}
ALLOWED_JOB_TYPES = {"WORKBOOK_SCAN", "BOQ", "IPC", "VO", "SCHEDULE_EXCEL"}

_SCHEMA = r"""
CREATE TABLE IF NOT EXISTS qlda_excel_jobs (
    id BIGSERIAL PRIMARY KEY,
    project_id BIGINT NOT NULL,
    workspace_project_id BIGINT NOT NULL,
    file_id TEXT NOT NULL,
    file_name TEXT NOT NULL DEFAULT '',
    file_size BIGINT NOT NULL DEFAULT 0,
    file_sha256 TEXT NOT NULL DEFAULT '',
    job_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'QUEUED',
    progress INTEGER NOT NULL DEFAULT 0,
    stage TEXT NOT NULL DEFAULT 'Đang chờ',
    current_sheet TEXT NOT NULL DEFAULT '',
    created_by TEXT NOT NULL DEFAULT '',
    worker_id TEXT NOT NULL DEFAULT '',
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 2,
    cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
    result_summary TEXT NOT NULL DEFAULT '',
    error_message TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    heartbeat_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_qlda_excel_jobs_queue
    ON qlda_excel_jobs(status, created_at, id);
CREATE INDEX IF NOT EXISTS idx_qlda_excel_jobs_workspace
    ON qlda_excel_jobs(workspace_project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_qlda_excel_jobs_dedupe
    ON qlda_excel_jobs(workspace_project_id, job_type, file_sha256, status);

CREATE TABLE IF NOT EXISTS qlda_excel_worker_state (
    worker_id TEXT PRIMARY KEY,
    hostname TEXT NOT NULL DEFAULT '',
    pid INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'IDLE',
    current_job_id BIGINT,
    heartbeat_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    version TEXT NOT NULL DEFAULT ''
);
"""


def _database_url() -> str:
    value = str(
        os.environ.get("DATABASE_URL")
        or os.environ.get("QLDA_DATABASE_URL")
        or os.environ.get("POSTGRES_URL")
        or ""
    ).strip()
    if value.startswith("postgres://"):
        value = "postgresql://" + value[len("postgres://"):]
    if not value:
        raise RuntimeError("DATABASE_URL đang trống; Excel Job Queue cần PostgreSQL.")
    return value


def _connect(*, autocommit: bool = False):
    try:
        import psycopg
        from psycopg.rows import dict_row
    except Exception as exc:
        raise RuntimeError("Thiếu psycopg để dùng Excel Job Queue.") from exc
    return psycopg.connect(_database_url(), autocommit=autocommit, row_factory=dict_row)


def ensure_schema() -> None:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(_SCHEMA)
        conn.commit()


def _public(row: Any) -> dict[str, Any]:
    if not row:
        return {}
    data = dict(row)
    for key, value in list(data.items()):
        if isinstance(value, datetime):
            try:
                data[key] = value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
            except Exception:
                data[key] = value.isoformat()
    return data


def enqueue_job(
    *,
    project_id: int,
    workspace_project_id: int,
    file_id: str,
    job_type: str,
    created_by: str = "",
    max_attempts: int = 2,
) -> dict[str, Any]:
    """Queue one already-stored VPS Excel file without loading its bytes into Streamlit."""
    ensure_schema()
    kind = str(job_type or "WORKBOOK_SCAN").strip().upper()
    if kind not in ALLOWED_JOB_TYPES:
        raise ValueError(f"Loại Excel job không hỗ trợ: {kind}")

    from local_vps_backend_v622 import get_file_row

    file_row = get_file_row(str(file_id))
    name = str(file_row.get("name") or "workbook.xlsx")
    size = int(file_row.get("size") or 0)
    sha = str(file_row.get("sha256") or "")
    if not sha:
        raise ValueError("File VPS thiếu SHA256; không thể tạo Excel job an toàn.")

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT * FROM qlda_excel_jobs
                   WHERE workspace_project_id=%s AND job_type=%s AND file_sha256=%s
                     AND status IN ('QUEUED','RUNNING')
                   ORDER BY id DESC LIMIT 1""",
                (int(workspace_project_id), kind, sha),
            )
            existing = cur.fetchone()
            if existing:
                return _public(existing)
            cur.execute(
                """INSERT INTO qlda_excel_jobs(
                       project_id,workspace_project_id,file_id,file_name,file_size,file_sha256,
                       job_type,status,progress,stage,created_by,max_attempts
                   ) VALUES(%s,%s,%s,%s,%s,%s,%s,'QUEUED',0,'Đang chờ',%s,%s)
                   RETURNING *""",
                (
                    int(project_id), int(workspace_project_id), str(file_id), name, size, sha,
                    kind, str(created_by or ""), max(1, min(int(max_attempts), 5)),
                ),
            )
            row = cur.fetchone()
        conn.commit()
    return _public(row)


def claim_next_job(worker_id: str) -> dict[str, Any] | None:
    """Atomically claim one job; SKIP LOCKED allows future multi-worker scaling."""
    ensure_schema()
    wid = str(worker_id or "worker").strip()[:160]
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id FROM qlda_excel_jobs
                   WHERE status='QUEUED' AND cancel_requested=FALSE AND attempts < max_attempts
                   ORDER BY created_at,id
                   FOR UPDATE SKIP LOCKED LIMIT 1"""
            )
            picked = cur.fetchone()
            if not picked:
                conn.commit()
                return None
            job_id = int(picked["id"])
            cur.execute(
                """UPDATE qlda_excel_jobs
                   SET status='RUNNING',progress=GREATEST(progress,1),stage='Đang khởi tạo',
                       worker_id=%s,attempts=attempts+1,started_at=COALESCE(started_at,NOW()),
                       heartbeat_at=NOW(),error_message=''
                   WHERE id=%s RETURNING *""",
                (wid, job_id),
            )
            row = cur.fetchone()
        conn.commit()
    return _public(row)


def update_progress(job_id: int, progress: int, stage: str, current_sheet: str = "") -> None:
    pct = max(0, min(int(progress), 99))
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE qlda_excel_jobs SET progress=%s,stage=%s,current_sheet=%s,heartbeat_at=NOW()
                   WHERE id=%s AND status='RUNNING'""",
                (pct, str(stage or "Đang xử lý")[:240], str(current_sheet or "")[:240], int(job_id)),
            )
        conn.commit()


def complete_job(job_id: int, summary: dict[str, Any] | None = None) -> None:
    payload = json.dumps(summary or {}, ensure_ascii=False, separators=(",", ":"))
    if len(payload) > 64000:
        payload = payload[:64000]
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE qlda_excel_jobs
                   SET status='DONE',progress=100,stage='Hoàn thành',current_sheet='',
                       result_summary=%s,error_message='',heartbeat_at=NOW(),finished_at=NOW()
                   WHERE id=%s""",
                (payload, int(job_id)),
            )
        conn.commit()


def fail_job(job_id: int, error: str, *, retry: bool = True) -> None:
    message = str(error or "Lỗi xử lý Excel")[:4000]
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT attempts,max_attempts,cancel_requested FROM qlda_excel_jobs WHERE id=%s", (int(job_id),))
            row = cur.fetchone()
            if not row:
                return
            if bool(row.get("cancel_requested")):
                status, stage = "CANCELLED", "Đã hủy"
            elif retry and int(row.get("attempts") or 0) < int(row.get("max_attempts") or 1):
                status, stage = "QUEUED", "Chờ thử lại"
            else:
                status, stage = "FAILED", "Thất bại"
            cur.execute(
                """UPDATE qlda_excel_jobs
                   SET status=%s,stage=%s,error_message=%s,worker_id='',current_sheet='',
                       heartbeat_at=NOW(),finished_at=CASE WHEN %s IN ('FAILED','CANCELLED') THEN NOW() ELSE NULL END
                   WHERE id=%s""",
                (status, stage, message, status, int(job_id)),
            )
        conn.commit()


def cancel_requested(job_id: int) -> bool:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT cancel_requested FROM qlda_excel_jobs WHERE id=%s", (int(job_id),))
            row = cur.fetchone()
    return bool(row and row.get("cancel_requested"))


def request_cancel(job_id: int) -> None:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE qlda_excel_jobs SET cancel_requested=TRUE,
                   status=CASE WHEN status='QUEUED' THEN 'CANCELLED' ELSE status END,
                   stage=CASE WHEN status='QUEUED' THEN 'Đã hủy' ELSE stage END,
                   finished_at=CASE WHEN status='QUEUED' THEN NOW() ELSE finished_at END
                   WHERE id=%s AND status IN ('QUEUED','RUNNING')""",
                (int(job_id),),
            )
        conn.commit()


def list_jobs(workspace_project_id: int | None = None, *, limit: int = 50) -> list[dict[str, Any]]:
    ensure_schema()
    count = max(1, min(int(limit), 500))
    with _connect() as conn:
        with conn.cursor() as cur:
            if workspace_project_id is None:
                cur.execute("SELECT * FROM qlda_excel_jobs ORDER BY id DESC LIMIT %s", (count,))
            else:
                cur.execute(
                    "SELECT * FROM qlda_excel_jobs WHERE workspace_project_id=%s ORDER BY id DESC LIMIT %s",
                    (int(workspace_project_id), count),
                )
            rows = cur.fetchall()
    return [_public(row) for row in rows]


def get_job(job_id: int) -> dict[str, Any] | None:
    ensure_schema()
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM qlda_excel_jobs WHERE id=%s", (int(job_id),))
            row = cur.fetchone()
    return _public(row) if row else None


def worker_heartbeat(worker_id: str, *, status: str, current_job_id: int | None = None) -> None:
    wid = str(worker_id or "worker")[:160]
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO qlda_excel_worker_state(worker_id,hostname,pid,status,current_job_id,heartbeat_at,version)
                   VALUES(%s,%s,%s,%s,%s,NOW(),%s)
                   ON CONFLICT(worker_id) DO UPDATE SET hostname=EXCLUDED.hostname,pid=EXCLUDED.pid,
                       status=EXCLUDED.status,current_job_id=EXCLUDED.current_job_id,
                       heartbeat_at=NOW(),version=EXCLUDED.version""",
                (wid, socket.gethostname(), os.getpid(), str(status or "IDLE")[:80], current_job_id, PATCH_VERSION),
            )
        conn.commit()


def worker_states() -> list[dict[str, Any]]:
    ensure_schema()
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM qlda_excel_worker_state ORDER BY heartbeat_at DESC")
            rows = cur.fetchall()
    return [_public(row) for row in rows]


def memory_used_percent() -> float:
    """Linux memory pressure without adding psutil as a production dependency."""
    try:
        values: dict[str, int] = {}
        with open("/proc/meminfo", "r", encoding="utf-8") as fh:
            for line in fh:
                name, raw = line.split(":", 1)
                values[name] = int(raw.strip().split()[0])
        total = float(values.get("MemTotal") or 0)
        available = float(values.get("MemAvailable") or 0)
        if total > 0:
            return max(0.0, min(100.0, (total - available) * 100.0 / total))
    except Exception:
        pass
    return 0.0


def resource_limits() -> dict[str, float]:
    try:
        hold = float(os.environ.get("QLDA_EXCEL_RAM_HOLD_PERCENT", "75"))
    except Exception:
        hold = 75.0
    try:
        pause = float(os.environ.get("QLDA_EXCEL_RAM_PAUSE_PERCENT", "85"))
    except Exception:
        pause = 85.0
    hold = max(50.0, min(90.0, hold))
    pause = max(hold + 1.0, min(97.0, pause))
    return {"hold_percent": hold, "pause_percent": pause}


def queue_stats() -> dict[str, Any]:
    ensure_schema()
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT status,COUNT(*) AS n FROM qlda_excel_jobs GROUP BY status")
            counts = {str(row["status"]): int(row["n"] or 0) for row in cur.fetchall()}
    return {
        "version": PATCH_VERSION,
        "counts": counts,
        "memory_used_percent": round(memory_used_percent(), 1),
        "limits": resource_limits(),
        "workers": worker_states(),
    }
