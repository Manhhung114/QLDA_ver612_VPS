from __future__ import annotations

from pathlib import Path
from typing import Any

from qlda.infrastructure.database import make_database
from qlda.modules.vo import background, persistence
from qlda.services.base import (
    CancelFn,
    DbFactory,
    ProgressFn,
    declared_file_size,
    ensure_not_cancelled,
    normalized_filename,
    require_project_id,
)


class VOService:
    """Application service for parsing and persisting one VO revision."""

    def __init__(self, db_factory: DbFactory = make_database):
        self._db_factory = db_factory

    def parse_file(
        self,
        path: str | Path,
        filename: str = "VO.xlsx",
        *,
        progress: ProgressFn | None = None,
        cancelled: CancelFn | None = None,
    ) -> dict[str, Any]:
        return background.parse_vo_path(
            path,
            normalized_filename(path, filename, "VO.xlsx"),
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
        return persistence.save_vo_result_batched(
            db,
            require_project_id(project_id, "project_id VO"),
            result,
            progress=progress,
            cancelled=cancelled,
        )

    def import_file(
        self,
        project_id: int,
        path: str | Path,
        filename: str = "VO.xlsx",
        *,
        file_size: int = 0,
        progress: ProgressFn | None = None,
        cancelled: CancelFn | None = None,
    ) -> dict[str, Any]:
        pid = require_project_id(project_id, "project_id VO")
        name = normalized_filename(path, filename, "VO.xlsx")
        if progress:
            progress(5, "Service VO đã nhận file", "")
        result = self.parse_file(path, name, progress=progress, cancelled=cancelled)
        ensure_not_cancelled(cancelled, "Job VO đã được yêu cầu hủy.")

        db = self._db_factory()
        vo_code = str(result.get("vo_code") or "")
        if progress:
            progress(75, "Đang chuẩn bị ghi VO vào PostgreSQL", vo_code)
        stats = self.save_result(db, pid, result, progress=progress, cancelled=cancelled)
        ensure_not_cancelled(cancelled, "Job VO đã được yêu cầu hủy.")
        if progress:
            progress(98, "Đang hoàn tất VO", str(stats.get("vo_code") or vo_code))

        return {
            "pipeline": "V6.26 VO service",
            "job_type": "VO",
            "workspace_project_id": pid,
            "filename": name,
            "file_size": declared_file_size(path, file_size),
            "vo_id": str(stats.get("vo_id") or ""),
            "vo_code": str(stats.get("vo_code") or ""),
            "revision_no": int(stats.get("revision_no") or 0),
            "expected_rows": int(stats.get("expected_rows") or 0),
            "scanned_rows": int(stats.get("scanned_rows") or 0),
            "prepared_rows": int(stats.get("prepared_rows") or 0),
            "inserted_rows": int(stats.get("inserted_rows") or 0),
            "written_rows": int(stats.get("written_rows") or 0),
            "failed_rows": int(stats.get("failed_rows") or 0),
            "verification_status": str(stats.get("verification_status") or "CHƯA ĐỦ"),
            "verified_postgresql": bool(stats.get("verified_postgresql")),
            "proposed_amount": float(stats.get("proposed_amount") or 0),
            "detail_line_count": int(result.get("detail_line_count") or 0),
            "batch_id": str(stats.get("batch_id") or result.get("batch_id") or ""),
            "source_sha256": str(result.get("source_sha256") or ""),
        }
