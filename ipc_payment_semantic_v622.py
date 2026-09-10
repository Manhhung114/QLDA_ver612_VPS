from __future__ import annotations

import math
import re
import unicodedata
from typing import Any


PATCH_MARKER = "V6.22 IPC PAYMENT SEMANTIC SECTIONS V1"
MAX_SCAN_ROWS = 220
MAX_SCAN_COLS = 24


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("đ", "d")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _number(value: Any) -> float | None:
    try:
        import ipc_claim_v622 as ipc
        return ipc._to_number(value)
    except Exception:
        pass
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except Exception:
        return None


def _close(a: float, b: float) -> bool:
    tolerance = max(2.0, abs(float(b)) * 2e-8)
    return abs(float(a) - float(b)) <= tolerance


def _money_from_row(row: list[Any]) -> tuple[float | None, int | None]:
    """Pick the monetary value from a summary row, ignoring ratios/percentages."""
    candidates: list[tuple[float, float, int, float]] = []
    for index, value in enumerate(row):
        number = _number(value)
        if number is None:
            continue
        magnitude = abs(float(number))
        # Payment summary money is normally >= 1,000 VND. Keep a small-value
        # fallback for synthetic/tests, but strongly prefer real monetary cells.
        score = 10.0 if magnitude >= 1000 else (4.0 if magnitude >= 100 else 0.0)
        # Values such as 0.75 / 75% are ratios and must never win over money.
        if magnitude <= 1.5:
            score -= 20.0
        candidates.append((score, magnitude, -index, float(number)))
    if not candidates:
        return None, None
    candidates.sort(reverse=True)
    score, _, neg_index, number = candidates[0]
    if score < 0:
        return None, None
    return number, -neg_index


def _section_from_parent(text: str) -> str:
    if "nghiem thu" not in text:
        return ""
    if any(x in text for x in ("vat tu", "vat lieu", "lap dat", "nhan cong")):
        return ""
    if "luy ke" in text and any(x in text for x in ("ky truoc", "den het ky truoc")):
        return "previous"
    if "luy ke" in text and any(x in text for x in ("den het ky nay", "den nay", "het ky nay")):
        return "cumulative"
    if "ky nay" in text and "luy ke" not in text:
        return "current"
    return ""


def _component_from_label(text: str) -> str:
    if "nghiem thu" not in text:
        return ""
    if "vat tu" in text or "vat lieu" in text:
        return "material"
    if "lap dat" in text or "nhan cong" in text:
        return "installation"
    return ""


def _explicit_section_from_child(text: str) -> str:
    if "luy ke" in text and any(x in text for x in ("ky truoc", "den het ky truoc")):
        return "previous"
    if "luy ke" in text and any(x in text for x in ("den het ky nay", "den nay", "het ky nay")):
        return "cumulative"
    if "ky nay" in text and "luy ke" not in text:
        return "current"
    return ""


def _legacy_bracket_slot(raw_text: str) -> tuple[str, str]:
    """Secondary hint for classic [4a]/[5a]/[6a] forms; never the primary rule."""
    compact = unicodedata.normalize("NFKD", str(raw_text or "")).lower()
    matches = re.findall(r"\[\s*([456])\s*([ab]?)\s*\]", compact)
    if not matches:
        return "", ""
    number, suffix = matches[-1]
    section = {"4": "previous", "5": "current", "6": "cumulative"}.get(number, "")
    component = {"a": "material", "b": "installation"}.get(suffix, "")
    return section, component


def _derive_period_identity(sections: dict[str, dict[str, Any]], field: str, validation: dict[str, Any]) -> None:
    previous = sections["previous"].get(field)
    current = sections["current"].get(field)
    cumulative = sections["cumulative"].get(field)

    if previous is not None and cumulative is not None:
        derived_current = float(cumulative) - float(previous)
        validation[f"{field}_current_from_cumulative_minus_previous"] = derived_current
        if current is None:
            sections["current"][field] = derived_current
            sections["current"][f"{field}_source"] = "derived:cumulative-previous"
        else:
            ok = _close(float(current), derived_current)
            validation[f"{field}_current_identity_ok"] = ok
            validation[f"{field}_current_direct"] = float(current)
            validation[f"{field}_current_delta"] = float(current) - derived_current
            # The period identity is the control rule. If a shifted form caused
            # the direct row to be mapped incorrectly, do not propagate it.
            if not ok:
                sections["current"][field] = derived_current
                sections["current"][f"{field}_source"] = "derived:cumulative-previous;direct-conflict"

    previous = sections["previous"].get(field)
    current = sections["current"].get(field)
    cumulative = sections["cumulative"].get(field)
    if previous is None and current is not None and cumulative is not None:
        sections["previous"][field] = float(cumulative) - float(current)
        sections["previous"][f"{field}_source"] = "derived:cumulative-current"
    if cumulative is None and previous is not None and current is not None:
        sections["cumulative"][field] = float(previous) + float(current)
        sections["cumulative"][f"{field}_source"] = "derived:previous+current"


def _validate_sections(sections: dict[str, dict[str, Any]]) -> dict[str, Any]:
    validation: dict[str, Any] = {}
    for field in ("total", "material", "installation"):
        _derive_period_identity(sections, field, validation)

    for section_name, section in sections.items():
        total = section.get("total")
        material = section.get("material")
        installation = section.get("installation")
        if total is not None and material is not None and installation is not None:
            calculated = float(material) + float(installation)
            validation[f"{section_name}_components_ok"] = _close(calculated, float(total))
            validation[f"{section_name}_components_delta"] = calculated - float(total)

    for field in ("total", "material", "installation"):
        previous = sections["previous"].get(field)
        current = sections["current"].get(field)
        cumulative = sections["cumulative"].get(field)
        if previous is not None and current is not None and cumulative is not None:
            calculated = float(previous) + float(current)
            validation[f"{field}_period_sum_ok"] = _close(calculated, float(cumulative))
            validation[f"{field}_period_sum_delta"] = calculated - float(cumulative)
    return validation


def semantic_payment_summary_from_rows(rows: list[list[Any]], *, sheet_name: str = "") -> dict[str, Any]:
    """Recognize previous/current/cumulative acceptance blocks by meaning, not cells."""
    sections: dict[str, dict[str, Any]] = {
        "previous": {},
        "current": {},
        "cumulative": {},
    }
    active_section = ""
    hits: list[dict[str, Any]] = []

    for row_index, raw_row in enumerate(rows[:MAX_SCAN_ROWS], start=1):
        row = list(raw_row[:MAX_SCAN_COLS])
        raw_text = " | ".join(str(v) for v in row if isinstance(v, str) and str(v).strip())
        text = _norm(raw_text)
        if not text:
            continue

        parent_section = _section_from_parent(text)
        value, value_col = _money_from_row(row)
        if parent_section:
            active_section = parent_section
            if value is not None:
                sections[parent_section]["total"] = float(value)
                sections[parent_section]["total_source"] = f"{sheet_name}:row{row_index}:col{(value_col or 0)+1}"
                hits.append({"row": row_index, "section": parent_section, "field": "total", "value": float(value)})
            continue

        component = _component_from_label(text)
        if component:
            explicit = _explicit_section_from_child(text)
            bracket_section, bracket_component = _legacy_bracket_slot(raw_text)
            section = explicit or active_section or bracket_section
            if not component and bracket_component:
                component = bracket_component
            if section and value is not None:
                sections[section][component] = float(value)
                sections[section][f"{component}_source"] = f"{sheet_name}:row{row_index}:col{(value_col or 0)+1}"
                hits.append({"row": row_index, "section": section, "field": component, "value": float(value)})
            continue

        # Stop carrying an acceptance section into unrelated deduction/payment blocks.
        if active_section and any(x in text for x in ("khau tru", "tam ung", "bao luu", "thanh toan", "dntt")):
            active_section = ""

    validation = _validate_sections(sections)
    present = sum(
        1 for section in sections.values() for key in ("total", "material", "installation")
        if section.get(key) is not None
    )
    checks = [value for key, value in validation.items() if key.endswith("_ok")]
    passed = sum(1 for value in checks if value is True)
    failed = sum(1 for value in checks if value is False)
    score = present * 2 + passed * 3 - failed * 4

    result = {
        "sheet": str(sheet_name or ""),
        "sections": sections,
        "validation": validation,
        "hits": hits,
        "present_fields": present,
        "score": score,
        "ok": bool(present >= 4 and score > 0),
    }
    if not result["ok"]:
        return result

    mapping = {
        "previous_acceptance": ("previous", "total"),
        "previous_material_acceptance": ("previous", "material"),
        "previous_installation_acceptance": ("previous", "installation"),
        "current_acceptance": ("current", "total"),
        "current_material_acceptance": ("current", "material"),
        "current_installation_acceptance": ("current", "installation"),
        "cumulative_acceptance": ("cumulative", "total"),
        "cumulative_material_acceptance": ("cumulative", "material"),
        "cumulative_installation_acceptance": ("cumulative", "installation"),
    }
    for name, (section_name, field) in mapping.items():
        value = sections[section_name].get(field)
        if value is not None:
            result[name] = float(value)
            result[f"{name}_source"] = str(sections[section_name].get(f"{field}_source") or "")
    return result


def semantic_payment_summary_from_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    rows = []
    columns = list(snapshot.get("columns") or [])
    has_row_number = bool(columns and _norm(columns[0]) in {"dong", "row", "row no"})
    for raw in list(snapshot.get("rows") or [])[:MAX_SCAN_ROWS]:
        values = list(raw or [])
        if has_row_number and values:
            values = values[1:]
        rows.append(values)
    return semantic_payment_summary_from_rows(rows, sheet_name=str(snapshot.get("sheet") or ""))


def semantic_payment_summary_from_worksheet(ws) -> dict[str, Any]:
    if ws is None:
        return {"ok": False, "score": 0, "sections": {}, "validation": {}, "hits": []}
    max_row = min(int(getattr(ws, "max_row", 0) or 0), MAX_SCAN_ROWS)
    max_col = min(int(getattr(ws, "max_column", 0) or 0), MAX_SCAN_COLS)
    if max_row <= 0 or max_col <= 0:
        return {"ok": False, "score": 0, "sections": {}, "validation": {}, "hits": []}
    rows = [
        list(values)
        for values in ws.iter_rows(min_row=1, max_row=max_row, min_col=1, max_col=max_col, values_only=True)
    ]
    return semantic_payment_summary_from_rows(rows, sheet_name=str(getattr(ws, "title", "")))


def semantic_payment_summary_from_saved_result(saved: dict[str, Any]) -> dict[str, Any]:
    """Scan every stored sheet snapshot so old Claims are repaired without re-upload."""
    candidates: list[dict[str, Any]] = []
    workbook_sheets = dict(saved.get("workbook_sheets") or {})
    for name, snapshot in workbook_sheets.items():
        if not isinstance(snapshot, dict):
            continue
        candidate = semantic_payment_summary_from_snapshot(snapshot)
        candidate["sheet"] = str(candidate.get("sheet") or name)
        if candidate.get("present_fields"):
            candidates.append(candidate)
    if not candidates:
        return {}
    candidates.sort(
        key=lambda item: (int(item.get("score") or 0), int(item.get("present_fields") or 0)),
        reverse=True,
    )
    best = dict(candidates[0])
    best["candidates"] = [
        {"sheet": str(item.get("sheet") or ""), "score": int(item.get("score") or 0), "present_fields": int(item.get("present_fields") or 0)}
        for item in candidates[:8]
    ]
    return best


def _merge_semantic(base: dict[str, Any], semantic: dict[str, Any]) -> dict[str, Any]:
    out = dict(base or {})
    if not semantic.get("ok"):
        return out
    for key in (
        "previous_acceptance", "previous_material_acceptance", "previous_installation_acceptance",
        "current_acceptance", "current_material_acceptance", "current_installation_acceptance",
        "cumulative_acceptance", "cumulative_material_acceptance", "cumulative_installation_acceptance",
    ):
        if key in semantic:
            out[key] = float(semantic[key])
    # Keep historical alias aligned with the recognized cumulative total.
    if "cumulative_acceptance" in semantic:
        out["cumulative_completed"] = float(semantic["cumulative_acceptance"])
    out["_semantic_sections"] = {
        "marker": PATCH_MARKER,
        "sheet": semantic.get("sheet", ""),
        "score": semantic.get("score", 0),
        "sections": semantic.get("sections", {}),
        "validation": semantic.get("validation", {}),
        "candidates": semantic.get("candidates", []),
    }
    return out


def install_ipc_payment_semantic() -> None:
    """Install form-independent acceptance section recognition after adaptive parser."""
    import ipc_claim_v622 as ipc

    if getattr(ipc, "_qlda_ipc_payment_semantic_installed", False):
        return
    original = ipc._payment_summary

    def payment_summary_semantic(ws):
        base = original(ws)
        semantic = semantic_payment_summary_from_worksheet(ws)
        return _merge_semantic(base, semantic)

    ipc._payment_summary = payment_summary_semantic
    ipc._qlda_ipc_payment_semantic_installed = True
    ipc._qlda_ipc_payment_semantic_marker = PATCH_MARKER
