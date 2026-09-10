from __future__ import annotations

import re
import unicodedata
from typing import Any


PATCH_MARKER = "V6.22 PROJECT REMAINING COMPONENTS V1"


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("đ", "d")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


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


def _num(value: Any) -> float:
    try:
        return float(value or 0)
    except Exception:
        return 0.0


def _fmt_money(value: Any) -> str:
    try:
        return f"{float(value or 0):,.0f}"
    except Exception:
        return "0"


def _claim_number(row: dict[str, Any]) -> int | None:
    """Return the numeric IPC period, preferring claim_no over claim_code."""
    for value in (row.get("claim_no"), row.get("claim_code")):
        text = str(value or "").strip()
        if not text:
            continue
        match = re.search(r"(?:ipc\s*[-#]?\s*)?0*([0-9]+)", text, flags=re.I)
        if match:
            try:
                number = int(match.group(1))
                if number >= 0:
                    return number
            except Exception:
                pass
    return None


def _remaining_intent(question: str) -> bool:
    q = _norm(question)
    remaining = any(x in q for x in (
        "con lai", "gia tri con lai", "chi phi con lai", "phan con lai",
        "chua thuc hien", "chua nghiem thu", "remaining",
    ))
    scope = any(x in q for x in (
        "du an", "toan du an", "boq", "nhan cong", "vat tu", "vat lieu",
        "chi phi", "gia tri", "ipc", "icp", "claim",
    ))
    return remaining and scope


def largest_ipc_claim(connection, project_id: int) -> dict[str, Any] | None:
    """Pick the highest NUMERIC IPC period in the project.

    This is deliberately not ordered lexicographically and not selected by money,
    approval status, disbursement value, or updated timestamp.
    """
    try:
        rows = connection.execute(
            "SELECT claim_id,claim_no,claim_code,filename,updated_at "
            "FROM payment_claims WHERE project_id=?",
            (int(project_id),),
        ).fetchall()
    except Exception:
        return None

    candidates: list[tuple[int, dict[str, Any]]] = []
    for raw in rows:
        row = _rowdict(raw)
        if not row:
            try:
                row = {
                    "claim_id": raw[0], "claim_no": raw[1], "claim_code": raw[2],
                    "filename": raw[3], "updated_at": raw[4],
                }
            except Exception:
                continue
        number = _claim_number(row)
        if number is not None:
            candidates.append((number, row))

    if not candidates:
        return None
    candidates.sort(key=lambda pair: pair[0], reverse=True)
    number, row = candidates[0]
    result = dict(row)
    result["ipc_number"] = int(number)
    result["claim_code"] = str(result.get("claim_code") or f"IPC-{number:02d}")
    return result


def project_remaining_components(connection, project_id: int) -> dict[str, Any]:
    """Calculate whole-project remaining material/labor using the highest IPC only.

    Business rule:
      remaining material = BOQ full-scan material - cumulative material of highest IPC
      remaining labor    = BOQ full-scan labor    - cumulative labor of highest IPC

    Previous IPC cumulative values are NEVER added together because the highest IPC
    already carries cumulative-to-date quantities/values.
    """
    try:
        from boq_ai_fullscan_v622 import fullscan_boq_component_totals
        boq = fullscan_boq_component_totals(connection, int(project_id))
    except Exception as exc:
        return {"ok": False, "valid": False, "reason": f"boq_fullscan_failed:{exc}"}

    latest = largest_ipc_claim(connection, int(project_id))
    if latest is None:
        return {
            "ok": False,
            "valid": False,
            "reason": "no_numeric_ipc",
            "boq": boq,
        }

    ipc_no = int(latest["ipc_number"])
    try:
        from claim_component_fullscan_v622 import fullscan_claim_components
        claim_rows = fullscan_claim_components(
            connection,
            int(project_id),
            f"IPC-{ipc_no}",
            persist=False,
        )
    except Exception as exc:
        return {
            "ok": False,
            "valid": False,
            "reason": f"ipc_fullscan_failed:{exc}",
            "boq": boq,
            "latest_claim": latest,
        }

    claim = next(
        (
            row for row in claim_rows
            if int(_claim_number(row) if _claim_number(row) is not None else -1) == ipc_no
        ),
        claim_rows[0] if claim_rows else None,
    )
    if not claim:
        return {
            "ok": False,
            "valid": False,
            "reason": "highest_ipc_has_no_component_scan",
            "boq": boq,
            "latest_claim": latest,
        }

    boq_material = _num(boq.get("material_total"))
    boq_labor = _num(boq.get("labor_total"))
    ipc_material = _num(claim.get("selected_material_cumulative"))
    ipc_labor = _num(claim.get("selected_labor_cumulative"))
    remaining_material = boq_material - ipc_material
    remaining_labor = boq_labor - ipc_labor

    boq_ok = bool(boq.get("ok"))
    boq_complete = bool(boq.get("complete"))
    claim_ok = bool(claim.get("ok")) and str(claim.get("selected_source") or "") != "invalid"
    negative = remaining_material < -1.0 or remaining_labor < -1.0
    valid = bool(boq_ok and boq_complete and claim_ok and not negative)

    reasons: list[str] = []
    if not boq_ok:
        reasons.append("BOQ full-scan không tạo được")
    elif not boq_complete:
        reasons.append("BOQ full-scan chưa quét đủ toàn bộ dòng")
    if not claim_ok:
        reasons.append(f"{latest.get('claim_code') or f'IPC-{ipc_no:02d}'} chưa có lũy kế vật tư/nhân công hợp lệ")
    if negative:
        reasons.append("giá trị còn lại âm; dữ liệu BOQ và IPC cần đối chiếu")

    return {
        "ok": bool(boq_ok and claim.get("ok")),
        "valid": valid,
        "reason": "; ".join(reasons),
        "latest_ipc_number": ipc_no,
        "latest_claim_code": str(latest.get("claim_code") or claim.get("claim_code") or f"IPC-{ipc_no:02d}"),
        "latest_claim_id": str(latest.get("claim_id") or ""),
        "boq_material_total": boq_material,
        "boq_labor_total": boq_labor,
        "ipc_material_cumulative": ipc_material,
        "ipc_labor_cumulative": ipc_labor,
        "remaining_material": remaining_material,
        "remaining_labor": remaining_labor,
        "remaining_total": remaining_material + remaining_labor,
        "boq_complete": boq_complete,
        "boq_scanned_rows": int(boq.get("scanned_rows") or 0),
        "boq_total_rows": int(boq.get("total_rows") or 0),
        "ipc_scanned_rows": int(claim.get("scanned_rows") or 0),
        "ipc_total_rows": int(claim.get("total_rows") or 0),
        "ipc_component_source": str(claim.get("selected_source") or ""),
        "boq": boq,
        "claim": claim,
    }


def install_project_remaining_components() -> None:
    """Append authoritative whole-project remaining material/labor to AI context."""
    import ai_claim_context_v622 as claim_ai

    if getattr(claim_ai, "_qlda_project_remaining_installed", False):
        return

    original = claim_ai._claim_appendix

    def claim_appendix_with_project_remaining(builder, project_id: int, question: str) -> str:
        base = original(builder, int(project_id), str(question or ""))
        if not _remaining_intent(str(question or "")):
            return base

        try:
            with builder.connect() as connection:
                result = project_remaining_components(connection, int(project_id))
        except Exception as exc:
            result = {"ok": False, "valid": False, "reason": f"remaining_calculation_failed:{exc}"}

        lines = [
            str(base or "").rstrip(),
            "",
            "### GIÁ TRỊ CÒN LẠI TOÀN DỰ ÁN — NGUỒN TÍNH BẮT BUỘC",
            "QUY TẮC BẮT BUỘC: IPC lớn nhất = IPC có SỐ KỲ numeric lớn nhất đang lưu trong dự án; "
            "không phải Claim có số tiền lớn nhất và không phải Claim cập nhật gần nhất.",
            "KHÔNG cộng IPC-01 + IPC-02 + ... vì mỗi IPC là số LŨY KẾ. Chỉ dùng lũy kế của IPC lớn nhất.",
            "VẬT TƯ CÒN LẠI TOÀN DỰ ÁN = TỔNG VẬT TƯ BOQ FULL-SCAN - VẬT TƯ LŨY KẾ IPC LỚN NHẤT.",
            "NHÂN CÔNG CÒN LẠI TOÀN DỰ ÁN = TỔNG NHÂN CÔNG BOQ FULL-SCAN - NHÂN CÔNG LŨY KẾ IPC LỚN NHẤT.",
            "Nếu phần ngữ cảnh khác mâu thuẫn với PROJECT-REMAINING, AI phải dùng PROJECT-REMAINING này.",
        ]

        if not result.get("ok"):
            lines.append(
                f"[PROJECT-REMAINING] CHƯA TÍNH ĐƯỢC: {result.get('reason') or 'thiếu dữ liệu BOQ/IPC'}. "
                "Không được tự lấy một IPC thấp hơn hoặc cộng nhiều IPC để thay thế."
            )
            return "\n".join(lines) + "\n"

        code = str(result.get("latest_claim_code") or f"IPC-{int(result.get('latest_ipc_number') or 0):02d}")
        lines.extend([
            f"[PROJECT-REMAINING] IPC lớn nhất hiện có: {code} (kỳ {int(result.get('latest_ipc_number') or 0)}).",
            f"BOQ FULL-SCAN: {result.get('boq_scanned_rows',0):,}/{result.get('boq_total_rows',0):,} dòng.",
            f"Tổng VẬT TƯ BOQ: {_fmt_money(result.get('boq_material_total'))} VND.",
            f"Vật tư LŨY KẾ {code}: {_fmt_money(result.get('ipc_material_cumulative'))} VND.",
            f"VẬT TƯ CÒN LẠI TOÀN DỰ ÁN: {_fmt_money(result.get('remaining_material'))} VND.",
            f"Tổng NHÂN CÔNG BOQ: {_fmt_money(result.get('boq_labor_total'))} VND.",
            f"Nhân công LŨY KẾ {code}: {_fmt_money(result.get('ipc_labor_cumulative'))} VND.",
            f"NHÂN CÔNG CÒN LẠI TOÀN DỰ ÁN: {_fmt_money(result.get('remaining_labor'))} VND.",
            f"TỔNG VẬT TƯ + NHÂN CÔNG CÒN LẠI: {_fmt_money(result.get('remaining_total'))} VND.",
            f"Nguồn lũy kế {code}: {result.get('ipc_component_source') or 'chưa xác định'}; "
            f"đã quét {result.get('ipc_scanned_rows',0):,}/{result.get('ipc_total_rows',0):,} dòng Claim.",
        ])
        if result.get("valid"):
            lines.append("Trạng thái PROJECT-REMAINING: HỢP LỆ — được phép dùng làm kết quả còn lại toàn dự án.")
        else:
            lines.append(
                f"Trạng thái PROJECT-REMAINING: CHƯA HỢP LỆ — {result.get('reason') or 'cần đối chiếu dữ liệu'}. "
                "AI phải nêu cảnh báo và không được trình bày các số trên như kết quả cuối cùng."
            )
        return "\n".join(lines) + "\n"

    claim_ai._claim_appendix = claim_appendix_with_project_remaining
    claim_ai._qlda_project_remaining_installed = True
    claim_ai._qlda_project_remaining_marker = PATCH_MARKER
