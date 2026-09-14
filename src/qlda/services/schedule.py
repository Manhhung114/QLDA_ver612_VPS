from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from qlda.infrastructure.database import make_database
from qlda.modules.schedule import background, persistence
from qlda.services.base import (
    CancelFn,
    DbFactory,
    ProgressFn,
    declared_file_size,
    ensure_not_cancelled,
    normalized_filename,
    options_dict,
    require_project_id,
)


class ScheduleService:
    """Application service for Excel schedule imports."""

    def __init__(self, db_factory: DbFactory = make_database):
        self._db_factory = db_factory

    def parse_file(
        self,
        path: str | Path,
        filename: str = "TienDo.xlsx",
        *,
        status_date: Any = None,
        progress: ProgressFn | None = None,
        cancelled: CancelFn | None = None,
    ) -> dict[str, Any]:
        return background.parse_schedule_excel_path(
            path,
            normalized_filename(path, filename, "TienDo.xlsx"),
            status_date=status_date,
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
        return persistence.save_schedule_result_batched(
            db,
            require_project_id(project_id, "project_id tiến độ"),
            result,
            progress=progress,
            cancelled=cancelled,
        )

    def import_file(
        self,
        project_id: int,
        path: str | Path,
        filename: str = "TienDo.xlsx",
        *,
        file_size: int = 0,
        options: Mapping[str, Any] | None = None,
        progress: ProgressFn | None = None,
        cancelled: CancelFn | None = None,
    ) -> dict[str, Any]:
        pid = require_project_id(project_id, "project_id tiến độ")
        name = normalized_filename(path, filename, "TienDo.xlsx")
        opts = options_dict(options)
        if progress:
            progress(5, "Service tiến độ đã nhận file", "")
        result = self.parse_file(
            path,
            name,
            status_date=opts.get("status_date"),
            progress=progress,
            cancelled=cancelled,
        )
        ensure_not_cancelled(cancelled, "Job tiến độ đã được yêu cầu hủy.")

        db = self._db_factory()
        sheet_name = str(result.get("sheet_name") or "")
        if progress:
            progress(75, "Đang chuẩn bị ghi tiến độ vào PostgreSQL", sheet_name)
        stats = self.save_result(db, pid, result, progress=progress, cancelled=cancelled)
        ensure_not_cancelled(cancelled, "Job tiến độ đã được yêu cầu hủy.")
        if progress:
            progress(98, "Đang hoàn tất Excel tiến độ", sheet_name)

        return {
            "pipeline": "V6.26 Schedule service",
            "job_type": "SCHEDULE_EXCEL",
            "workspace_project_id": pid,
            "filename": name,
            "file_size": declared_file_size(path, file_size),
            "sheet_name": sheet_name,
            "status_date": str(result.get("status_date") or ""),
            "expected_rows": int(stats.get("expected_rows") or 0),
            "scanned_rows": int(stats.get("scanned_rows") or 0),
            "prepared_rows": int(stats.get("prepared_rows") or 0),
            "inserted_rows": int(stats.get("inserted_rows") or 0),
            "written_rows": int(stats.get("written_rows") or 0),
            "failed_rows": int(stats.get("failed_rows") or 0),
            "verification_status": str(stats.get("verification_status") or "CHƯA ĐỦ"),
            "verified_postgresql": bool(stats.get("verified_postgresql")),
            "source_row_count": int(stats.get("source_row_count") or 0),
            "skipped_rows": int(stats.get("skipped_rows") or 0),
            "batch_id": str(stats.get("batch_id") or ""),
            "source_sha256": str(result.get("source_sha256") or ""),
        }
