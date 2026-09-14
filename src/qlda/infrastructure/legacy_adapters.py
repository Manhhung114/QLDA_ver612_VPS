from __future__ import annotations

"""Remaining anti-corruption adapter for QLDA V7.3.

Session, project access, files, jobs, search and AI now have native V7
infrastructure adapters. Only the business-heavy Excel import pipeline still
depends on the deprecated V6 service layer while it is migrated incrementally.

Domain and application packages never import this module.
"""


class LegacyExcelImportAdapter:
    @staticmethod
    def scan_workbook(path, *, progress=None, cancelled=None):
        from qlda.services.excel import ExcelImportService

        return ExcelImportService.scan_workbook(
            path,
            progress=progress,
            cancelled=cancelled,
        )

    @staticmethod
    def process_job(job, path, file_row, *, progress=None, cancelled=None):
        from qlda.infrastructure.database import make_database
        from qlda.services.excel import ExcelImportService

        return ExcelImportService(db_factory=make_database).process_job(
            job,
            path,
            file_row,
            progress=progress,
            cancelled=cancelled,
        )
