from __future__ import annotations

"""V7.5 native Excel-import adapter.

The application boundary talks directly to packaged import engines under
``qlda.import_engines``. V7.5 removes ``legacy_import`` and the V6 service/
module facades from the worker path while preserving the proven parser and
persistence behavior (row verification, rollback, revisions and idempotency).
"""

from pathlib import Path
from typing import Any, Callable, Mapping

from qlda.import_engines import load_engine
from qlda.infrastructure.database import make_database

ProgressFn = Callable[[int, str, str], None]
CancelFn = Callable[[], bool]
DbFactory = Callable[[], Any]
EngineLoader = Callable[[str], Any]


def _require_project_id(value: Any, label: str = "project_id") -> int:
    try:
        project_id = int(value or 0)
    except Exception as exc:
        raise ValueError(f"{label} không hợp lệ.") from exc
    if project_id <= 0:
        raise ValueError(f"{label} không hợp lệ.")
    return project_id


def _normalized_filename(path: str | Path, filename: str, fallback: str) -> str:
    value = Path(str(filename or "")).name.strip()
    if value:
        return value
    source = Path(path)
    return source.name or fallback


def _declared_file_size(path: str | Path, value: Any = 0) -> int:
    try:
        size = int(value or 0)
    except Exception:
        size = 0
    if size > 0:
        return size
    try:
        return int(Path(path).stat().st_size)
    except Exception:
        return 0


def _ensure_not_cancelled(cancelled: CancelFn | None, message: str) -> None:
    if cancelled and cancelled():
        raise InterruptedError(message)


class NativeExcelImportAdapter:
    """ExcelImportPort implementation backed by packaged V7.5 import engines."""

    def __init__(
        self,
        db_factory: DbFactory = make_database,
        *,
        engine_loader: EngineLoader = load_engine,
    ) -> None:
        self._db_factory = db_factory
        self._engine_loader = engine_loader

    def _engine(self, name: str):
        return self._engine_loader(name)

    @staticmethod
    def normalize_job_type(value: Any) -> str:
        job_type = str(value or "").strip().upper()
        return "BOQ" if job_type == "BOQ_IMPORT" else job_type

    @staticmethod
    def scan_workbook(
        path: str | Path,
        *,
        progress: ProgressFn | None = None,
        cancelled: CancelFn | None = None,
    ) -> dict[str, Any]:
        from openpyxl import load_workbook

        source = Path(path)
        if source.suffix.lower() not in {".xlsx", ".xlsm"}:
            raise ValueError("Background Excel V7.5 hỗ trợ .xlsx/.xlsm.")
        if not source.exists() or not source.is_file():
            raise FileNotFoundError(f"Không tìm thấy file Excel trên VPS: {source}")
        if progress:
            progress(5, "Đang mở workbook từ ổ đĩa", "")
        workbook = load_workbook(str(source), read_only=True, data_only=True, keep_links=False)
        try:
            sheets = list(workbook.worksheets)
            total = max(1, len(sheets))
            result_sheets: list[dict[str, Any]] = []
            for index, sheet in enumerate(sheets, start=1):
                if cancelled and cancelled():
                    raise InterruptedError("Job đã được yêu cầu hủy.")
                if progress:
                    progress(10 + int((index - 1) * 80 / total), "Đang quét cấu trúc workbook", str(sheet.title))
                nonempty_rows = 0
                nonempty_cells = 0
                sample_rows: list[list[str]] = []
                for row_no, values in enumerate(sheet.iter_rows(values_only=True), start=1):
                    if cancelled and row_no % 500 == 0 and cancelled():
                        raise InterruptedError("Job đã được yêu cầu hủy.")
                    used = [value for value in values if value not in (None, "")]
                    if used:
                        nonempty_rows += 1
                        nonempty_cells += len(used)
                        if len(sample_rows) < 5:
                            sample_rows.append([str(value)[:120] for value in values[:12]])
                result_sheets.append({
                    "name": str(sheet.title),
                    "max_row": int(getattr(sheet, "max_row", 0) or 0),
                    "max_column": int(getattr(sheet, "max_column", 0) or 0),
                    "nonempty_rows": nonempty_rows,
                    "nonempty_cells": nonempty_cells,
                    "sample_rows": sample_rows,
                })
                if progress:
                    progress(10 + int(index * 80 / total), "Đang quét cấu trúc workbook", str(sheet.title))
            if progress:
                progress(95, "Đang hoàn tất kiểm tra workbook", "")
            return {
                "pipeline": "V7.5 native workbook scan",
                "file_name": source.name,
                "file_size": int(source.stat().st_size),
                "sheet_count": len(result_sheets),
                "sheets": result_sheets,
            }
        finally:
            workbook.close()

    def _process_boq(
        self, project_id: int, path: str | Path, filename: str, file_size: int,
        options: Mapping[str, Any], progress: ProgressFn | None, cancelled: CancelFn | None,
    ) -> dict[str, Any]:
        pid = _require_project_id(project_id, "project_id BOQ")
        name = _normalized_filename(path, filename, "BOQ.xlsx")
        if progress:
            progress(5, "Native BOQ đã nhận file", "")
        result = self._engine("boq_background").parse_boq_path(path, name, progress=progress, cancelled=cancelled)
        _ensure_not_cancelled(cancelled, "Job BOQ đã được yêu cầu hủy.")
        db = self._db_factory()
        if progress:
            progress(75, "Đang chuẩn bị ghi BOQ vào PostgreSQL", "")
        stats = self._engine("boq_persistence").save_boq_result_batched(
            db, pid, result,
            replace_existing_excel=bool(dict(options).get("replace_existing_excel", True)),
            progress=progress, cancelled=cancelled,
        )
        _ensure_not_cancelled(cancelled, "Job BOQ đã được yêu cầu hủy.")
        if progress:
            progress(94, "Đang lưu snapshot workbook dùng chung", "")
        self._engine("boq_snapshot").save_saved_boq_workbook(db, pid, result)
        if progress:
            progress(98, "Đang hoàn tất BOQ", "")
        return {
            "pipeline": "V7.5 native BOQ engine", "job_type": "BOQ", "workspace_project_id": pid,
            "filename": name, "file_size": _declared_file_size(path, file_size),
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

    def _process_ipc(
        self, project_id: int, path: str | Path, filename: str, file_size: int,
        progress: ProgressFn | None, cancelled: CancelFn | None,
    ) -> dict[str, Any]:
        pid = _require_project_id(project_id, "project_id IPC")
        name = _normalized_filename(path, filename, "IPC.xlsx")
        if progress:
            progress(5, "Native IPC đã nhận file", "")
        result = self._engine("ipc_background").parse_ipc_path(path, name, progress=progress, cancelled=cancelled)
        _ensure_not_cancelled(cancelled, "Job IPC đã được yêu cầu hủy.")
        db = self._db_factory()
        claim_code = str(result.get("claim_code") or "")
        if progress:
            progress(75, "Đang chuẩn bị ghi IPC vào PostgreSQL", claim_code)
        stats = self._engine("ipc_persistence").save_ipc_result_batched(db, pid, result, progress=progress, cancelled=cancelled)
        _ensure_not_cancelled(cancelled, "Job IPC đã được yêu cầu hủy.")
        if progress:
            progress(98, "Đang hoàn tất IPC", str(stats.get("claim_code") or claim_code))
        return {
            "pipeline": "V7.5 native IPC engine", "job_type": "IPC", "workspace_project_id": pid,
            "filename": name, "file_size": _declared_file_size(path, file_size),
            "claim_id": str(stats.get("claim_id") or ""), "claim_no": str(stats.get("claim_no") or ""),
            "claim_code": str(stats.get("claim_code") or ""), "revision_no": int(stats.get("revision_no") or 0),
            "expected_rows": int(stats.get("expected_rows") or 0), "scanned_rows": int(stats.get("scanned_rows") or 0),
            "prepared_rows": int(stats.get("prepared_rows") or 0), "inserted_rows": int(stats.get("inserted_rows") or 0),
            "written_rows": int(stats.get("written_rows") or 0), "failed_rows": int(stats.get("failed_rows") or 0),
            "verification_status": str(stats.get("verification_status") or "CHƯA ĐỦ"),
            "verified_postgresql": bool(stats.get("verified_postgresql")),
            "requested_amount": float(stats.get("requested_amount") or 0),
            "certified_cumulative": float(stats.get("certified_cumulative") or 0),
            "detail_line_count": int(result.get("detail_line_count") or 0),
            "batch_id": str(stats.get("batch_id") or result.get("batch_id") or ""),
            "source_sha256": str(result.get("source_sha256") or ""),
        }

    def _process_vo(
        self, project_id: int, path: str | Path, filename: str, file_size: int,
        progress: ProgressFn | None, cancelled: CancelFn | None,
    ) -> dict[str, Any]:
        pid = _require_project_id(project_id, "project_id VO")
        name = _normalized_filename(path, filename, "VO.xlsx")
        if progress:
            progress(5, "Native VO đã nhận file", "")
        result = self._engine("vo_background").parse_vo_path(path, name, progress=progress, cancelled=cancelled)
        _ensure_not_cancelled(cancelled, "Job VO đã được yêu cầu hủy.")
        db = self._db_factory()
        vo_code = str(result.get("vo_code") or "")
        if progress:
            progress(75, "Đang chuẩn bị ghi VO vào PostgreSQL", vo_code)
        stats = self._engine("vo_persistence").save_vo_result_batched(db, pid, result, progress=progress, cancelled=cancelled)
        _ensure_not_cancelled(cancelled, "Job VO đã được yêu cầu hủy.")
        if progress:
            progress(98, "Đang hoàn tất VO", str(stats.get("vo_code") or vo_code))
        return {
            "pipeline": "V7.5 native VO engine", "job_type": "VO", "workspace_project_id": pid,
            "filename": name, "file_size": _declared_file_size(path, file_size),
            "vo_id": str(stats.get("vo_id") or ""), "vo_code": str(stats.get("vo_code") or ""),
            "revision_no": int(stats.get("revision_no") or 0),
            "expected_rows": int(stats.get("expected_rows") or 0), "scanned_rows": int(stats.get("scanned_rows") or 0),
            "prepared_rows": int(stats.get("prepared_rows") or 0), "inserted_rows": int(stats.get("inserted_rows") or 0),
            "written_rows": int(stats.get("written_rows") or 0), "failed_rows": int(stats.get("failed_rows") or 0),
            "verification_status": str(stats.get("verification_status") or "CHƯA ĐỦ"),
            "verified_postgresql": bool(stats.get("verified_postgresql")),
            "proposed_amount": float(stats.get("proposed_amount") or 0),
            "detail_line_count": int(result.get("detail_line_count") or 0),
            "batch_id": str(stats.get("batch_id") or result.get("batch_id") or ""),
            "source_sha256": str(result.get("source_sha256") or ""),
        }

    def _process_schedule(
        self, project_id: int, path: str | Path, filename: str, file_size: int,
        options: Mapping[str, Any], progress: ProgressFn | None, cancelled: CancelFn | None,
    ) -> dict[str, Any]:
        pid = _require_project_id(project_id, "project_id tiến độ")
        name = _normalized_filename(path, filename, "TienDo.xlsx")
        opts = dict(options)
        if progress:
            progress(5, "Native tiến độ đã nhận file", "")
        result = self._engine("schedule_background").parse_schedule_excel_path(
            path, name, status_date=opts.get("status_date"), progress=progress, cancelled=cancelled,
        )
        _ensure_not_cancelled(cancelled, "Job tiến độ đã được yêu cầu hủy.")
        db = self._db_factory()
        sheet_name = str(result.get("sheet_name") or "")
        if progress:
            progress(75, "Đang chuẩn bị ghi tiến độ vào PostgreSQL", sheet_name)
        stats = self._engine("schedule_persistence").save_schedule_result_batched(
            db, pid, result, progress=progress, cancelled=cancelled,
        )
        _ensure_not_cancelled(cancelled, "Job tiến độ đã được yêu cầu hủy.")
        if progress:
            progress(98, "Đang hoàn tất Excel tiến độ", sheet_name)
        return {
            "pipeline": "V7.5 native Schedule engine", "job_type": "SCHEDULE_EXCEL", "workspace_project_id": pid,
            "filename": name, "file_size": _declared_file_size(path, file_size), "sheet_name": sheet_name,
            "status_date": str(result.get("status_date") or ""),
            "expected_rows": int(stats.get("expected_rows") or 0), "scanned_rows": int(stats.get("scanned_rows") or 0),
            "prepared_rows": int(stats.get("prepared_rows") or 0), "inserted_rows": int(stats.get("inserted_rows") or 0),
            "written_rows": int(stats.get("written_rows") or 0), "failed_rows": int(stats.get("failed_rows") or 0),
            "verification_status": str(stats.get("verification_status") or "CHƯA ĐỦ"),
            "verified_postgresql": bool(stats.get("verified_postgresql")),
            "source_row_count": int(stats.get("source_row_count") or 0),
            "skipped_rows": int(stats.get("skipped_rows") or 0), "batch_id": str(stats.get("batch_id") or ""),
            "source_sha256": str(result.get("source_sha256") or ""),
        }

    def process_job(
        self, job: dict[str, Any], path: str | Path, file_row: dict[str, Any], *,
        progress: ProgressFn | None = None, cancelled: CancelFn | None = None,
    ) -> dict[str, Any]:
        job_type = self.normalize_job_type(job.get("job_type"))
        if job_type == "WORKBOOK_SCAN":
            return self.scan_workbook(path, progress=progress, cancelled=cancelled)
        project_id = _require_project_id(
            job.get("workspace_project_id") or job.get("project_id"), f"workspace_project_id {job_type}"
        )
        filename = str(file_row.get("name") or job.get("file_name") or "").strip()
        file_size = int(file_row.get("size") or 0)
        options = dict(job.get("options") or {})
        if job_type == "BOQ":
            return self._process_boq(project_id, path, filename or "BOQ.xlsx", file_size, options, progress, cancelled)
        if job_type == "IPC":
            return self._process_ipc(project_id, path, filename or "IPC.xlsx", file_size, progress, cancelled)
        if job_type == "VO":
            return self._process_vo(project_id, path, filename or "VO.xlsx", file_size, progress, cancelled)
        if job_type == "SCHEDULE_EXCEL":
            return self._process_schedule(project_id, path, filename or "TienDo.xlsx", file_size, options, progress, cancelled)
        raise RuntimeError(f"Pipeline {job_type} chưa được bật trong V7.5.")
