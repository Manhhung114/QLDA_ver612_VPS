from __future__ import annotations

from pathlib import Path
from typing import Any

from qlda.infrastructure.database import make_database
from qlda.modules.ipc import background, persistence
from qlda.services.base import (
    CancelFn,
    DbFactory,
    ProgressFn,
    declared_file_size,
    ensure_not_cancelled,
    normalized_filename,
    require_project_id,
)


class IPCService:
    """Application service for parsing and persisting one IPC revision."""

    def __init__(self, db_factory: DbFactory = make_database):
        self._db_factory = db_factory

    def parse_file(
        self,
        path: str | Path,
        filename: str = "IPC.xlsx",
        *,
        progress: ProgressFn | None = None,
        cancelled: CancelFn | None = None,
    ) -> dict[str, Any]:
        return background.parse_ipc_path(
            path,
            normalized_filename(path, filename, "IPC.xlsx"),
            progress=progress,
            cancelled=cancelled,
        )

    def save_result(
        self,
        db: Any,
        project_id: int,
        result: dict[str, Any],
        *,
        progress: ProgressFn | None = None,
        cancelled: CancelFn | None = None,
    ) -> dict[str, Any]:
        return persistence.save_ipc_result_batched(
            db,
            require_project_id(project_id, "project_id IPC"),
            result,
            progress=progress,
            cancelled=cancelled,
        )

    def import_file(
        self,
        project_id: int,
        path: str | Path,
        filename: str = "IPC.xlsx",
        *,
        file_size: int = 0,
        progress: ProgressFn | None = None,
        cancelled: CancelFn | None = None,
    ) -> dict[str, Any]:
        pid = require_project_id(project_id, "project_id IPC")
        name = normalized_filename(path, filename, "IPC.xlsx")
        if progress:
            progress(5, "Service IPC đã nhận file", "")
        result = self.parse_file(path, name, progress=progress, cancelled=cancelled)
        ensure_not_cancelled(cancelled, "Job IPC đã được yêu cầu hủy.")

        db = self._db_factory()
        claim_code = str(result.get("claim_code") or "")
        if progress:
            progress(75, "Đang chuẩn bị ghi IPC vào PostgreSQL", claim_code)
        stats = self.save_result(db, pid, result, progress=progress, cancelled=cancelled)
        ensure_not_cancelled(cancelled, "Job IPC đã được yêu cầu hủy.")
        if progress:
            progress(98, "Đang hoàn tất IPC", str(stats.get("claim_code") or claim_code))

        return {
            "pipeline": "V6.26 IPC service",
            "job_type": "IPC",
            "workspace_project_id": pid,
            "filename": name,
            "file_size": declared_file_size(path, file_size),
            "claim_id": str(stats.get("claim_id") or ""),
            "claim_no": str(stats.get("claim_no") or ""),
            "claim_code": str(stats.get("claim_code") or ""),
            "revision_no": int(stats.get("revision_no") or 0),
            "expected_rows": int(stats.get("expected_rows") or 0),
            "scanned_rows": int(stats.get("scanned_rows") or 0),
            "prepared_rows": int(stats.get("prepared_rows") or 0),
            "inserted_rows": int(stats.get("inserted_rows") or 0),
            "written_rows": int(stats.get("written_rows") or 0),
            "failed_rows": int(stats.get("failed_rows") or 0),
            "verification_status": str(stats.get("verification_status") or "CHƯA ĐỦ"),
            "verified_postgresql": bool(stats.get("verified_postgresql")),
            "requested_amount": float(stats.get("requested_amount") or 0),
            "certified_cumulative": float(stats.get("certified_cumulative") or 0),
            "detail_line_count": int(result.get("detail_line_count") or 0),
            "batch_id": str(stats.get("batch_id") or result.get("batch_id") or ""),
            "source_sha256": str(result.get("source_sha256") or ""),
        }
