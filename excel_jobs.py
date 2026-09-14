from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any

PATCH_VERSION = "V6.24 EXCEL BACKGROUND JOBS V1"
VALID_STATUSES = {"QUEUED", "RUNNING", "DONE", "FAILED", "CANCELLED"}
VALID_JOB_TYPES = {"BOQ_IMPORT"}
PURPOSE_PREFIX = "QLDA_EXCEL_JOB"

_SCHEMA = r"""
CREATE TABLE IF NOT EXISTS qlda_excel_jobs (
    id BIGSERIAL PRIMARY KEY,
    project_id BIGINT NOT NULL,
    file_id TEXT NOT NULL,
    source_name TEXT NOT NULL DEFAULT '',
    source_sha256 TEXT NOT NULL DEFAULT '',
    job_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'QUEUED',
    progress INTEGER NOT NULL DEFAULT 0,
    current_step TEXT NOT NULL DEFAULT 'Chờ xử lý',
    options_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    result_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_message TEXT NOT NULL DEFAULT '',
    dedupe_key TEXT NOT NULL DEFAULT '',
    created_by TEXT NOT NULL DEFAULT '',
    worker_id TEXT NOT NULL DEFAULT '',
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    heartbeat_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_qlda_excel_jobs_queue
    ON qlda_excel_jobs(status, created_at, id);
CREATE INDEX IF NOT EXISTS idx_qlda_excel_jobs_project
    ON qlda_excel_jobs(project_id, job_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_qlda_excel_jobs_file
    ON qlda_excel_jobs(file_id, job_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_qlda_excel_jobs_dedupe
    ON qlda_excel_jobs(dedupe_key, status);
"""


def _database_url() -> str:
    value = (
        str(os.environ.get("DATABASE_URL", "") or "").strip()
        or str(os.environ.get("QLDA_DATABASE_URL", "") or "").strip()
        or str(os.environ.get("POSTGRES_URL", "") or "").strip()
    )
    if value.startswith("postgres://"):
        value = "postgresql://" + value[len("postgres://"):]
    if not value:
        raise RuntimeError("Chưa cấu hình DATABASE_URL cho Excel background worker.")
    return value


def _connect(*, autocommit: bool = False):
    try:
        import psycopg
        from psycopg.rows import dict_row
    except Exception as exc:
        raise RuntimeError("Thiếu psycopg cho Excel background worker.") from exc
    return psycopg.connect(_database_url(), autocommit=autocommit, row_factory=dict_row)


def ensure_schema() -> None:
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(_SCHEMA)
        conn.commit()


def _clean_json(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))


def _iso(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        try:
            return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        except Exception:
            return value.isoformat()
    return str(value)


def _public_job(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {}
    out = dict(row)
    for key in ("created_at", "started_at", "heartbeat_at", "finished_at"):
        out[key] = _iso(out.get(key))
    out["progress"] = max(0, min(100, int(out.get("progress") or 0)))
    options = out.get("options_json")
    result = out.get("result_json")
    if isinstance(options, str):
        try:
            options = json.loads(options)
        except Exception:
            options = {}
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except Exception:
            result = {}
    out["options"] = dict(options or {}) if isinstance(options, dict) else {}
    out["result"] = dict(result or {}) if isinstance(result, dict) else {}
    out.pop("options_json", None)
    out.pop("result_json", None)
    return out


def build_upload_purpose(job_type: str, project_id: int, **options: Any) -> str:
    job = str(job_type or "").strip().upper()
    if job not in VALID_JOB_TYPES:
        raise ValueError(f"Loại Excel job chưa hỗ trợ: {job}")
    pid = int(project_id)
    if pid <= 0:
        raise ValueError("project_id không hợp lệ.")
    clean = _clean_json(options)
    compact = json.dumps(clean, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    purpose = f"{PURPOSE_PREFIX}|{job}|{pid}|{compact}"
    if len(purpose) > 190:
        raise ValueError("Tùy chọn Excel job quá dài cho upload ticket.")
    return purpose


def parse_upload_purpose(value: str) -> dict[str, Any] | None:
    text = str(value or "").strip()
    prefix = PURPOSE_PREFIX + "|"
    if not text.startswith(prefix):
        return None
    parts = text.split("|", 3)
    if len(parts) != 4:
        raise ValueError("Upload purpose của Excel job không hợp lệ.")
    _, job_type, project_id, options_raw = parts
    job_type = job_type.strip().upper()
    if job_type not in VALID_JOB_TYPES:
        raise ValueError(f"Loại Excel job chưa hỗ trợ: {job_type}")
    pid = int(project_id)
    if pid <= 0:
        raise ValueError("project_id của Excel job không hợp lệ.")
    try:
        options = json.loads(options_raw or "{}")
    except Exception as exc:
        raise ValueError("Tùy chọn Excel job không phải JSON hợp lệ.") from exc
    if not isinstance(options, dict):
        options = {}
    return {"job_type": job_type, "project_id": pid, "options": _clean_json(options)}


def _dedupe_key(project_id: int, job_type: str, source_sha256: str, options: dict[str, Any]) -> str:
    payload = {
        "project_id": int(project_id),
        "job_type": str(job_type).strip().upper(),
        "sha256": str(source_sha256 or "").strip().lower(),
        "options": _clean_json(options),
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()


def enqueue_file_job(
    *,
    file_id: str,
    project_id: int,
    job_type: str,
    options: dict[str, Any] | None = None,
    created_by: str = "",
) -> dict[str, Any]:
    """Queue one already-persisted VPS file for background Excel processing."""
    from local_vps_backend_v622 import get_file_row

    ensure_schema()
    pid = int(project_id)
    if pid <= 0:
        raise ValueError("project_id không hợp lệ.")
    kind = str(job_type or "").strip().upper()
    if kind not in VALID_JOB_TYPES:
        raise ValueError(f"Loại Excel job chưa hỗ trợ: {kind}")
    row = get_file_row(str(file_id or ""))
    sha = str(row.get("sha256") or "").strip().lower()
    name = str(row.get("name") or "Excel.xlsx")
    opts = _clean_json(options or {})
    dedupe = _dedupe_key(pid, kind, sha, opts)

    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT * FROM qlda_excel_jobs
                   WHERE dedupe_key=%s AND status IN ('QUEUED','RUNNING','DONE')
                   ORDER BY id DESC LIMIT 1""",
                (dedupe,),
            )
            existing = cur.fetchone()
            if existing:
                out = _public_job(dict(existing))
                out["reused"] = True
                return out
            cur.execute(
                """INSERT INTO qlda_excel_jobs(
                       project_id,file_id,source_name,source_sha256,job_type,status,progress,current_step,
                       options_json,result_json,error_message,dedupe_key,created_by
                   ) VALUES(%s,%s,%s,%s,%s,'QUEUED',0,'Chờ worker xử lý',%s::jsonb,'{}'::jsonb,'',%s,%s)
                   RETURNING *""",
                (
                    pid,
                    str(file_id),
                    name,
                    sha,
                    kind,
                    json.dumps(opts, ensure_ascii=False, separators=(",", ":")),
                    dedupe,
                    str(created_by or "").strip().lower(),
                ),
            )
            created = dict(cur.fetchone())
        conn.commit()
    out = _public_job(created)
    out["reused"] = False
    return out


def enqueue_from_upload_purpose(upload_purpose: str, *, file_id: str, created_by: str = "") -> dict[str, Any] | None:
    meta = parse_upload_purpose(upload_purpose)
    if not meta:
        return None
    return enqueue_file_job(
        file_id=file_id,
        project_id=int(meta["project_id"]),
        job_type=str(meta["job_type"]),
        options=dict(meta.get("options") or {}),
        created_by=created_by,
    )


def list_project_jobs(project_id: int, *, job_type: str = "", limit: int = 10) -> list[dict[str, Any]]:
    ensure_schema()
    pid = int(project_id)
    limit_i = max(1, min(int(limit), 100))
    with _connect() as conn:
        with conn.cursor() as cur:
            if str(job_type or "").strip():
                cur.execute(
                    """SELECT * FROM qlda_excel_jobs
                       WHERE project_id=%s AND job_type=%s
                       ORDER BY id DESC LIMIT %s""",
                    (pid, str(job_type).strip().upper(), limit_i),
                )
            else:
                cur.execute(
                    "SELECT * FROM qlda_excel_jobs WHERE project_id=%s ORDER BY id DESC LIMIT %s",
                    (pid, limit_i),
                )
            rows = [dict(x) for x in cur.fetchall()]
    return [_public_job(row) for row in rows]


def claim_next_job(worker_id: str) -> dict[str, Any] | None:
    ensure_schema()
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """WITH picked AS (
                       SELECT id FROM qlda_excel_jobs
                       WHERE status='QUEUED'
                       ORDER BY created_at, id
                       FOR UPDATE SKIP LOCKED
                       LIMIT 1
                   )
                   UPDATE qlda_excel_jobs j
                   SET status='RUNNING', progress=GREATEST(j.progress,1),
                       current_step='Worker đã nhận tác vụ',
                       worker_id=%s, attempts=j.attempts+1,
                       started_at=COALESCE(j.started_at,NOW()), heartbeat_at=NOW(),
                       error_message=''
                   FROM picked
                   WHERE j.id=picked.id
                   RETURNING j.*""",
                (str(worker_id or "worker"),),
            )
            row = cur.fetchone()
        conn.commit()
    return _public_job(dict(row)) if row else None


def update_job(job_id: int, *, progress: int, current_step: str) -> None:
    ensure_schema()
    pct = max(0, min(99, int(progress)))
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE qlda_excel_jobs
                   SET progress=%s,current_step=%s,heartbeat_at=NOW()
                   WHERE id=%s AND status='RUNNING'""",
                (pct, str(current_step or "Đang xử lý")[:300], int(job_id)),
            )
        conn.commit()


def heartbeat_job(job_id: int) -> None:
    ensure_schema()
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE qlda_excel_jobs SET heartbeat_at=NOW() WHERE id=%s AND status='RUNNING'",
                (int(job_id),),
            )
        conn.commit()


def complete_job(job_id: int, result: dict[str, Any] | None = None) -> dict[str, Any]:
    ensure_schema()
    payload = _clean_json(result or {})
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE qlda_excel_jobs
                   SET status='DONE',progress=100,current_step='Hoàn thành',result_json=%s::jsonb,
                       error_message='',heartbeat_at=NOW(),finished_at=NOW()
                   WHERE id=%s RETURNING *""",
                (json.dumps(payload, ensure_ascii=False, separators=(",", ":")), int(job_id)),
            )
            row = cur.fetchone()
        conn.commit()
    return _public_job(dict(row)) if row else {}


def fail_job(job_id: int, error: Any) -> dict[str, Any]:
    ensure_schema()
    message = str(error or "Lỗi không xác định")[:4000]
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE qlda_excel_jobs
                   SET status='FAILED',current_step='Xử lý thất bại',error_message=%s,
                       heartbeat_at=NOW(),finished_at=NOW()
                   WHERE id=%s RETURNING *""",
                (message, int(job_id)),
            )
            row = cur.fetchone()
        conn.commit()
    return _public_job(dict(row)) if row else {}


def requeue_stale_jobs(*, stale_minutes: int = 20, max_attempts: int = 3) -> int:
    ensure_schema()
    minutes = max(5, min(int(stale_minutes), 24 * 60))
    attempts = max(1, min(int(max_attempts), 10))
    with _connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """UPDATE qlda_excel_jobs
                   SET status='QUEUED',current_step='Khôi phục sau khi worker gián đoạn',worker_id='',
                       started_at=NULL,heartbeat_at=NULL,error_message=''
                   WHERE status='RUNNING' AND attempts < %s
                     AND COALESCE(heartbeat_at,started_at,created_at) < NOW() - (%s * INTERVAL '1 minute')""",
                (attempts, minutes),
            )
            count = int(cur.rowcount or 0)
            cur.execute(
                """UPDATE qlda_excel_jobs
                   SET status='FAILED',current_step='Dừng sau nhiều lần worker gián đoạn',
                       error_message='Worker bị gián đoạn quá số lần cho phép',finished_at=NOW()
                   WHERE status='RUNNING' AND attempts >= %s
                     AND COALESCE(heartbeat_at,started_at,created_at) < NOW() - (%s * INTERVAL '1 minute')""",
                (attempts, minutes),
            )
        conn.commit()
    return count
