from __future__ import annotations

"""Deterministic layout recognition: no AI guesses or database writes.

This is a *preflight*, not a replacement for vetted BOQ/IPC/VO parsers.
Mappings are provenance-rich and review-required when the layout is ambiguous.
All column offsets returned by recognize_sheet are zero-based.
"""

import hashlib
import re
import unicodedata
from pathlib import Path
from typing import Any, Callable, Iterable


MAX_HEADER_ROWS = 80
MAX_HEADER_COLUMNS = 40

# Deliberately exclude ambiguous catch-all terms such as "tổng" / "giá trị".
# Canonical names are scoped to each business role.
ALIASES: dict[str, dict[str, tuple[str, ...]]] = {
    "BOQ": {
        "description": ("noi dung cong viec", "noi dung cong tac", "ten cong tac", "ten hang muc",
                        "hang muc", "dien giai", "mo ta cong viec", "description", "boq item",
                        "work item", "ten vat tu", "ten thiet bi"),
        "quantity": ("khoi luong", "khoi luong hop dong", "so luong", "kl", "quantity", "qty",
                     "kl thi cong"),
        "unit": ("dvt", "don vi tinh", "don vi", "unit"),
        "unit_price": ("don gia", "don gia hop dong", "gia don vi", "rate", "unit price"),
        "amount": ("thanh tien", "gia tri du toan", "thanh tien du toan", "gia tri hop dong",
                   "amount", "total amount"),
        "item_code": ("ma hieu", "ma cong tac", "ma vat tu", "item code"),
    },
    "IPC": {
        "description": ("noi dung cong viec", "hang muc cong viec", "ten cong tac",
                        "noi dung nghiem thu", "dien giai", "description"),
        "quantity": ("khoi luong nghiem thu", "khoi luong hoan thanh", "kl hoan thanh",
                     "khoi luong ky nay", "khoi luong thuc hien"),
        "unit_price": ("don gia hop dong", "don gia vat tu", "don gia nhan cong", "don gia"),
        "current_amount": ("gia tri thanh toan ky nay", "gia tri de nghi thanh toan ky nay",
                           "de nghi thanh toan ky nay", "thanh toan ky nay", "gia tri ky nay"),
        "cumulative_amount": ("luy ke thanh toan", "luy ke gia tri thanh toan",
                              "luy ke gia tri nghiem thu", "luy ke den ky nay",
                              "luy ke gia tri dntt den het ky truoc"),
        "contract_amount": ("gia tri hop dong", "gia tri theo hop dong"),
        "amount": ("thanh tien nghiem thu", "gia tri hoan thanh", "thanh tien"),
    },
    "VO": {
        "description": ("noi dung cong viec", "noi dung phat sinh", "ten cong tac",
                        "dien giai", "description", "mo ta cong viec"),
        "contract_quantity": ("khoi luong hop dong", "khoi luong theo hop dong", "theo hop dong"),
        "actual_quantity": ("thuc te thi cong", "khoi luong thuc te", "kl thuc te"),
        "variation_quantity": ("phat sinh tang giam", "khoi luong phat sinh",
                               "khoi luong tang giam", "chenh lech khoi luong", "kl phat sinh"),
        "increase_quantity": ("phat sinh tang", "khoi luong tang"),
        "decrease_quantity": ("phat sinh giam", "khoi luong giam"),
        "unit_price": ("don gia", "don gia vat tu", "don gia vat lieu", "don gia nhan cong"),
        "amount": ("thanh tien", "gia tri phat sinh", "gia tri tang giam"),
    },
    "SCHEDULE_EXCEL": {
        "name": ("cong viec", "hang muc cong viec", "ten cong viec", "noi dung cong viec",
                 "noi dung cong tac", "ten hang muc", "task name", "name", "activity name",
                 "mo ta cong viec"),
        "start": ("bat dau", "ngay bat dau", "ngay khoi cong", "khoi cong",
                  "start", "start date", "planned start"),
        "end": ("ket thuc", "ngay ket thuc", "hoan thanh", "ngay hoan thanh",
                "ngay du kien hoan thanh", "finish", "finish date", "end date"),
        "wbs": ("wbs", "stt", "ma cong viec", "task id"),
        "duration": ("so ngay", "thoi luong", "duration", "duration days"),
        "planned": ("kh %", "ke hoach %", "tien do ke hoach", "planned %"),
        "actual": ("tt %", "thuc te %", "tien do thuc te", "actual %"),
        "responsible": ("phu trach", "nguoi phu trach", "don vi thi cong", "responsible"),
        "predecessor": ("predecessor", "cong viec truoc"),
        "note": ("ghi chu", "note"),
    },
}

ROLE_HINTS = {
    "BOQ": ("boq", "du toan", "bang khoi luong", "bill of quantities"),
    "IPC": ("thanh toan", "dntt", "gtht", "gia tri hoan thanh", "nghiem thu"),
    "VO": ("phat sinh", "variation", "vo ", "dieu chinh khoi luong"),
    "SCHEDULE_EXCEL": ("tien do", "schedule", "ke hoach thi cong", "gantt"),
}

SUMMARY_HINTS = ("tong hop", "summary", "bia", "cover", "muc luc", "ky duyet")

def normalize(value: Any) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("đ", "d").replace("Đ", "D").lower()
    text = re.sub(r"[^a-z0-9%]+", " ", text)
    return " ".join(text.split())


def _alias_score(label: str, alias: str) -> int:
    if label == alias:
        return 4
    # Containment is restricted to specific long aliases and short header text.
    # A paragraph/footer must not be interpreted as a column name.
    if len(alias) >= 10 and len(label) <= 65 and re.search(
        r"(?:^| )" + re.escape(alias) + r"(?: |$)", label
    ):
        return 2
    return 0


def _row_headers(rows: list[tuple[Any, ...]], row_index: int, width: int) -> Iterable[tuple[int, str]]:
    for column in range(width):
        current = normalize(rows[row_index][column]) if column < len(rows[row_index]) else ""
        if not current:
            continue
        yield column, current
        for depth in (1, 2):
            if row_index < depth:
                break
            previous = normalize(rows[row_index - depth][column]) if column < len(rows[row_index - depth]) else ""
            if previous and previous != current:
                yield column, f"{previous} {current}"


def _column_candidates(rows: list[tuple[Any, ...]], index: int, role: str, width: int) -> dict[str, int]:
    best: dict[str, tuple[int, int]] = {}
    for column, label in _row_headers(rows, index, width):
        for field, variants in ALIASES[role].items():
            points = max((_alias_score(label, alias) for alias in variants), default=0)
            if not points:
                continue
            old = best.get(field)
            if old is None or points > old[0]:
                best[field] = (points, column)

    # One source column cannot silently feed two financial fields.
    used: set[int] = set()
    chosen: dict[str, int] = {}
    for field, (points, column) in sorted(best.items(), key=lambda x: (-x[1][0], x[0])):
        if column not in used:
            chosen[field] = column
            used.add(column)
    return chosen


def _complete(role: str, fields: set[str]) -> bool:
    if role == "SCHEDULE_EXCEL":
        return {"name", "start", "end"} <= fields
    if role == "BOQ":
        return "description" in fields and (
            "amount" in fields or {"quantity", "unit_price"} <= fields
        )
    if role == "VO":
        return "description" in fields and (
            bool(fields & {"variation_quantity", "increase_quantity", "decrease_quantity"})
            or {"contract_quantity", "actual_quantity"} <= fields
        ) and bool(fields & {"amount", "unit_price"})
    return (bool(fields & {"current_amount", "cumulative_amount", "contract_amount"})
            and len(fields & {"current_amount", "cumulative_amount", "contract_amount", "amount",
                              "quantity", "description"}) >= 2)


def _required_description(role: str, fields: set[str]) -> list[str]:
    if _complete(role, fields):
        return []
    if role == "SCHEDULE_EXCEL":
        return sorted({"name", "start", "end"} - fields)
    if role == "BOQ":
        return (["description"] if "description" not in fields else []) + (
            ["amount OR quantity+unit_price"] if "amount" not in fields
            and not {"quantity", "unit_price"} <= fields else []
        )
    if role == "VO":
        return (["description"] if "description" not in fields else []) + (
            ["variation OR contract+actual quantity"] if not (fields & {
                "variation_quantity", "increase_quantity", "decrease_quantity"
            } or {"contract_quantity", "actual_quantity"} <= fields) else []
        ) + (["amount OR unit_price"] if not fields & {"amount", "unit_price"} else [])
    return ["payment amount / cumulative / contract columns"]


def recognize_sheet(
    sheet_name: str,
    rows: Iterable[Iterable[Any]],
    *,
    preferred_role: str | None = None,
    max_rows: int = MAX_HEADER_ROWS,
    max_columns: int = MAX_HEADER_COLUMNS,
) -> dict[str, Any]:
    """Inspect only bounded header rows; never return or alter financial values.

    Returns a suggested role, canonical column indices (zero based), header row
    (one based), and a review flag. A distinct high-confidence layout is not
    permission to persist financial data without the existing importer checks.
    """
    if preferred_role is not None and preferred_role not in ALIASES:
        raise ValueError(f"Unknown Excel role: {preferred_role}")
    grid = [tuple(row)[:max_columns] for _, row in zip(range(max_rows), rows)]
    width = min(max((len(row) for row in grid), default=0), max_columns)
    name = normalize(sheet_name)
    scores: list[dict[str, Any]] = []
    roles = [preferred_role] if preferred_role else list(ALIASES)
    for role in roles:
        best: dict[str, Any] | None = None
        for row_index in range(len(grid)):
            mapping = _column_candidates(grid, row_index, role, width)
            fields = set(mapping)
            full = _complete(role, fields)
            name_hint = any(hint in name for hint in ROLE_HINTS[role])
            score = len(mapping) * 2 + (10 if full else 0) + (3 if name_hint else 0)
            # Avoid promoting a generic cost summary to a BOQ detail sheet.
            if role == "BOQ" and any(h in name for h in SUMMARY_HINTS):
                score -= 8
            proposal = {
                "role": role, "sheet": str(sheet_name), "header_row": row_index + 1,
                "columns": mapping, "score": score, "complete": full,
                "missing": _required_description(role, fields),
            }
            if best is None or (score, len(mapping)) > (best["score"], len(best["columns"])):
                best = proposal
        if best and best["columns"]:
            scores.append(best)
    scores.sort(key=lambda p: (p["complete"], p["score"]), reverse=True)
    if not scores:
        return {
            "sheet": str(sheet_name), "role": None, "status": "unsupported",
            "review_required": True, "header_row": 0, "columns": {}, "missing": [],
            "confidence": 0.0, "fingerprint": "", "candidates": [],
        }
    first = scores[0]
    second = scores[1] if len(scores) > 1 else None
    # Without a preferred role, weak distinctions must be manually reviewed.
    ambiguous = bool(second and second["complete"] == first["complete"]
                     and first["score"] - second["score"] < 4)
    complete = bool(first["complete"] and first["score"] >= 14)
    status = "recognized" if complete and not ambiguous else "review_required"
    identity = first["role"] + ":" + ",".join(sorted(first["columns"]))
    fingerprint = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    return {
        "sheet": str(sheet_name), "role": first["role"] if complete else None,
        "suggested_role": first["role"], "status": status,
        "review_required": status != "recognized",
        "header_row": first["header_row"], "columns": first["columns"],
        "missing": first["missing"], "confidence": round(
            min(0.98, first["score"] / 26.0) if complete and not ambiguous else
            min(0.69, first["score"] / 26.0), 2
        ),
        "fingerprint": fingerprint,
        "candidates": [{"role": x["role"], "score": x["score"],
                        "header_row": x["header_row"], "complete": x["complete"]}
                       for x in scores[:3]],
    }


def analyze_workbook_path(
    path: str | Path,
    *,
    preferred_role: str | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Read visible worksheet headers from an approved local .xlsx/.xlsm path."""
    from openpyxl import load_workbook

    source = Path(path)
    if source.suffix.lower() not in {".xlsx", ".xlsm"}:
        raise ValueError("Template recognition supports .xlsx/.xlsm only.")
    if not source.is_file():
        raise FileNotFoundError(source)
    workbook = load_workbook(str(source), read_only=True, data_only=True, keep_links=False)
    try:
        profiles: list[dict[str, Any]] = []
        for sheet in workbook.worksheets:
            if cancelled and cancelled():
                raise InterruptedError("Excel template recognition cancelled.")
            if sheet.sheet_state != "visible":
                continue
            rows = sheet.iter_rows(
                min_row=1, max_row=min(MAX_HEADER_ROWS, max(1, sheet.max_row or 1)),
                min_col=1, max_col=min(MAX_HEADER_COLUMNS, max(1, sheet.max_column or 1)),
                values_only=True,
            )
            profiles.append(recognize_sheet(sheet.title, rows, preferred_role=preferred_role))
        accepted = [x for x in profiles if x["status"] == "recognized"]
        return {
            "engine": "universal-excel-recognition-v1",
            "source_filename": source.name,
            "preferred_role": preferred_role,
            "sheet_count": len(profiles),
            "recognized_count": len(accepted),
            "review_required": not bool(accepted) or any(
                p["status"] == "review_required" for p in profiles
                if p["suggested_role"] is not None and p["columns"]
            ),
            "sheets": profiles,
        }
    finally:
        workbook.close()
