from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import parse_qs, urlparse

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


def parse_sheet_gid(value: str, *, default: int = 0) -> int:
    """Return the worksheet gid from a normal Google Sheets URL.

    Google usually places gid in the URL fragment (``#gid=123``), but some
    generated links use a query parameter. A bare spreadsheet ID has no gid,
    therefore the first worksheet (gid=0) is used.
    """
    text = str(value or "").strip()
    if not text:
        return int(default)
    try:
        parsed = urlparse(text)
        for part in (parsed.query, parsed.fragment):
            values = parse_qs(part).get("gid") or []
            if values and str(values[0]).isdigit():
                return int(values[0])
    except Exception:
        pass
    match = re.search(r"(?:[?#&])gid=(\d+)", text)
    return int(match.group(1)) if match else int(default)


def _norm(value: object) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("đ", "d")
    text = re.sub(r"[^a-z0-9%]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _percent(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        return number * 100.0 if 0.0 <= number <= 1.0 else number
    text = str(value).strip().replace("\u00a0", " ").replace(",", ".")
    if not text:
        return None
    text = re.sub(r"\s*%\s*$", "", text).strip()
    try:
        number = float(text)
    except ValueError:
        return None
    return number * 100.0 if 0.0 <= number <= 1.0 else number


def _is_zone_header(value: object) -> bool:
    text = _norm(value)
    if not text:
        return False
    # Common contractor templates use Zone 1/2/3, Khu 1, Khu vực 1, KV1,
    # or Area 1. Requiring a suffix avoids treating a generic "Khu vực"
    # description column as a progress zone.
    return bool(
        re.match(r"^(?:zone|khu vuc|khu|kv|area)\s*[-_:]?\s*[a-z0-9]+(?:\b|$)", text)
    )


def _is_floor_progress_header(value: object) -> bool:
    """Recognize floor columns used by SME tower production matrices.

    Real contractor workbooks often do not have Zone columns outside the basement.
    Their upper-tower sheets instead look like::

        Công tác | T1 | TL | T2 | T3 | ... | T19A | ... | T36 | TỔNG

    Those T*/TL columns are progress dimensions, not worksheet names.  We map
    them to the existing ``zone`` field so the current normalized data model and
    dashboard can consume them without a database migration.
    """
    text = _norm(value).replace(" ", "")
    if not text:
        return False
    return bool(
        re.fullmatch(r"t(?:l|\d+[a-z]?)", text)
        or re.fullmatch(r"(?:tang|floor)(?:l|\d+[a-z]?)", text)
    )


def _is_work_item_header(value: object) -> bool:
    text = _norm(value)
    if not text:
        return False
    preferred = (
        "cong tac",
        "noi dung cong tac",
        "noi dung",
        "hang muc",
        "mo ta",
        "description",
        "work item",
        "workitem",
        "task",
    )
    return any(text == target or text.startswith(target + " ") for target in preferred)


def _progress_header_row(rows: list[list[object]]) -> tuple[int, list[str], list[tuple[int, str]], str]:
    """Return header row, progress columns and detected layout.

    Priority is the traditional Zone/Khu-vực layout. If it is absent, fall back
    to a floor-matrix layout only when the same row also contains an explicit
    work-item header. This keeps the fallback conservative and prevents random
    T1/T2 labels in notes from being treated as production data.
    """
    best_zone: tuple[int, list[str], list[tuple[int, str]], str] | None = None
    best_floor: tuple[int, list[str], list[tuple[int, str]], str] | None = None

    for idx, row in enumerate(rows[:200]):
        headers = [str(cell or "").strip() for cell in row]
        zone_columns = [(i, h) for i, h in enumerate(headers) if _is_zone_header(h)]
        if zone_columns and (best_zone is None or len(zone_columns) > len(best_zone[2])):
            best_zone = (idx, headers, zone_columns, "ZONE")

        floor_columns = [
            (i, h) for i, h in enumerate(headers) if _is_floor_progress_header(h)
        ]
        if (
            len(floor_columns) >= 2
            and any(_is_work_item_header(h) for h in headers)
            and (best_floor is None or len(floor_columns) > len(best_floor[2]))
        ):
            best_floor = (idx, headers, floor_columns, "FLOOR_MATRIX")

    if best_zone is not None:
        return best_zone
    if best_floor is not None:
        return best_floor
    raise ValueError(
        "Không nhận diện được ma trận sản lượng. Cần dòng tiêu đề có Zone/Khu vực "
        "hoặc dạng Công tác + các cột tầng T1/TL/T2/..."
    )


def _work_item_column(headers: list[str], progress_indexes: set[int]) -> int:
    preferred = (
        "cong tac",
        "noi dung cong tac",
        "noi dung",
        "hang muc",
        "mo ta",
        "description",
        "work item",
        "workitem",
        "task",
    )
    normalized = [_norm(x) for x in headers]
    for target in preferred:
        for idx, value in enumerate(normalized):
            if idx in progress_indexes:
                continue
            if value == target or value.startswith(target + " "):
                return idx

    # Ignore common numbering/code columns when no explicit work-item header is found.
    ignored = {"stt", "tt", "no", "number", "ma", "ma cong tac", "code"}
    for idx, value in enumerate(normalized):
        if idx in progress_indexes or not value or value in ignored:
            continue
        return idx
    return 0


def normalize_production_sheet(worksheet: str, values: Iterable[Iterable[object]]) -> list[ProductionRow]:
    rows = [list(r) for r in values]
    if not rows:
        return []

    header_idx, headers, progress_columns, _layout = _progress_header_row(rows)
    work_col = _work_item_column(headers, {idx for idx, _ in progress_columns})
    result: list[ProductionRow] = []
    last_work_item = ""

    for row_idx, row in enumerate(rows[header_idx + 1 :], start=header_idx + 2):
        raw_work = row[work_col] if work_col < len(row) else None
        work_item = str(raw_work or "").strip()

        # Merged cells are common in Google/Excel contractor templates. When a
        # work-item cell is merged vertically Google may return blanks on the
        # following rows, so carry the previous label only if this row actually
        # contains numeric progress values.
        parsed: list[tuple[str, float]] = []
        for col_idx, progress_dimension in progress_columns:
            raw = row[col_idx] if col_idx < len(row) else None
            pct = _percent(raw)
            if pct is None:
                continue
            # Reject quantities or other accidental numeric values outside the
            # physically meaningful progress range. 0..1 ratios and 0..100
            # percentage values are both accepted by _percent().
            if pct < 0.0 or pct > 100.0:
                continue
            parsed.append((progress_dimension, pct))

        if not parsed:
            if work_item:
                last_work_item = work_item
            continue
        if not work_item:
            work_item = last_work_item
        if not work_item:
            # Example: SME row 3 contains apartment counts (1, 12, 12, ...)
            # under T1/TL/T2/... but no work-item text. It is metadata, not
            # production progress, and must not enter the normalized table.
            continue
        last_work_item = work_item

        for progress_dimension, pct in parsed:
            result.append(
                ProductionRow(
                    worksheet=str(worksheet or ""),
                    work_item=work_item,
                    zone=progress_dimension,
                    progress_percent=pct,
                    source_row=row_idx,
                )
            )
    return result
