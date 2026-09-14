from __future__ import annotations

"""V6.24.5 local file-server entrypoint with Excel queue hand-off.

The upload is streamed directly to SSD first. Only after the durable file row is
registered do we enqueue a lightweight PostgreSQL job. Upload success therefore
never depends on the worker being available at that exact moment.
"""

from runtime_settings_bridge_v622 import install_runtime_settings_bridge
from single_session_v622 import install_local_single_session

install_runtime_settings_bridge()
install_local_single_session()

import local_file_server_v622 as base  # noqa: E402
from local_vps_backend_v622 import verify_upload_ticket  # noqa: E402

SERVER_VERSION = "QLDA-Local-File-Server/6.24.5"
_original_save_stream = base.save_stream_from_ticket


def save_stream_and_enqueue(ticket: str, *, name: str, mime_type: str, stream, content_length: int):
    meta = verify_upload_ticket(ticket)
    item = _original_save_stream(
        ticket,
        name=name,
        mime_type=mime_type,
        stream=stream,
        content_length=content_length,
    )
    purpose = str(meta.get("upload_purpose") or "")
    if purpose.startswith("QLDA_EXCEL_JOB|"):
        try:
            from excel_jobs_v624 import enqueue_from_upload_purpose

            job = enqueue_from_upload_purpose(
                purpose,
                file_id=str(item.get("id") or ""),
                created_by=str(meta.get("email") or ""),
            )
            if job:
                item = dict(item)
                item["excel_job"] = {
                    "id": int(job.get("id") or 0),
                    "status": str(job.get("status") or ""),
                    "reused": bool(job.get("reused")),
                    "version": "V6.24.5",
                }
        except Exception as exc:
            # The original file is already durable. Queue failure must not turn
            # a successful disk upload into data loss. The error is returned to
            # the upload page/API and can be retried after the queue is repaired.
            item = dict(item)
            item["excel_job_error"] = f"{type(exc).__name__}: {exc}"
    return item


base.save_stream_from_ticket = save_stream_and_enqueue
base.SERVER_VERSION = SERVER_VERSION
base.QLDAFileHandler.server_version = SERVER_VERSION


def main():
    return base.main()


if __name__ == "__main__":
    main()
