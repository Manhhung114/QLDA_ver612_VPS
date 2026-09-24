from __future__ import annotations

"""Guarantee complete Google Sheet row ingestion for Contractor Data Hub.

The production normalizer intentionally creates many structured points from one
source row (one point per Zone/floor). Historically, sources classified as
PRODUCTION kept only those structured points whenever normalization succeeded,
which meant titles, section headers and lower summary tables disappeared from
PostgreSQL/AI context. This patch keeps an exact raw SHEET_ROW for every non-
empty Google/Excel row *in addition to* normalized production points.

It also prevents the primary floor matrix parser from leaking into the later
summary blocks headed by "TỔNG SẢN LƯỢNG", where sparse summary cells would
otherwise be mislabeled as T29/T32/... by the first header row.
"""

import os
from typing import Any, Iterable

PATCH_MARKER = "V7 CONTRACTOR DATA COMPLETE ROWS V3"


def _column_label(index: int) -> str:
    """1-based Excel/Google column label."""
    value = int(index)
    out = ""
    while value > 0:
        value, remainder = divmod(value - 1, 26)
        out = chr(65 + remainder) + out
    return out or "A"


def _raw_row_content(row: Iterable[Any]) -> str:
    """Lossless, header-independent text for one sheet row.

    Stable A/B/C... coordinates are used instead of assuming one header row for
    the whole worksheet. SME files contain several separate tables on the same
    worksheet, so reusing the first T1/T2/... header for the lower summary blocks
    produces incorrect semantics.
    """
    pairs: list[str] = []
    for idx, value in enumerate(list(row), start=1):
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        pairs.append(f"{_column_label(idx)}={text}")
    return " | ".join(pairs)


def _dedupe(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Remove duplicate raw rows from legacy AUTO/public paths."""
    seen: set[tuple[Any, ...]] = set()
    out: list[dict[str, Any]] = []
    for raw in records:
        row = dict(raw or {})
        key = (
            str(row.get("external_item_id") or ""),
            str(row.get("worksheet") or ""),
            str(row.get("record_type") or ""),
            int(row.get("source_row") or 0),
            str(row.get("zone") or ""),
            str(row.get("work_item") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _include_raw_source(source: dict[str, Any]) -> dict[str, Any]:
    """Make legacy PRODUCTION branches retain raw rows and structured rows."""
    item = dict(source or {})
    category = str(item.get("category") or "AUTO").upper()
    if category == "PRODUCTION":
        item["_qlda_original_category"] = category
        # Existing AUTO branches store both generic + normalized rows.
        item["category"] = "AUTO"
    return item


def _snapshot_with_raw_count(
    records: list[dict[str, Any]], snapshot: dict[str, Any] | None
) -> dict[str, Any]:
    out = dict(snapshot or {})
    out["record_count"] = len(records)
    out["raw_row_count"] = sum(
        1 for row in records if str(row.get("record_type") or "") == "SHEET_ROW"
    )
    return out


def install_contractor_data_complete_rows() -> None:
    import qlda.application.contractor_data_hub as public_hub
    import qlda.application.contractor_data_hub.service as service_mod
    import qlda.application.google_sheets.service as google_sheet_service
    from qlda.infrastructure.contractor_data_hub import ContractorDataHubRepository

    service_cls = service_mod.ContractorDataHubService
    public_service_cls = public_hub.ContractorDataHubService
    if getattr(service_cls, "_qlda_complete_rows_v3", False):
        return

    original_normalize = service_mod.normalize_production_sheet
    original_sheet_file_records = service_cls._sheet_file_records
    original_public_sheet_records = service_cls._public_sheet_records
    original_xlsx_records = service_cls._xlsx_records

    @staticmethod
    def generic_all_rows(
        source: dict[str, Any],
        worksheet: str,
        values: list[list[Any]],
        *,
        external_item_id: str,
        external_path: str = "",
        modified_time: str = "",
    ) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        source_category = str(
            source.get("_qlda_original_category")
            or source.get("category")
            or "AUTO"
        ).upper()
        for row_no, row in enumerate(values or [], start=1):
            content = _raw_row_content(row)
            if not content:
                continue
            records.append({
                "source_name": str(source.get("name") or "Google Sheet"),
                "source_kind": "SHEET",
                "category": source_category,
                "external_item_id": str(external_item_id or ""),
                "external_path": str(external_path or ""),
                "worksheet": str(worksheet or ""),
                "record_type": "SHEET_ROW",
                "record_ref": f"row:{row_no}",
                "content": content,
                "source_row": int(row_no),
                "external_modified_time": str(modified_time or ""),
            })
        return records

    def normalize_primary_matrix(worksheet: str, values: Iterable[Iterable[object]]):
        rows = [list(row) for row in values]
        if not rows:
            return []

        # SME update worksheets contain additional summary tables below the main
        # T1/TL/T2... matrix. Stop the primary matrix at the explicit section
        # marker; those later rows are still preserved exactly as SHEET_ROW.
        stop_at = len(rows)
        try:
            header_idx, _headers, _progress, _layout = google_sheet_service._progress_header_row(rows)
        except Exception:
            header_idx = -1

        if header_idx >= 0:
            for idx in range(header_idx + 1, len(rows)):
                normalized_cells = [google_sheet_service._norm(cell) for cell in rows[idx]]
                if any(
                    text == "tong san luong" or text.startswith("tong san luong ")
                    for text in normalized_cells
                    if text
                ):
                    stop_at = idx
                    break

        return original_normalize(worksheet, rows[:stop_at])

    def sheet_file_records_complete(self, source: dict[str, Any], spreadsheet_id: str, **kwargs):
        records, production_points, snapshot = original_sheet_file_records(
            self, _include_raw_source(source), spreadsheet_id, **kwargs
        )
        records = _dedupe(records)
        return records, production_points, _snapshot_with_raw_count(records, snapshot)

    def public_sheet_records_complete(self, source: dict[str, Any]):
        records, production_points, snapshot = original_public_sheet_records(
            self, _include_raw_source(source)
        )
        records = _dedupe(records)
        return records, production_points, _snapshot_with_raw_count(records, snapshot)

    @staticmethod
    def xlsx_records_complete(source: dict[str, Any], data: bytes, *, file_meta: dict[str, Any]):
        records, production_points = original_xlsx_records(
            _include_raw_source(source), data, file_meta=file_meta
        )
        return _dedupe(records), production_points

    # The Contractor Data Hub service imported normalize_production_sheet
    # directly, so patch both its local reference and the canonical function.
    service_mod.normalize_production_sheet = normalize_primary_matrix
    google_sheet_service.normalize_production_sheet = normalize_primary_matrix

    service_cls._generic_sheet_records = generic_all_rows
    service_cls._sheet_file_records = sheet_file_records_complete
    service_cls._public_sheet_records = public_sheet_records_complete
    service_cls._xlsx_records = xlsx_records_complete
    service_cls._qlda_complete_rows_v1 = True
    service_cls._qlda_complete_rows_v2 = True
    service_cls._qlda_complete_rows_v3 = True
    service_cls._qlda_complete_rows_marker = PATCH_MARKER

    # The exported public application class has its own _public_sheet_records
    # implementation for OAuth/XLSX/GID discovery. Wrap that entrypoint too so a
    # final direct-GID fallback cannot revert PRODUCTION sources to
    # `production or generic` and silently discard raw source rows.
    if (
        public_service_cls is not service_cls
        and not getattr(public_service_cls, "_qlda_complete_rows_public_v1", False)
    ):
        original_public_entry = public_service_cls._public_sheet_records

        def public_entry_complete(self, source: dict[str, Any]):
            records, production_points, snapshot = original_public_entry(
                self, _include_raw_source(source)
            )
            records = _dedupe(records)
            return records, production_points, _snapshot_with_raw_count(records, snapshot)

        public_service_cls._public_sheet_records = public_entry_complete
        public_service_cls._qlda_complete_rows_public_v1 = True
        public_service_cls._qlda_complete_rows_public_marker = PATCH_MARKER

    # Current overview asks for 20k records. That ceiling can truncate a project
    # as soon as several worksheets/contractors are synchronized. Preserve small
    # intentional query limits, but expand exactly the dashboard's legacy limit.
    if not getattr(ContractorDataHubRepository, "_qlda_complete_rows_limit_v1", False):
        original_records = ContractorDataHubRepository.records
        dashboard_limit = max(
            20000,
            min(500000, int(os.environ.get("QLDA_CDH_DASHBOARD_MAX_RECORDS", "100000"))),
        )

        def records_complete(self, *args, **kwargs):
            try:
                if int(kwargs.get("limit") or 0) == 20000:
                    kwargs["limit"] = dashboard_limit
            except Exception:
                pass
            return original_records(self, *args, **kwargs)

        ContractorDataHubRepository.records = records_complete
        ContractorDataHubRepository._qlda_complete_rows_limit_v1 = True


__all__ = ["install_contractor_data_complete_rows"]
