from __future__ import annotations

import hashlib
import math
import re
from datetime import date, datetime
from typing import Any


PATCH_MARKER = "V6.22 IPC ADAPTIVE PARSER V1"
MAX_ROLE_SCAN_ROWS = 80
MAX_ROLE_SCAN_COLS = 40
MAX_PAYMENT_SCAN_ROWS = 110
MAX_PAYMENT_SCAN_COLS = 16
MAX_METADATA_SCAN_ROWS = 35
MAX_METADATA_SCAN_COLS = 16


def _cell(row: tuple[Any, ...], index: int, default: Any = "") -> Any:
    if index < 0 or index >= len(row):
        return default
    value = row[index]
    return default if value is None else value


def _col_letter(index0: int) -> str:
    value = int(index0) + 1
    out = ""
    while value:
        value, rem = divmod(value - 1, 26)
        out = chr(65 + rem) + out
    return out


def _top_grid(ws, max_rows: int, max_cols: int) -> list[tuple[Any, ...]]:
    if ws is None:
        return []
    cache = getattr(ws, "_qlda_adaptive_grid_cache", None)
    if isinstance(cache, dict):
        key = (int(max_rows), int(max_cols))
        if key in cache:
            return cache[key]
    else:
        try:
            ws._qlda_adaptive_grid_cache = {}
            cache = ws._qlda_adaptive_grid_cache
        except Exception:
            cache = {}

    rows = min(int(getattr(ws, "max_row", 0) or 0), int(max_rows))
    cols = min(int(getattr(ws, "max_column", 0) or 0), int(max_cols))
    if rows <= 0 or cols <= 0:
        return []
    grid = [
        tuple(values)
        for values in ws.iter_rows(
            min_row=1,
            max_row=rows,
            min_col=1,
            max_col=cols,
            values_only=True,
        )
    ]
    try:
        cache[(int(max_rows), int(max_cols))] = grid
    except Exception:
        pass
    return grid


def _role_from_aliases(ipc, aliases: tuple[str, ...]) -> str:
    wanted = " | ".join(ipc._norm(x) for x in aliases)
    if "khai bao" in wanted or "declaration" in wanted:
        return "metadata"
    if "gtht" in wanted or "gia tri hoan thanh" in wanted:
        return "gtht"
    if "thanh toan" in wanted or "payment" in wanted:
        return "payment"
    return ""


def _sheet_role_score(ipc, ws, role: str) -> tuple[float, dict[str, Any]]:
    grid = _top_grid(ws, MAX_ROLE_SCAN_ROWS, MAX_ROLE_SCAN_COLS)
    name = ipc._norm(getattr(ws, "title", ""))
    text_values: list[str] = []
    error_count = 0
    placeholder_count = 0
    large_numeric_count = 0
    for row in grid:
        for value in row:
            if value in (None, ""):
                continue
            text = str(value).strip()
            norm = ipc._norm(text)
            if norm:
                text_values.append(norm)
            upper = text.upper()
            if upper.startswith("#REF!") or upper.startswith("#VALUE!") or upper.startswith("#NAME?") or upper.startswith("#N/A"):
                error_count += 1
            if "…" in text or "...." in text or "____" in text:
                placeholder_count += 1
            number = ipc._to_number(value)
            if number is not None and abs(number) >= 1000:
                large_numeric_count += 1
    blob = " | ".join(text_values)

    role_keywords = {
        "metadata": (
            ("thanh toan lan", 8), ("hop dong so", 5), ("so hop dong", 5),
            ("goi thau", 4), ("nha thau", 4), ("ben nhan thau", 4),
            ("cong trinh", 3), ("du an", 3), ("tu ngay", 2), ("den ngay", 2),
        ),
        "payment": (
            ("bang tong hop de nghi thanh toan", 18),
            ("gia tri de nghi thanh toan ky nay", 18),
            ("luy ke gia tri nghiem thu den het ky nay", 12),
            ("luy ke gia tri dntt den het ky truoc", 10),
            ("gia tri thanh toan ky nay", 10),
            ("gia tri hop dong", 5),
            ("gia tri tru ky nay", 4),
        ),
        "gtht": (
            ("bang tong hop gia tri khoi luong hoan thanh", 18),
            ("hang muc cong viec", 12), ("ten cong tac", 12),
            ("khoi luong nghiem thu vat tu", 8),
            ("khoi luong nghiem thu lap dat", 8),
            ("don gia vat tu", 5), ("thanh tien nghiem thu vat tu", 5),
        ),
    }
    score = 0.0
    for keyword, weight in role_keywords.get(role, ()):
        if keyword in blob:
            score += float(weight)

    if role == "metadata":
        if "khai bao" in name:
            score += 12
        elif "nhap du lieu" in name or "input" in name:
            score += 9
    elif role == "payment":
        if "03 dntt" in name or name == "dntt":
            score += 14
        elif "dntt" in name:
            score += 8
        elif "thanh toan" in name or "payment" in name:
            score += 7
    elif role == "gtht":
        if "gtht" in name or "gt hoan thanh" in name:
            score += 10

    score += min(large_numeric_count, 40) * 0.25
    score -= error_count * 0.45
    score -= placeholder_count * 2.5

    profile = {
        "sheet": str(getattr(ws, "title", "")),
        "role": role,
        "score": round(score, 3),
        "errors": int(error_count),
        "placeholders": int(placeholder_count),
        "large_numeric_cells": int(large_numeric_count),
    }
    return score, profile


def _name_hint(ipc, ws, role: str) -> bool:
    name = ipc._norm(getattr(ws, "title", ""))
    if role == "metadata":
        return any(x in name for x in ("khai bao", "nhap du lieu", "declaration", "input"))
    if role == "payment":
        return any(x in name for x in ("thanh toan", "dntt", "payment", "de nghi"))
    if role == "gtht":
        return any(x in name for x in ("gtht", "gt hoan thanh", "khoi luong hoan thanh"))
    return False


def _resolve_role_sheet(ipc, workbook, role: str):
    visible = [ws for ws in workbook.worksheets if getattr(ws, "sheet_state", "visible") == "visible"]
    candidates = [ws for ws in visible if _name_hint(ipc, ws, role)] or visible
    scored: list[tuple[float, Any, dict[str, Any]]] = []
    for ws in candidates:
        score, profile = _sheet_role_score(ipc, ws, role)
        scored.append((score, ws, profile))
    scored.sort(key=lambda item: item[0], reverse=True)

    # If name-based candidates are weak, fall back to content search across all sheets.
    if scored and scored[0][0] < 12 and len(candidates) != len(visible):
        seen = {id(ws) for ws in candidates}
        for ws in visible:
            if id(ws) in seen:
                continue
            score, profile = _sheet_role_score(ipc, ws, role)
            scored.append((score, ws, profile))
        scored.sort(key=lambda item: item[0], reverse=True)

    if not scored:
        return None, {"sheet": "", "role": role, "score": 0.0}
    best_score, best_ws, best_profile = scored[0]
    best_profile = dict(best_profile)
    best_profile["alternatives"] = [
        {"sheet": p[2]["sheet"], "score": p[2]["score"]}
        for p in scored[1:4]
    ]
    return best_ws, best_profile


def _adaptive_first_sheet_factory(original_first_sheet):
    def adaptive_first_sheet(workbook, aliases: tuple[str, ...]):
        import ipc_claim_v622 as ipc

        role = _role_from_aliases(ipc, aliases)
        if not role:
            return original_first_sheet(workbook, aliases)
        cache = getattr(workbook, "_qlda_adaptive_role_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            try:
                workbook._qlda_adaptive_role_cache = cache
            except Exception:
                pass
        if role not in cache:
            cache[role] = _resolve_role_sheet(ipc, workbook, role)
        ws, profile = cache[role]
        if ws is not None:
            try:
                ws._qlda_adaptive_role_profile = profile
            except Exception:
                pass
            return ws
        return original_first_sheet(workbook, aliases)

    return adaptive_first_sheet


def _is_bad_text(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return True
    upper = text.upper()
    return bool(
        upper.startswith("#REF!")
        or upper.startswith("#VALUE!")
        or upper.startswith("#NAME?")
        or upper.startswith("#N/A")
        or text in {"0", "0.0"}
        or "…" in text
        or "...." in text
    )


def _label_match(ipc, value: Any, alias: str, excludes: tuple[str, ...] = ()) -> bool:
    if not isinstance(value, str):
        return False
    text = ipc._norm(value)
    wanted = ipc._norm(alias)
    if not wanted or wanted not in text:
        return False
    return not any(ipc._norm(x) and ipc._norm(x) in text for x in excludes)


def _value_to_right(
    ipc,
    grid: list[tuple[Any, ...]],
    row_index: int,
    col_index: int,
    *,
    numeric: bool = False,
    max_steps: int = 12,
) -> tuple[Any, int] | tuple[None, None]:
    row = grid[row_index]
    candidates: list[tuple[float, int, Any]] = []
    for index in range(col_index + 1, min(len(row), col_index + max_steps + 1)):
        value = row[index]
        if value in (None, ""):
            continue
        if numeric:
            number = ipc._to_number(value)
            if number is None:
                continue
            # Money fields can have a nearby ratio column. Prefer the large monetary value.
            magnitude_bonus = 4.0 if abs(number) >= 1000 else 0.0
            candidates.append((magnitude_bonus - (index - col_index) * 0.03, index, float(number)))
        else:
            if _is_bad_text(value):
                continue
            return value, index
    if numeric and candidates:
        candidates.sort(key=lambda item: (item[0], abs(float(item[2]))), reverse=True)
        _, index, value = candidates[0]
        return value, index
    return None, None


def _find_labeled_value(
    ipc,
    grid: list[tuple[Any, ...]],
    aliases: tuple[str, ...],
    *,
    numeric: bool = False,
    excludes: tuple[str, ...] = (),
) -> tuple[Any, float, str]:
    for alias_rank, alias in enumerate(aliases):
        for r, row in enumerate(grid):
            for c, label in enumerate(row):
                if not _label_match(ipc, label, alias, excludes):
                    continue
                value, value_col = _value_to_right(ipc, grid, r, c, numeric=numeric)
                if value_col is not None:
                    confidence = max(0.75, 0.99 - alias_rank * 0.04)
                    return value, confidence, f"{_col_letter(value_col)}{r + 1}"
    return None, 0.0, ""


def _clean_date(ipc, value: Any) -> str:
    if value in (None, "", 0, 0.0):
        return ""
    if isinstance(value, (date, datetime)):
        return ipc._date_text(value)
    text = str(value or "").strip()
    if not text or text in {"0", "0.0"} or text.upper().startswith("#"):
        return ""
    parsed = ipc._date_text(value)
    return "" if parsed in {"0", "0.0"} else str(parsed or "")


def adaptive_metadata_from_declaration(ws) -> dict[str, Any]:
    if ws is None:
        return {}
    import ipc_claim_v622 as ipc

    grid = _top_grid(ws, MAX_METADATA_SCAN_ROWS, MAX_METADATA_SCAN_COLS)
    fields: dict[str, float] = {}
    sources: dict[str, str] = {}

    def text_field(name: str, aliases: tuple[str, ...], fallback: Any = "") -> str:
        value, conf, source = _find_labeled_value(ipc, grid, aliases, numeric=False)
        if value in (None, ""):
            value = fallback
            conf = 0.55 if fallback not in (None, "") else 0.0
        fields[name] = conf
        sources[name] = source
        return str(value or "").strip()

    project = text_field("project", ("DỰ ÁN", "CÔNG TRÌNH"))
    location = text_field("location", ("ĐỊA ĐIỂM",))
    package = text_field("package", ("GÓI THẦU",))
    contractor = text_field("contractor", ("NHÀ THẦU THI CÔNG TRỰC TIẾP", "BÊN NHẬN THẦU", "NHÀ THẦU"))
    account_name = text_field("account_name", ("CHỦ THẺ", "NGƯỜI HƯỞNG THỤ"), contractor)
    account_no = text_field("account_no", ("SỐ TÀI KHOẢN", "TÀI KHOẢN"))
    bank = text_field("bank", ("NGÂN HÀNG",))
    bank_branch = text_field("bank_branch", ("CHI NHÁNH",))
    claim_no = text_field("claim_no", ("THANH TOÁN LẦN", "THANH TOÁN LẦN:", "LẦN"))
    currency = text_field("currency", ("ĐƠN VỊ TÍNH",), "VNĐ") or "VNĐ"
    contract_no = text_field("contract_no", ("HỢP ĐỒNG SỐ", "SỐ HỢP ĐỒNG"))

    from_raw, from_conf, from_source = _find_labeled_value(ipc, grid, ("TỪ NGÀY",), numeric=False)
    to_raw, to_conf, to_source = _find_labeled_value(ipc, grid, ("ĐẾN NGÀY",), numeric=False)
    from_date = _clean_date(ipc, from_raw)
    to_date = _clean_date(ipc, to_raw)
    fields["from_date"] = from_conf if from_date else 0.0
    fields["to_date"] = to_conf if to_date else 0.0
    sources["from_date"] = from_source
    sources["to_date"] = to_source

    role_profile = dict(getattr(ws, "_qlda_adaptive_role_profile", {}) or {})
    essential = [fields.get(k, 0.0) for k in ("contractor", "claim_no", "contract_no", "package")]
    confidence = sum(essential) / len(essential) if essential else 0.0

    return {
        "project": project,
        "location": location,
        "package": package,
        "contractor": contractor,
        "account_name": account_name,
        "account_no": account_no,
        "bank": bank,
        "bank_branch": bank_branch,
        "claim_no": claim_no,
        "from_date": from_date,
        "to_date": to_date,
        "currency": currency,
        "contract_no": contract_no,
        "_adaptive": {
            "sheet": str(getattr(ws, "title", "")),
            "role_profile": role_profile,
            "field_confidence": fields,
            "sources": sources,
            "confidence": round(confidence, 4),
        },
    }


def adaptive_payment_summary(ws) -> dict[str, Any]:
    if ws is None:
        return {}
    import ipc_claim_v622 as ipc

    grid = _top_grid(ws, MAX_PAYMENT_SCAN_ROWS, MAX_PAYMENT_SCAN_COLS)
    field_conf: dict[str, float] = {}
    sources: dict[str, str] = {}

    def money(name: str, aliases: tuple[str, ...], excludes: tuple[str, ...] = (), fallbacks: tuple[str, ...] = ()) -> float:
        value, conf, source = _find_labeled_value(ipc, grid, aliases, numeric=True, excludes=excludes)
        if value is None:
            for ref in fallbacks:
                number = ipc._to_number(ipc._safe_cell(ws, ref))
                if number is not None:
                    value, conf, source = float(number), 0.55, ref
                    break
        field_conf[name] = conf
        sources[name] = source
        return float(value or 0)

    contract_value = money(
        "contract_value",
        ("GIÁ TRỊ HỢP ĐỒNG + PLHĐ", "GIÁ TRỊ HỢP ĐỒNG PLHĐ", "GIÁ TRỊ HỢP ĐỒNG"),
        excludes=("GỐC", "TẠM ỨNG"),
        fallbacks=("D10", "D11", "F11"),
    )
    contract_advance = money(
        "contract_advance",
        ("GIÁ TRỊ TẠM ỨNG HĐ + PLHĐ", "GIÁ TRỊ TẠM ỨNG HĐ PLHĐ", "GIÁ TRỊ TẠM ỨNG HĐ"),
        fallbacks=("D14", "D12"),
    )
    material_advance = money(
        "material_advance", ("GIÁ TRỊ TẠM ỨNG VẬT TƯ",), fallbacks=("D16", "D14", "D13")
    )
    cumulative_acceptance = money(
        "cumulative_acceptance",
        ("LŨY KẾ GIÁ TRỊ NGHIỆM THU ĐẾN HẾT KỲ NÀY", "LŨY KẾ GIÁ TRỊ NGHIỆM THU ĐẾN NAY"),
        fallbacks=("D17", "D15", "D14"),
    )
    cumulative_material_acceptance = money(
        "cumulative_material_acceptance",
        ("GIÁ TRỊ NGHIỆM THU VẬT TƯ [6A]", "LŨY KẾ GIÁ TRỊ NGHIỆM THU VẬT TƯ"),
        fallbacks=("D18", "D16", "D15"),
    )
    cumulative_installation_acceptance = money(
        "cumulative_installation_acceptance",
        ("GIÁ TRỊ NGHIỆM THU LẮP ĐẶT [6B]", "LŨY KẾ GIÁ TRỊ NGHIỆM THU LẮP ĐẶT"),
        fallbacks=("F19", "F17", "F16", "D16"),
    )
    cumulative_material_deduction = money(
        "cumulative_material_deduction",
        ("LŨY KẾ KHẤU TRỪ GIÁ TRỊ VẬT TƯ LẮP ĐẶT",),
        fallbacks=("F20", "F18", "F17", "D17"),
    )
    previous_approved = money(
        "previous_approved",
        ("LŨY KẾ GIÁ TRỊ ĐNTT ĐẾN HẾT KỲ TRƯỚC", "LŨY KẾ GIÁ TRỊ ĐÃ DUYỆT CÁC KỲ TRƯỚC"),
        fallbacks=("D22", "D20", "D19"),
    )
    cumulative_retention = money(
        "cumulative_retention",
        ("LŨY KẾ GIÁ TRỊ HĐ, PLHĐ BẢO LƯU ĐẾN HẾT KỲ NÀY", "LŨY KẾ BẢO LƯU ĐẾN NAY"),
        fallbacks=("D28", "D26", "D25"),
    )
    cumulative_advance_recovery = money(
        "cumulative_advance_recovery",
        ("LŨY KẾ GIÁ TRỊ KHẤU TRỪ TẠM ỨNG HĐ, PLHĐ", "LŨY KẾ THU HỒI TẠM ỨNG ĐẾN NAY"),
        fallbacks=("D31", "D29", "D28"),
    )
    cumulative_material_advance_recovery = money(
        "cumulative_material_advance_recovery",
        ("LŨY KẾ THU HỒI TẠM ỨNG VẬT TƯ",),
        fallbacks=("D32", "D30", "D29"),
    )
    cumulative_deductions = money(
        "cumulative_deductions",
        ("LŨY KẾ GIÁ TRỊ KHẤU TRỪ, GIỮ LẠI ĐẾN HẾT KỲ NÀY", "LŨY KẾ GIÁ TRỊ KHẤU TRỪ GIỮ LẠI ĐẾN HẾT KỲ NÀY"),
    )
    current_gross = money(
        "current_gross",
        ("GIÁ TRỊ THANH TOÁN KỲ NÀY CHƯA TRỪ", "GIÁ TRỊ NGHIỆM THU KỲ NÀY"),
        excludes=("BẰNG CHỮ",),
        fallbacks=("D34", "D32", "D31"),
    )
    current_deductions = money(
        "current_deductions",
        ("GIÁ TRỊ TRỪ KỲ NÀY", "[17]=[17A+17B+17C+17D]"),
        fallbacks=("K24", "K27", "K25"),
    )
    requested_amount = money(
        "requested_amount",
        ("GIÁ TRỊ ĐỀ NGHỊ THANH TOÁN KỲ NÀY", "GIÁ TRỊ ĐNTT KỲ NÀY", "GIÁ TRỊ THANH TOÁN KỲ NÀY"),
        excludes=("CHƯA TRỪ", "BẰNG CHỮ", "NGHIỆM THU"),
        fallbacks=("K33", "K31", "K30"),
    )

    words, words_conf, words_source = _find_labeled_value(
        ipc,
        grid,
        ("GIÁ TRỊ ĐNTT BẰNG CHỮ", "GIÁ TRỊ THANH TOÁN KỲ NÀY, BẰNG CHỮ", "BẰNG CHỮ"),
        numeric=False,
    )
    amount_in_words = str(words or "").strip()
    field_conf["amount_in_words"] = words_conf
    sources["amount_in_words"] = words_source

    validation: dict[str, Any] = {}
    if cumulative_acceptance and cumulative_deductions and previous_approved and requested_amount:
        calculated = cumulative_acceptance - cumulative_deductions - previous_approved
        delta = requested_amount - calculated
        tolerance = max(2.0, abs(requested_amount) * 1e-8)
        validation["claim_formula"] = {
            "formula": "cumulative_acceptance - cumulative_deductions - previous_approved",
            "calculated": calculated,
            "actual": requested_amount,
            "delta": delta,
            "ok": abs(delta) <= tolerance,
        }

    essential_names = ("contract_value", "requested_amount", "cumulative_acceptance")
    essential = [field_conf.get(name, 0.0) for name in essential_names]
    confidence = sum(essential) / len(essential) if essential else 0.0
    formula_check = validation.get("claim_formula") or {}
    if formula_check.get("ok"):
        confidence = min(1.0, confidence + 0.04)
    elif formula_check:
        confidence = max(0.0, confidence - 0.15)

    prior_total_deductions = cumulative_deductions
    if not prior_total_deductions:
        prior_total_deductions = previous_approved + cumulative_retention + cumulative_advance_recovery + cumulative_material_advance_recovery

    return {
        "contract_value": contract_value,
        "contract_advance": contract_advance,
        "material_advance": material_advance,
        "cumulative_acceptance": cumulative_acceptance,
        "cumulative_material_acceptance": cumulative_material_acceptance,
        "cumulative_installation_acceptance": cumulative_installation_acceptance,
        "cumulative_material_deduction": cumulative_material_deduction,
        "cumulative_completed": cumulative_acceptance,
        "previous_approved": previous_approved,
        "cumulative_retention": cumulative_retention,
        "cumulative_advance_recovery": cumulative_advance_recovery,
        "cumulative_material_advance_recovery": cumulative_material_advance_recovery,
        "prior_total_deductions": prior_total_deductions,
        "current_gross": current_gross,
        "current_deductions": current_deductions,
        "requested_amount": requested_amount,
        "amount_in_words": amount_in_words,
        "_adaptive": {
            "sheet": str(getattr(ws, "title", "")),
            "role_profile": dict(getattr(ws, "_qlda_adaptive_role_profile", {}) or {}),
            "field_confidence": field_conf,
            "sources": sources,
            "validation": validation,
            "confidence": round(confidence, 4),
        },
    }


def _header_row_for_new_gtht(ipc, ws) -> tuple[int | None, list[tuple[Any, ...]]]:
    grid = _top_grid(ws, 35, 32)
    for r, row in enumerate(grid):
        for value in row[:10]:
            norm = ipc._norm(value)
            if "hang muc cong viec" in norm or "noi dung cong viec" in norm:
                return r + 1, grid
    return None, grid


def _composite_headers(ipc, grid: list[tuple[Any, ...]], header_row: int, max_cols: int = 32) -> list[str]:
    base = list(grid[header_row - 1]) if header_row - 1 < len(grid) else []
    sub1 = list(grid[header_row]) if header_row < len(grid) else []
    sub2 = list(grid[header_row + 1]) if header_row + 1 < len(grid) else []
    base += [None] * (max_cols - len(base))
    sub1 += [None] * (max_cols - len(sub1))
    sub2 += [None] * (max_cols - len(sub2))
    carried = ""
    out: list[str] = []
    for c in range(max_cols):
        if base[c] not in (None, ""):
            carried = str(base[c])
        parts = [carried, str(sub1[c] or ""), str(sub2[c] or "")]
        out.append(ipc._norm(" | ".join(parts)))
    return out


def _find_header_col(headers: list[str], include: tuple[str, ...], exclude: tuple[str, ...] = ()) -> int | None:
    for index, text in enumerate(headers):
        if all(part in text for part in include) and not any(part in text for part in exclude):
            return index
    return None


def adaptive_parse_gtht_factory(legacy_parser):
    def adaptive_parse_gtht(ws) -> list[dict[str, Any]]:
        if ws is None:
            return []
        import ipc_claim_v622 as ipc

        header_row, grid = _header_row_for_new_gtht(ipc, ws)
        if header_row is None:
            return legacy_parser(ws)

        headers = _composite_headers(ipc, grid, header_row, 32)
        cols = {
            "seq": _find_header_col(headers, ("stt",)),
            "description": _find_header_col(headers, ("hang muc cong viec",)),
            "spec": _find_header_col(headers, ("quy cach",)),
            "unit": _find_header_col(headers, ("dvt",)),
            "material_unit_price": _find_header_col(headers, ("don gia vat tu",)),
            "labor_unit_price": _find_header_col(headers, ("don gia lap dat",)),
            "contract_qty": _find_header_col(headers, ("hop dong", "khoi luong")),
            "contract_amount": _find_header_col(headers, ("hop dong", "thanh tien")),
            "material_previous_qty": _find_header_col(headers, ("khoi luong nghiem thu vat tu", "lk ky truoc")),
            "material_current_qty": _find_header_col(headers, ("khoi luong nghiem thu vat tu", "ky nay"), ("lk den het",)),
            "material_cumulative_qty": _find_header_col(headers, ("khoi luong nghiem thu vat tu", "lk den het ky nay")),
            "installation_previous_qty": _find_header_col(headers, ("khoi luong nghiem thu lap dat", "lk ky truoc")),
            "installation_current_qty": _find_header_col(headers, ("khoi luong nghiem thu lap dat", "ky nay"), ("lk den het",)),
            "installation_cumulative_qty": _find_header_col(headers, ("khoi luong nghiem thu lap dat", "lk den het ky nay")),
            "material_previous_value": _find_header_col(headers, ("thanh tien nghiem thu vat tu", "lk ky truoc"), ("da lap dat",)),
            "material_current_value": _find_header_col(headers, ("thanh tien nghiem thu vat tu", "ky nay"), ("lk den het", "da lap dat")),
            "material_cumulative_value": _find_header_col(headers, ("thanh tien nghiem thu vat tu", "lk den het ky nay"), ("da lap dat",)),
            "installation_previous_value": _find_header_col(headers, ("thanh tien nghiem thu lap dat", "lk ky truoc")),
            "installation_current_value": _find_header_col(headers, ("thanh tien nghiem thu lap dat", "ky nay"), ("lk den het",)),
            "installation_cumulative_value": _find_header_col(headers, ("thanh tien nghiem thu lap dat", "lk den het ky nay")),
            "note": _find_header_col(headers, ("ghi chu",)),
            "cost_code": _find_header_col(headers, ("code",)),
            "system": _find_header_col(headers, ("he thong",)),
            "boq_sheet": _find_header_col(headers, ("sheet theo boq",)),
        }
        if cols["description"] is None or cols["unit"] is None or cols["contract_qty"] is None:
            return legacy_parser(ws)

        max_col = max(index for index in cols.values() if index is not None) + 1
        max_col = max(max_col, 26)
        max_row = int(getattr(ws, "max_row", 0) or 0)
        start_row = header_row + 3
        out: list[dict[str, Any]] = []
        num = ipc._to_number

        def at(values: tuple[Any, ...], key: str, default: Any = "") -> Any:
            index = cols.get(key)
            return _cell(values, index, default) if index is not None else default

        for row_no, raw in enumerate(
            ws.iter_rows(min_row=start_row, max_row=max_row, min_col=1, max_col=max_col, values_only=True),
            start=start_row,
        ):
            values = tuple(raw)
            description = str(at(values, "description") or "").strip()
            if not description:
                continue
            norm_desc = ipc._norm(description)
            if norm_desc.startswith("tong cong") or norm_desc.startswith("tong hop") or norm_desc in {"tong", "tong cong"}:
                continue

            seq = num(at(values, "seq"))
            contract_qty = num(at(values, "contract_qty"))
            unit = str(at(values, "unit") or "").strip()
            material_price = num(at(values, "material_unit_price")) or 0
            labor_price = num(at(values, "labor_unit_price")) or 0
            is_detail = bool(
                (contract_qty is not None and contract_qty > 0)
                or (seq is not None and seq > 0 and (unit or material_price or labor_price))
            )
            if not is_detail:
                continue

            material_previous_qty = num(at(values, "material_previous_qty")) or 0
            material_current_qty = num(at(values, "material_current_qty")) or 0
            material_cumulative_qty = num(at(values, "material_cumulative_qty")) or 0
            installation_previous_qty = num(at(values, "installation_previous_qty")) or 0
            installation_current_qty = num(at(values, "installation_current_qty")) or 0
            installation_cumulative_qty = num(at(values, "installation_cumulative_qty")) or 0
            material_previous_value = num(at(values, "material_previous_value")) or 0
            material_current_value = num(at(values, "material_current_value")) or 0
            material_cumulative_value = num(at(values, "material_cumulative_value")) or 0
            installation_previous_value = num(at(values, "installation_previous_value")) or 0
            installation_current_value = num(at(values, "installation_current_value")) or 0
            installation_cumulative_value = num(at(values, "installation_cumulative_value")) or 0
            contract_amount = num(at(values, "contract_amount")) or 0
            ratio = 0.0
            if contract_qty and contract_qty > 0:
                ratio = max(float(material_cumulative_qty), float(installation_cumulative_qty)) / float(contract_qty)

            out.append(
                {
                    "sheet_name": str(getattr(ws, "title", "")),
                    "row_no": row_no,
                    "seq": seq,
                    "boq_item": description,
                    "contract_qty": float(contract_qty or 0),
                    "unit": unit,
                    "spec": str(at(values, "spec") or "").strip(),
                    "item_code": str(at(values, "cost_code") or "").strip(),
                    "brand": "",
                    "origin": "",
                    "material_unit_price": float(material_price),
                    "labor_unit_price": float(labor_price),
                    "contract_amount": float(contract_amount),
                    "material_previous_qty": float(material_previous_qty),
                    "material_current_qty": float(material_current_qty),
                    "material_cumulative_qty": float(material_cumulative_qty),
                    # Backward-compatible DB column names. New IPC forms store quantities here, not percentages.
                    "installation_previous_pct": float(installation_previous_qty),
                    "installation_current_pct": float(installation_current_qty),
                    "installation_cumulative_pct": float(installation_cumulative_qty),
                    "installation_previous_value": float(installation_previous_value),
                    "installation_current_value": float(installation_current_value),
                    "installation_cumulative_value": float(installation_cumulative_value),
                    "material_previous_value": float(material_previous_value),
                    "material_current_value": float(material_current_value),
                    "material_cumulative_value": float(material_cumulative_value),
                    "deduction_previous": 0.0,
                    "deduction_current": 0.0,
                    "deduction_cumulative": 0.0,
                    "current_value": float(material_current_value + installation_current_value),
                    "cumulative_value": float(material_cumulative_value + installation_cumulative_value),
                    "completion_ratio": float(ratio),
                    "note": str(at(values, "note") or "").strip(),
                    "cost_code": str(at(values, "cost_code") or "").strip(),
                    "system": str(at(values, "system") or "").strip(),
                    "boq_sheet": str(at(values, "boq_sheet") or "").strip(),
                    "installation_measure": "quantity",
                }
            )

        mapped = sum(1 for value in cols.values() if value is not None)
        essential = ("description", "unit", "contract_qty", "material_current_value", "installation_current_value")
        essential_ok = sum(1 for key in essential if cols.get(key) is not None)
        confidence = min(1.0, 0.55 + essential_ok * 0.07 + min(mapped, 16) * 0.006)
        if not out:
            confidence = min(confidence, 0.5)
        trace = {
            "sheet": str(getattr(ws, "title", "")),
            "role_profile": dict(getattr(ws, "_qlda_adaptive_role_profile", {}) or {}),
            "header_row": header_row,
            "columns": {key: (_col_letter(value) if value is not None else "") for key, value in cols.items()},
            "layout": "semantic_multirow_gtht",
            "confidence": round(confidence, 4),
            "detail_rows": len(out),
        }
        if out:
            out[0]["_adaptive_trace"] = trace
        return out

    return adaptive_parse_gtht


def _adaptive_parse_wrapper_factory(original_parse):
    def adaptive_parse(data: bytes, filename: str = "IPC.xlsx") -> dict[str, Any]:
        result = original_parse(data, filename)
        parsed = dict(result or {})
        metadata = dict(parsed.get("metadata") or {})
        summary = dict(parsed.get("summary") or {})
        meta_trace = dict(metadata.get("_adaptive") or {})
        payment_trace = dict(summary.get("_adaptive") or {})
        detail_trace: dict[str, Any] = {}
        for item in parsed.get("detail_items") or []:
            if isinstance(item, dict) and item.get("_adaptive_trace"):
                detail_trace = dict(item.get("_adaptive_trace") or {})
                break

        parts = []
        weights = []
        if payment_trace:
            parts.append(float(payment_trace.get("confidence") or 0) * 0.55)
            weights.append(0.55)
        if detail_trace:
            parts.append(float(detail_trace.get("confidence") or 0) * 0.30)
            weights.append(0.30)
        if meta_trace:
            parts.append(float(meta_trace.get("confidence") or 0) * 0.15)
            weights.append(0.15)
        confidence = sum(parts) / sum(weights) if weights else 0.0

        role_map = {
            "metadata": str(meta_trace.get("sheet") or ""),
            "payment": str(payment_trace.get("sheet") or ""),
            "gtht": str(detail_trace.get("sheet") or ""),
        }
        signature_text = "|".join(
            [
                PATCH_MARKER,
                role_map["metadata"], role_map["payment"], role_map["gtht"],
                str(detail_trace.get("header_row") or ""),
                repr(sorted((detail_trace.get("columns") or {}).items())),
            ]
        )
        validation = dict(payment_trace.get("validation") or {})
        profile = {
            "parser": PATCH_MARKER,
            "confidence": round(confidence, 4),
            "confidence_pct": round(confidence * 100, 1),
            "sheet_roles": role_map,
            "payment_sources": dict(payment_trace.get("sources") or {}),
            "field_confidence": dict(payment_trace.get("field_confidence") or {}),
            "gtht_mapping": dict(detail_trace.get("columns") or {}),
            "validation": validation,
            "template_signature": hashlib.sha256(signature_text.encode("utf-8")).hexdigest()[:16],
        }
        parsed["parser_profile"] = profile

        warnings = list(parsed.get("warnings") or [])
        formula_check = validation.get("claim_formula") or {}
        if formula_check and not formula_check.get("ok"):
            warning = (
                "CẢNH BÁO KIỂM TRA CLAIM: công thức đối chiếu Giá trị đề nghị = "
                "Lũy kế nghiệm thu - Lũy kế khấu trừ/giữ lại - Lũy kế ĐNTT kỳ trước chưa khớp. "
                "Hãy kiểm tra mapping trước khi lưu."
            )
            if warning not in warnings:
                warnings.insert(0, warning)
        if confidence < 0.70:
            warning = (
                f"ĐỘ TIN CẬY NHẬN DẠNG THẤP ({confidence * 100:.1f}%): mẫu IPC đã thay đổi nhiều. "
                "Hệ thống không nên tự lưu trước khi người dùng kiểm tra mapping sheet/trường."
            )
            if warning not in warnings:
                warnings.insert(0, warning)
        elif confidence < 0.90:
            warning = (
                f"Độ tin cậy nhận dạng {confidence * 100:.1f}%. Hãy kiểm tra sheet nguồn và giá trị chính trước khi lưu Claim."
            )
            if warning not in warnings:
                warnings.append(warning)
        parsed["warnings"] = warnings
        return parsed

    return adaptive_parse


def install_ipc_adaptive_parser() -> None:
    """Install content-based IPC recognition for both legacy and changing forms.

    Rules:
      1. Select sheet roles from content, not fixed names alone.
      2. Read financial fields from semantic labels; fixed cells are fallback only.
      3. Cross-check Claim arithmetic when the workbook exposes the required totals.
      4. Persist a template signature/mapping inside the Claim workbook payload.
      5. Emit confidence so low-confidence layouts require user confirmation.
    """
    import ipc_claim_v622 as ipc

    if getattr(ipc, "_qlda_ipc_adaptive_parser_installed", False):
        return

    original_first_sheet = ipc._first_sheet
    legacy_gtht = ipc._parse_gtht
    ipc._first_sheet = _adaptive_first_sheet_factory(original_first_sheet)
    ipc._metadata_from_declaration = adaptive_metadata_from_declaration
    ipc._payment_summary = adaptive_payment_summary
    ipc._parse_gtht = adaptive_parse_gtht_factory(legacy_gtht)

    original_parse = ipc.parse_ipc_workbook
    ipc.parse_ipc_workbook = _adaptive_parse_wrapper_factory(original_parse)
    ipc._qlda_ipc_adaptive_parser_installed = True
    ipc._qlda_ipc_adaptive_parser_marker = PATCH_MARKER
