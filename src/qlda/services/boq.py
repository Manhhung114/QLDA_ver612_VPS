from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from qlda.infrastructure.database import make_database
from qlda.modules.boq import background, persistence
from qlda.shared.legacy import resolve
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


class BOQService:
    """Application service for the complete BOQ Excel import use case."""

    def __init__(self, db_factory: DbFactory = make_database):
        self._db_factory = db_factory

    def parse_file(
        self,
        path: str | Path,
        filename: str = "BOQ.xlsx",
        *,
        progress: ProgressFn | None = None,
        cancelled: CancelFn | None = None,
    ) -> dict[str, Any]:
        return background.parse_boq_path(
            path,
            normalized_filename(path, filename, "BOQ.xlsx"),
            progress=progress,
            cancelled=cancelled,
        )

    def save_result(
        self,
        db: Any,
        project_id: int,
        result: dict[str, Any],
        *,
        replace_existing_excel: bool = True,
        progress: ProgressFn | None = None,
        cancelled: CancelFn | None = None,
    ) -> dict[str, Any]:
        return persistence.save_boq_result_batched(
            db,
            require_project_id(project_id, "project_id BOQ"),
            result,
            replace_existing_excel=bool(replace_existing_excel),
            progress=progress,
            cancelled=cancelled,
        )

    def import_file(
        self,
        project_id: int,
        path: str | Path,
        filename: str = "BOQ.xlsx",
        *,
        file_size: int = 0,
        options: Mapping[str, Any] | None = None,
        progress: ProgressFn | None = None,
        cancelled: CancelFn | None = None,
    ) -> dict[str, Any]:
        pid = require_project_id(project_id, "project_id BOQ")
        name = normalized_filename(path, filename, "BOQ.xlsx")
        opts = options_dict(options)
        if progress:
            progress(5, "Service BOQ đã nhận file", "")
        result = self.parse_file(path, name, progress=progress, cancelled=cancelled)
        ensure_not_cancelled(cancelled, "Job BOQ đã được yêu cầu hủy.")

        db = self._db_factory()
        if progress:
            progress(75, "Đang chuẩn bị ghi BOQ vào PostgreSQL", "")
        stats = self.save_result(
            db,
            pid,
            result,
            replace_existing_excel=bool(opts.get("replace_existing_excel", True)),
            progress=progress,
            cancelled=cancelled,
        )
        ensure_not_cancelled(cancelled, "Job BOQ đã được yêu cầu hủy.")

        if progress:
            progress(94, "Đang lưu snapshot workbook dùng chung", "")
        save_snapshot = resolve("boq_persistence_v622", "save_saved_boq_workbook")
        save_snapshot(db, pid, result)
        if progress:
            progress(98, "Đang hoàn tất BOQ", "")

        return {
            "pipeline": "V6.26 BOQ service",
            "job_type": "BOQ",
            "workspace_project_id": pid,
            "filename": name,
            "file_size": declared_file_size(path, file_size),
            "inserted": int(stats.get("inserted") or 0),
            "expected_rows": int(stats.get("expected_rows") or 0),
            "scanned_rows": int(stats.get("scanned_rows") or 0),
            "prepared_rows": int(stats.get("prepared_rows") or 0),
            "written_rows": int(stats.get("written_rows") or 0),
            "failed_rows": int(stats.get("failed_rows") or 0),
            "verification_status": str(stats.get("verification_status") or "CHƯA ĐỦ"),
            "verified_postgresql": bool(stats.get("verified_postgresql")),
            "deleted": int(stats.get("deleted") or 0),
            "detail_line_count": int(result.get("detail_line_count") or 0),
            "before_tax_total": float(stats.get("before_tax_total") or 0),
            "vat_total": float(stats.get("vat_total") or 0),
            "after_tax_total": float(stats.get("after_tax_total") or 0),
            "material_cost_total": float(stats.get("material_cost_total") or 0),
            "labor_cost_total": float(stats.get("labor_cost_total") or 0),
            "material_component_line_count": int(stats.get("material_component_line_count") or 0),
            "labor_component_line_count": int(stats.get("labor_component_line_count") or 0),
            "batch_id": str(stats.get("batch_id") or result.get("batch_id") or ""),
        }
