from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

_SHEET_ID_RE = re.compile(r"/spreadsheets/d/([a-zA-Z0-9-_]+)")


@dataclass(frozen=True, slots=True)
class ProductionRow:
    worksheet: str
    work_item: str
    zone: str
    progress_percent: float
    source_row: int


def parse_spreadsheet_id(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError("Chưa nhập link hoặc Spreadsheet ID.")
    match = _SHEET_ID_RE.search(text)
    if match:
        return match.group(1)
    if re.fullmatch(r"[a-zA-Z0-9-_]{20,}", text):
        return text
    raise ValueError("Link Google Sheets / Spreadsheet ID không hợp lệ.")


def _percent(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number * 100.0 if 0.0 <= number <= 1.0 else number
    text = str(value).strip().replace(",", ".")
    if not text:
        return None
    if text.endswith("%"):
        text = text[:-1].strip()
    try:
        number = float(text)
    except ValueError:
        return None
    return number * 100.0 if 0.0 <= number <= 1.0 else number


def _zone_header_row(rows: list[list[object]]) -> tuple[int, list[str]]:
    best_index = -1
    best_headers: list[str] = []
    for idx, row in enumerate(rows[:30]):
        headers = [str(cell or "").strip() for cell in row]
        zone_count = sum(1 for h in headers if h.lower().startswith("zone"))
        if zone_count > sum(1 for h in best_headers if h.lower().startswith("zone")):
            best_index, best_headers = idx, headers
    if best_index < 0:
        raise ValueError("Không nhận diện được dòng tiêu đề Zone trong worksheet.")
    return best_index, best_headers


def normalize_production_sheet(worksheet: str, values: Iterable[Iterable[object]]) -> list[ProductionRow]:
    rows = [list(r) for r in values]
    if not rows:
        return []
    header_idx, headers = _zone_header_row(rows)
    zone_columns = [(i, h) for i, h in enumerate(headers) if h.lower().startswith("zone")]
    if not zone_columns:
        raise ValueError("Worksheet không có cột Zone.")

    result: list[ProductionRow] = []
    for row_idx, row in enumerate(rows[header_idx + 1 :], start=header_idx + 2):
        work_item = str(row[0] if row else "").strip()
        if not work_item:
            continue
        found_numeric = False
        for col_idx, zone in zone_columns:
            raw = row[col_idx] if col_idx < len(row) else None
            pct = _percent(raw)
            if pct is None:
                continue
            found_numeric = True
            result.append(
                ProductionRow(
                    worksheet=str(worksheet or ""),
                    work_item=work_item,
                    zone=zone,
                    progress_percent=max(0.0, min(100.0, pct)),
                    source_row=row_idx,
                )
            )
        # Group/header rows normally have no progress values and are intentionally skipped.
        if not found_numeric:
            continue
    return result
