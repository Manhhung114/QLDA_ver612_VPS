from __future__ import annotations

"""Source-faithful production summary extraction for Contractor Data Hub.

This is deterministic application logic, not an AI compatibility layer. It keeps
workbook-authored totals separate from normalized detail records so dashboards
and RAG provenance can use the same source-defined values.
"""

import re
import unicodedata
from collections import defaultdict
from typing import Any, Iterable

_COORD_RE = re.compile(r"(?:^| \| )([A-Z]+)=(.*?)(?= \| [A-Z]+=|$)")


def _rowdict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    try:
        return {str(k): row[k] for k in row.keys()}
    except Exception:
        try:
            return dict(row)
        except Exception:
            return {}


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("đ", "d")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _column_number(label: str) -> int:
    out = 0
    for ch in str(label or "").upper():
        if "A" <= ch <= "Z":
            out = out * 26 + (ord(ch) - 64)
    return out


def parse_raw_sheet_content(content: str) -> dict[str, str]:
    return {match.group(1): match.group(2).strip() for match in _COORD_RE.finditer(str(content or ""))}


def _pct(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace("\u00a0", " ").replace(",", ".")
    if not text:
        return None
    is_percent = text.endswith("%")
    if is_percent:
        text = text[:-1].strip()
    try:
        number = float(text)
    except Exception:
        return None
    if is_percent:
        return number
    return number * 100.0 if 0.0 <= number <= 1.0 else number


def _contains_marker(cells: dict[str, str], marker: str) -> bool:
    target = _norm(marker)
    return any(_norm(value) == target or _norm(value).startswith(target + " ") for value in cells.values())


def _total_column(cells: dict[str, str]) -> str:
    candidates = [key for key, value in cells.items() if _norm(value) in {"tong", "total"}]
    return max(candidates, key=_column_number) if candidates else ""


def _work_label(cells: dict[str, str], *, total_col: str = "") -> str:
    preferred = str(cells.get("B") or "").strip()
    if preferred:
        return preferred
    limit = _column_number(total_col) if total_col else 10**9
    for key in sorted(cells, key=_column_number):
        if _column_number(key) >= limit:
            continue
        value = str(cells.get(key) or "").strip()
        if not value or re.fullmatch(r"A\d+", value, flags=re.I):
            continue
        if _pct(value) is not None:
            continue
        return value
    return ""


def extract_official_summaries(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    for raw in records:
        row = dict(raw or {})
        if str(row.get("record_type") or "").upper() != "SHEET_ROW":
            continue
        worksheet = str(row.get("worksheet") or "").strip()
        if not worksheet:
            continue
        grouped[(int(row.get("workspace_project_id") or 0), worksheet)].append(row)

    out: list[dict[str, Any]] = []
    for (workspace_id, worksheet), raw_rows in grouped.items():
        rows: list[tuple[int, dict[str, str], dict[str, Any]]] = []
        for raw in sorted(raw_rows, key=lambda x: int(x.get("source_row") or 0)):
            cells = parse_raw_sheet_content(str(raw.get("content") or ""))
            if cells:
                rows.append((int(raw.get("source_row") or 0), cells, raw))
        if not rows:
            continue

        summary: dict[str, Any] = {
            "workspace_project_id": workspace_id,
            "worksheet": worksheet,
            "weighted_total": None,
            "weighted_total_row": None,
            "package_total": None,
            "package_total_row": None,
            "discipline_totals": {},
            "package_totals": {},
        }

        marker_idx = next((i for i, (_, cells, _) in enumerate(rows) if _contains_marker(cells, "TỔNG SẢN LƯỢNG")), None)
        if marker_idx is not None:
            header_idx = None
            total_col = ""
            for i in range(marker_idx + 1, min(len(rows), marker_idx + 5)):
                candidate = rows[i][1]
                tc = _total_column(candidate)
                normalized = [_norm(x) for x in candidate.values()]
                if tc and any("thi cong phan tho" in x or "lap dat thiet bi" in x for x in normalized):
                    header_idx, total_col = i, tc
                    break
            if header_idx is not None and total_col:
                for i in range(header_idx + 1, len(rows)):
                    source_row, cells, _ = rows[i]
                    if _contains_marker(cells, "Tổng hợp sản lượng theo đầu mục tiến độ"):
                        break
                    value = _pct(cells.get(total_col))
                    if value is None:
                        continue
                    label = _work_label(cells, total_col=total_col)
                    if summary["weighted_total"] is None and not label:
                        summary["weighted_total"] = float(value)
                        summary["weighted_total_row"] = source_row
                    elif label:
                        summary["discipline_totals"][label] = float(value)

        package_marker_idx = next(
            (i for i, (_, cells, _) in enumerate(rows) if _contains_marker(cells, "Tổng hợp sản lượng theo đầu mục tiến độ")),
            None,
        )
        if package_marker_idx is not None:
            header_idx = None
            total_col = ""
            for i in range(package_marker_idx + 1, min(len(rows), package_marker_idx + 5)):
                candidate = rows[i][1]
                tc = _total_column(candidate)
                if tc and any("hang muc cong viec" in _norm(x) for x in candidate.values()):
                    header_idx, total_col = i, tc
                    break
            if header_idx is not None and total_col:
                blank_totals: list[tuple[int, float]] = []
                for i in range(header_idx + 1, len(rows)):
                    source_row, cells, _ = rows[i]
                    value = _pct(cells.get(total_col))
                    if value is None:
                        continue
                    label = _work_label(cells, total_col=total_col)
                    code = str(cells.get("A") or "").strip()
                    if label:
                        summary["package_totals"][f"{code} {label}".strip()] = float(value)
                    else:
                        blank_totals.append((source_row, float(value)))
                if blank_totals:
                    source_row, value = blank_totals[-1]
                    summary["package_total"] = value
                    summary["package_total_row"] = source_row

        if summary["weighted_total"] is not None or summary["package_total"] is not None:
            out.append(summary)
    return out


__all__ = ["extract_official_summaries", "parse_raw_sheet_content"]
