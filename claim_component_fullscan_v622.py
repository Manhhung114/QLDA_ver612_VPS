from __future__ import annotations

import re
from typing import Any


PATCH_MARKER = "V6.22 CLAIM COMPONENT FULLSCAN V2"
MAX_CLAIMS = 80


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


def _wanted_claim_no(question: str) -> str:
    try:
        import ai_claim_context_v622 as claim_ai
        text = claim_ai._norm(question)
    except Exception:
        text = str(question or "").lower()
    match = re.search(r"(?:claim|ipc)\s*#?\s*0*([0-9]+)", text)
    return str(int(match.group(1))) if match else ""


def _component_intent(question: str) -> bool:
    try:
        import ai_claim_context_v622 as claim_ai
        text = claim_ai._norm(question)
    except Exception:
        text = str(question or "").lower()
    return bool(
        any(x in text for x in ("claim", "ipc", "nghiem thu"))
        and any(x in text for x in (
            "nhan cong", "vat tu", "vat lieu", "chi phi", "gia tri", "don gia", "luy ke", "khoi luong"
        ))
    )


def _recover_prices(connection, project_id: int, question: str) -> None:
    """Repair split Claim unit prices from saved workbook snapshots when possible."""
    try:
        import boq_claim_price_recovery_v622 as recovery
        recovery._recover_claims(connection, int(project_id), str(question or ""))
    except Exception:
        pass


def _claim_headers(connection, project_id: int, question: str = "") -> list[dict[str, Any]]:
    wanted = _wanted_claim_no(question)
    try:
        rows = connection.execute(
            "SELECT claim_id,claim_no,claim_code,filename,updated_at "
            "FROM payment_claims WHERE project_id=? ORDER BY claim_no LIMIT 80",
            (int(project_id),),
        ).fetchall()
    except Exception:
        return []
    out = [_rowdict(row) for row in rows]
    if wanted:
        out = [row for row in out if str(row.get("claim_no") or "").lstrip("0") == wanted]
    return out


def _scan_one_claim(connection, claim: dict[str, Any]) -> dict[str, Any]:
    """Read every GTHT row and derive material/labor components without changing Claim totals.

    In the real Claim template:
      R/S/T = material quantity * material unit price
      U/V/W = installation quantity * (material unit price + labor unit price)
      X/Y/Z = installation quantity * material unit price (material deduction)

    Therefore pure labor is:
      installation quantity * labor unit price
    and can be cross-checked as:
      gross installation value - material deduction.

    The existing U/V/W fields are gross installation values and MUST NOT be
    overwritten with pure labor values.
    """
    claim_id = str(claim.get("claim_id") or "")
    if not claim_id:
        return {"ok": False, "claim_code": str(claim.get("claim_code") or ""), "reason": "missing_claim_id"}

    try:
        rows = connection.execute(
            """SELECT row_no,boq_item,unit,
                      material_unit_price,labor_unit_price,
                      material_previous_qty,material_current_qty,material_cumulative_qty,
                      installation_previous_pct,installation_current_pct,installation_cumulative_pct,
                      material_previous_value,material_current_value,material_cumulative_value,
                      installation_previous_value,installation_current_value,installation_cumulative_value,
                      deduction_previous,deduction_current,deduction_cumulative
               FROM payment_claim_items WHERE claim_id=? ORDER BY row_no""",
            (claim_id,),
        ).fetchall()
    except Exception as exc:
        return {
            "ok": False,
            "claim_id": claim_id,
            "claim_code": str(claim.get("claim_code") or ""),
            "reason": f"item_query_failed:{exc}",
        }

    items = [_rowdict(row) for row in rows]
    totals = {
        "material_previous_total": 0.0,
        "material_current_total": 0.0,
        "material_cumulative_total": 0.0,
        "labor_previous_total": 0.0,
        "labor_current_total": 0.0,
        "labor_cumulative_total": 0.0,
        "gross_installation_previous_total": 0.0,
        "gross_installation_current_total": 0.0,
        "gross_installation_cumulative_total": 0.0,
        "deduction_previous_total": 0.0,
        "deduction_current_total": 0.0,
        "deduction_cumulative_total": 0.0,
    }
    rows_with_labor_qty = 0
    rows_with_material_qty = 0
    rows_with_labor_price = 0
    rows_with_material_price = 0
    labor_crosscheck_rows = 0
    labor_crosscheck_mismatch_rows = 0
    labor_crosscheck_delta = 0.0

    for item in items:
        material_price = _num(item.get("material_unit_price"))
        labor_price = _num(item.get("labor_unit_price"))
        material_previous_qty = _num(item.get("material_previous_qty"))
        material_current_qty = _num(item.get("material_current_qty"))
        material_cumulative_qty = _num(item.get("material_cumulative_qty"))

        # Legacy DB column names end in _pct, but in the current Claim workbook
        # these columns are Khối lượng lắp đặt: kỳ trước / kỳ này / lũy kế.
        labor_previous_qty = _num(item.get("installation_previous_pct"))
        labor_current_qty = _num(item.get("installation_current_pct"))
        labor_cumulative_qty = _num(item.get("installation_cumulative_pct"))

        if material_price != 0:
            rows_with_material_price += 1
        if labor_price != 0:
            rows_with_labor_price += 1
        if material_previous_qty != 0 or material_current_qty != 0 or material_cumulative_qty != 0:
            rows_with_material_qty += 1
        if labor_previous_qty != 0 or labor_current_qty != 0 or labor_cumulative_qty != 0:
            rows_with_labor_qty += 1

        totals["material_previous_total"] += material_previous_qty * material_price
        totals["material_current_total"] += material_current_qty * material_price
        totals["material_cumulative_total"] += material_cumulative_qty * material_price
        totals["labor_previous_total"] += labor_previous_qty * labor_price
        totals["labor_current_total"] += labor_current_qty * labor_price
        totals["labor_cumulative_total"] += labor_cumulative_qty * labor_price

        gross_previous = _num(item.get("installation_previous_value"))
        gross_current = _num(item.get("installation_current_value"))
        gross_cumulative = _num(item.get("installation_cumulative_value"))
        deduction_previous = _num(item.get("deduction_previous"))
        deduction_current = _num(item.get("deduction_current"))
        deduction_cumulative = _num(item.get("deduction_cumulative"))
        totals["gross_installation_previous_total"] += gross_previous
        totals["gross_installation_current_total"] += gross_current
        totals["gross_installation_cumulative_total"] += gross_cumulative
        totals["deduction_previous_total"] += deduction_previous
        totals["deduction_current_total"] += deduction_current
        totals["deduction_cumulative_total"] += deduction_cumulative

        # Cross-check only rows where at least one side carries meaningful data.
        derived_labor = labor_cumulative_qty * labor_price
        net_stored_labor = gross_cumulative - deduction_cumulative
        if any(abs(v) > 1e-9 for v in (labor_cumulative_qty, labor_price, gross_cumulative, deduction_cumulative)):
            labor_crosscheck_rows += 1
            delta = derived_labor - net_stored_labor
            labor_crosscheck_delta += delta
            tolerance = max(1.0, abs(derived_labor) * 1e-8)
            if abs(delta) > tolerance:
                labor_crosscheck_mismatch_rows += 1

    result = {
        "ok": bool(items),
        "claim_id": claim_id,
        "claim_no": str(claim.get("claim_no") or ""),
        "claim_code": str(claim.get("claim_code") or f"IPC-{claim.get('claim_no','')}"),
        "filename": str(claim.get("filename") or ""),
        "updated_at": str(claim.get("updated_at") or ""),
        "total_rows": len(items),
        "scanned_rows": len(items),
        "rows_with_material_qty": rows_with_material_qty,
        "rows_with_labor_qty": rows_with_labor_qty,
        "rows_with_material_price": rows_with_material_price,
        "rows_with_labor_price": rows_with_labor_price,
        "labor_crosscheck_rows": labor_crosscheck_rows,
        "labor_crosscheck_mismatch_rows": labor_crosscheck_mismatch_rows,
        "labor_crosscheck_delta": labor_crosscheck_delta,
        **totals,
    }
    result["net_installation_labor_previous_total"] = (
        totals["gross_installation_previous_total"] - totals["deduction_previous_total"]
    )
    result["net_installation_labor_current_total"] = (
        totals["gross_installation_current_total"] - totals["deduction_current_total"]
    )
    result["net_installation_labor_cumulative_total"] = (
        totals["gross_installation_cumulative_total"] - totals["deduction_cumulative_total"]
    )
    return result


def fullscan_claim_components(connection, project_id: int, question: str = "", *, persist: bool = False) -> list[dict[str, Any]]:
    """Calculate pure material/labor totals for each selected Claim on all rows.

    `persist` is retained only for backward call compatibility. The full-scan is
    intentionally read-only with respect to Claim value fields because the
    stored installation values are gross values required by the payment logic.
    """
    del persist
    _recover_prices(connection, int(project_id), str(question or ""))
    claims = _claim_headers(connection, int(project_id), str(question or ""))
    return [_scan_one_claim(connection, claim) for claim in claims]


def _fmt_money(value: Any) -> str:
    try:
        return f"{float(value or 0):,.0f}"
    except Exception:
        return "0"


def install_claim_component_fullscan() -> None:
    """Append authoritative per-Claim full-scan totals to the AI Claim context."""
    import ai_claim_context_v622 as claim_ai

    if getattr(claim_ai, "_qlda_claim_component_fullscan_installed", False):
        return

    original = claim_ai._claim_appendix

    def claim_appendix_with_component_fullscan(builder, project_id: int, question: str) -> str:
        q = str(question or "")
        needs_components = _component_intent(q)

        stats: list[dict[str, Any]] = []
        if needs_components:
            try:
                with builder.connect() as connection:
                    stats = fullscan_claim_components(connection, int(project_id), q, persist=False)
            except Exception:
                stats = []

        base = original(builder, int(project_id), q)
        if not needs_components:
            return base

        lines = [str(base or "").rstrip(), "", "### CLAIM FULL-SCAN VẬT TƯ / NHÂN CÔNG — NGUỒN TỔNG HỢP ƯU TIÊN"]
        lines.append(
            "QUY TẮC BẮT BUỘC: Chi phí vật tư = Khối lượng vật tư × Đơn giá vật tư; "
            "Chi phí nhân công = Khối lượng lắp đặt × Đơn giá nhân công. "
            "Các cột legacy installation_*_pct được hiểu là KHỐI LƯỢNG lắp đặt, không phải phần trăm."
        )
        lines.append(
            "Cột Giá trị lắp đặt gốc của Claim là giá trị GỘP trước khấu trừ vật tư; không được coi trực tiếp là chi phí nhân công "
            "và FULL-SCAN không ghi đè các giá trị nghiệm thu gốc."
        )
        lines.append(
            "Tổng lũy kế của một Claim được tính trên TOÀN BỘ dòng payment_claim_items của chính Claim đó; "
            "giới hạn số dòng đưa vào prompt không được dùng để tính tổng."
        )
        if not stats:
            lines.append("Không tạo được Claim FULL-SCAN. Không được gọi subtotal từ một phần dòng là tổng nhân công/vật tư của Claim.")
            return "\n".join(lines) + "\n"

        for item in stats:
            if not item.get("ok"):
                lines.append(f"[CLAIM-FULLSCAN:{item.get('claim_code','')}] chưa có dữ liệu chi tiết để tính.")
                continue
            lines.extend([
                f"[CLAIM-FULLSCAN:{item.get('claim_code','')}] Đã quét {item['scanned_rows']:,}/{item['total_rows']:,} dòng GTHT.",
                f"Chi phí vật tư KỲ NÀY: {_fmt_money(item['material_current_total'])} VND.",
                f"Chi phí vật tư LŨY KẾ: {_fmt_money(item['material_cumulative_total'])} VND.",
                f"Chi phí nhân công KỲ NÀY: {_fmt_money(item['labor_current_total'])} VND.",
                f"Chi phí nhân công LŨY KẾ: {_fmt_money(item['labor_cumulative_total'])} VND.",
                f"Căn cứ nhân công: {item['rows_with_labor_qty']:,} dòng có khối lượng lắp đặt; "
                f"{item['rows_with_labor_price']:,} dòng có đơn giá nhân công khác 0.",
                f"Kiểm tra chéo: Giá trị lắp đặt lũy kế - Khấu trừ vật tư lũy kế = "
                f"{_fmt_money(item['net_installation_labor_cumulative_total'])} VND; "
                f"chênh với Khối lượng lắp đặt lũy kế × Đơn giá nhân công = {_fmt_money(item['labor_crosscheck_delta'])} VND.",
            ])
            if item.get("labor_crosscheck_mismatch_rows"):
                lines.append(
                    f"CẢNH BÁO: {item['labor_crosscheck_mismatch_rows']:,}/{item['labor_crosscheck_rows']:,} dòng không khớp kiểm tra chéo. "
                    "AI phải nêu cảnh báo thay vì tự sửa dữ liệu gốc."
                )

        if len(stats) > 1:
            lines.append(
                "LƯU Ý: Không cộng các giá trị LŨY KẾ của IPC-01 + IPC-02 + ... vì sẽ cộng trùng. "
                "Muốn biết lũy kế đến IPC-N thì dùng trực tiếp Chi phí nhân công/vật tư LŨY KẾ của IPC-N."
            )
        else:
            lines.append(
                "Khi đối chiếu BOQ với Claim này, dùng trực tiếp giá trị LŨY KẾ FULL-SCAN của Claim; "
                "không cộng lại các Claim trước vì lũy kế đã bao gồm các kỳ trước."
            )
        return "\n".join(lines) + "\n"

    claim_ai._claim_appendix = claim_appendix_with_component_fullscan
    claim_ai._qlda_claim_component_fullscan_installed = True
    claim_ai._qlda_claim_component_fullscan_marker = PATCH_MARKER
