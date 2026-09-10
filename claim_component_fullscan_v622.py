from __future__ import annotations

import re
from typing import Any


PATCH_MARKER = "V6.22 CLAIM COMPONENT FULLSCAN V3"
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
            "SELECT claim_id,claim_no,claim_code,filename,updated_at,contract_value,certified_cumulative "
            "FROM payment_claims WHERE project_id=? ORDER BY claim_no LIMIT 80",
            (int(project_id),),
        ).fetchall()
    except Exception:
        return []
    out = [_rowdict(row) for row in rows]
    if wanted:
        out = [row for row in out if str(row.get("claim_no") or "").lstrip("0") == wanted]
    return out


def _saved_claim_context(connection, claim_id: str) -> dict[str, Any]:
    """Read authoritative workbook summary + installation measure marker.

    New adaptive Claim forms explicitly store installation_measure='quantity'.
    Legacy forms do not have that marker, so installation_*_pct must NOT be
    assumed to be a quantity by the component full-scan.
    """
    try:
        row = connection.execute(
            "SELECT payload FROM payment_claim_workbooks WHERE claim_id=?",
            (str(claim_id),),
        ).fetchone()
    except Exception:
        return {}
    if row is None:
        return {}
    data = _rowdict(row)
    payload = data.get("payload") if data else None
    if payload is None:
        try:
            payload = row[0]
        except Exception:
            payload = ""
    try:
        import ipc_claim_v622 as ipc
        saved = ipc._decode_result(str(payload or ""))
    except Exception:
        return {}

    details = list(saved.get("detail_items") or [])
    measures = {
        str(item.get("installation_measure") or "").strip().lower()
        for item in details
        if isinstance(item, dict) and str(item.get("installation_measure") or "").strip()
    }
    installation_measure = "quantity" if "quantity" in measures else "legacy_or_unknown"
    return {
        "summary": dict(saved.get("summary") or {}),
        "installation_measure": installation_measure,
        "parser_profile": dict(saved.get("parser_profile") or {}),
    }


def _positive_min(values: list[float]) -> float:
    candidates = [float(v) for v in values if float(v or 0) > 0]
    return min(candidates) if candidates else 0.0


def _within_ceiling(value: float, ceiling: float) -> bool:
    if value < -1.0:
        return False
    if ceiling <= 0:
        return True
    # Allow small workbook/rounding/variation differences, but never a result
    # hundreds of billions above the Claim/contract control total.
    return value <= ceiling * 1.02 + 10.0


def _close(a: float, b: float) -> bool:
    tolerance = max(10.0, abs(b) * 2e-6)
    return abs(a - b) <= tolerance


def _scan_one_claim(connection, claim: dict[str, Any]) -> dict[str, Any]:
    """Derive Claim components with payment-summary totals as the main control.

    Cumulative component identity from the Claim payment sheet:
      labor = cumulative installation acceptance - cumulative material deduction
      material + labor = cumulative acceptance

    Row-level quantity * labor unit price is only trusted when the saved adaptive
    parser explicitly marked installation_measure='quantity'. Legacy columns named
    installation_*_pct are never blindly treated as quantities.
    """
    claim_id = str(claim.get("claim_id") or "")
    if not claim_id:
        return {"ok": False, "claim_code": str(claim.get("claim_code") or ""), "reason": "missing_claim_id"}

    saved = _saved_claim_context(connection, claim_id)
    summary = dict(saved.get("summary") or {})
    installation_measure = str(saved.get("installation_measure") or "legacy_or_unknown")

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
        "labor_previous_quantity_total": 0.0,
        "labor_current_quantity_total": 0.0,
        "labor_cumulative_quantity_total": 0.0,
        "gross_installation_previous_total": 0.0,
        "gross_installation_current_total": 0.0,
        "gross_installation_cumulative_total": 0.0,
        "deduction_previous_total": 0.0,
        "deduction_current_total": 0.0,
        "deduction_cumulative_total": 0.0,
    }
    rows_with_labor_measure = 0
    rows_with_material_qty = 0
    rows_with_labor_price = 0
    rows_with_material_price = 0

    for item in items:
        material_price = _num(item.get("material_unit_price"))
        labor_price = _num(item.get("labor_unit_price"))
        material_previous_qty = _num(item.get("material_previous_qty"))
        material_current_qty = _num(item.get("material_current_qty"))
        material_cumulative_qty = _num(item.get("material_cumulative_qty"))
        installation_previous = _num(item.get("installation_previous_pct"))
        installation_current = _num(item.get("installation_current_pct"))
        installation_cumulative = _num(item.get("installation_cumulative_pct"))

        if material_price != 0:
            rows_with_material_price += 1
        if labor_price != 0:
            rows_with_labor_price += 1
        if material_previous_qty != 0 or material_current_qty != 0 or material_cumulative_qty != 0:
            rows_with_material_qty += 1
        if installation_previous != 0 or installation_current != 0 or installation_cumulative != 0:
            rows_with_labor_measure += 1

        totals["material_previous_total"] += material_previous_qty * material_price
        totals["material_current_total"] += material_current_qty * material_price
        totals["material_cumulative_total"] += material_cumulative_qty * material_price

        # Only calculate quantity * price when the saved parser explicitly says
        # these legacy-named fields actually contain quantities.
        if installation_measure == "quantity":
            totals["labor_previous_quantity_total"] += installation_previous * labor_price
            totals["labor_current_quantity_total"] += installation_current * labor_price
            totals["labor_cumulative_quantity_total"] += installation_cumulative * labor_price

        totals["gross_installation_previous_total"] += _num(item.get("installation_previous_value"))
        totals["gross_installation_current_total"] += _num(item.get("installation_current_value"))
        totals["gross_installation_cumulative_total"] += _num(item.get("installation_cumulative_value"))
        totals["deduction_previous_total"] += _num(item.get("deduction_previous"))
        totals["deduction_current_total"] += _num(item.get("deduction_current"))
        totals["deduction_cumulative_total"] += _num(item.get("deduction_cumulative"))

    row_net_labor_previous = totals["gross_installation_previous_total"] - totals["deduction_previous_total"]
    row_net_labor_current = totals["gross_installation_current_total"] - totals["deduction_current_total"]
    row_net_labor_cumulative = totals["gross_installation_cumulative_total"] - totals["deduction_cumulative_total"]

    summary_contract = _num(summary.get("contract_value"))
    claim_contract = _num(claim.get("contract_value"))
    certified_cumulative = _num(summary.get("cumulative_acceptance")) or _num(claim.get("certified_cumulative"))
    ceiling = _positive_min([summary_contract, claim_contract, certified_cumulative])

    summary_material = _num(summary.get("cumulative_material_acceptance"))
    summary_installation = _num(summary.get("cumulative_installation_acceptance"))
    summary_deduction = _num(summary.get("cumulative_material_deduction"))
    summary_labor = summary_installation - summary_deduction
    summary_components = summary_material + summary_labor
    summary_identity_ok = bool(
        certified_cumulative > 0
        and (summary_material != 0 or summary_installation != 0 or summary_deduction != 0)
        and _close(summary_components, certified_cumulative)
        and _within_ceiling(summary_material, ceiling)
        and _within_ceiling(summary_labor, ceiling)
    )

    quantity_labor = totals["labor_cumulative_quantity_total"]
    quantity_labor_valid = bool(
        installation_measure == "quantity"
        and _within_ceiling(quantity_labor, ceiling)
        and (claim_contract <= 0 or totals["material_cumulative_total"] + quantity_labor <= claim_contract * 1.02 + 10.0)
    )
    row_net_labor_valid = bool(
        _within_ceiling(row_net_labor_cumulative, ceiling)
        and (claim_contract <= 0 or totals["material_cumulative_total"] + row_net_labor_cumulative <= claim_contract * 1.02 + 10.0)
    )

    if summary_identity_ok:
        selected_labor_cumulative = summary_labor
        selected_material_cumulative = summary_material
        selected_source = "payment_summary_identity"
    elif quantity_labor_valid:
        selected_labor_cumulative = quantity_labor
        selected_material_cumulative = totals["material_cumulative_total"]
        selected_source = "detail_quantity_x_labor_price"
    elif row_net_labor_valid:
        selected_labor_cumulative = row_net_labor_cumulative
        selected_material_cumulative = totals["material_cumulative_total"]
        selected_source = "detail_gross_installation_minus_material_deduction"
    else:
        selected_labor_cumulative = 0.0
        selected_material_cumulative = 0.0
        selected_source = "invalid"

    # Current-period labor: quantity formula is safe only for quantity-marked forms;
    # otherwise use gross installation - material deduction from the workbook rows.
    if installation_measure == "quantity" and _within_ceiling(totals["labor_current_quantity_total"], claim_contract or ceiling):
        selected_labor_current = totals["labor_current_quantity_total"]
    elif _within_ceiling(row_net_labor_current, claim_contract or ceiling):
        selected_labor_current = row_net_labor_current
    else:
        selected_labor_current = 0.0

    result = {
        "ok": bool(items),
        "claim_id": claim_id,
        "claim_no": str(claim.get("claim_no") or ""),
        "claim_code": str(claim.get("claim_code") or f"IPC-{claim.get('claim_no','')}"),
        "filename": str(claim.get("filename") or ""),
        "updated_at": str(claim.get("updated_at") or ""),
        "total_rows": len(items),
        "scanned_rows": len(items),
        "installation_measure": installation_measure,
        "rows_with_material_qty": rows_with_material_qty,
        "rows_with_labor_measure": rows_with_labor_measure,
        "rows_with_material_price": rows_with_material_price,
        "rows_with_labor_price": rows_with_labor_price,
        "contract_value": claim_contract or summary_contract,
        "certified_cumulative": certified_cumulative,
        "control_ceiling": ceiling,
        "summary_material_cumulative": summary_material,
        "summary_installation_cumulative": summary_installation,
        "summary_material_deduction_cumulative": summary_deduction,
        "summary_labor_cumulative": summary_labor,
        "summary_components": summary_components,
        "summary_identity_ok": summary_identity_ok,
        "detail_quantity_labor_cumulative": quantity_labor,
        "detail_quantity_labor_valid": quantity_labor_valid,
        "detail_net_labor_cumulative": row_net_labor_cumulative,
        "detail_net_labor_valid": row_net_labor_valid,
        "selected_labor_current": selected_labor_current,
        "selected_labor_cumulative": selected_labor_cumulative,
        "selected_material_cumulative": selected_material_cumulative,
        "selected_source": selected_source,
        **totals,
    }
    return result


def fullscan_claim_components(connection, project_id: int, question: str = "", *, persist: bool = False) -> list[dict[str, Any]]:
    """Calculate/validate material and labor totals for each selected Claim.

    `persist` is retained only for backward call compatibility. The scan is read-only.
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
    """Append validated per-Claim component totals to the AI Claim context."""
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

        lines = [str(base or "").rstrip(), "", "### CLAIM FULL-SCAN VẬT TƯ / NHÂN CÔNG — NGUỒN ĐÃ KIỂM SOÁT"]
        lines.append(
            "QUY TẮC BẮT BUỘC: tuyệt đối không mặc định cột legacy installation_*_pct là khối lượng. "
            "Chỉ được tính Khối lượng lắp đặt × Đơn giá nhân công khi workbook đã được parser đánh dấu installation_measure=quantity."
        )
        lines.append(
            "ĐỐI VỚI LŨY KẾ, ưu tiên danh tính trên sheet Thanh toán: "
            "Nhân công = Nghiệm thu lắp đặt lũy kế - Khấu trừ vật tư lũy kế; "
            "Vật tư + Nhân công phải khớp Tổng nghiệm thu lũy kế và không được vượt trần hợp đồng/Claim."
        )
        if not stats:
            lines.append("Không tạo được Claim FULL-SCAN. Không được suy đoán tổng nhân công từ một phần dòng.")
            return "\n".join(lines) + "\n"

        for item in stats:
            code = item.get("claim_code", "")
            if not item.get("ok"):
                lines.append(f"[CLAIM-FULLSCAN:{code}] chưa có dữ liệu chi tiết để tính.")
                continue

            lines.append(f"[CLAIM-FULLSCAN:{code}] Đã quét {item['scanned_rows']:,}/{item['total_rows']:,} dòng GTHT.")
            lines.append(
                f"Kiểu dữ liệu lắp đặt: {item['installation_measure']}; "
                f"trần kiểm soát={_fmt_money(item['control_ceiling'])} VND; "
                f"giá trị hợp đồng={_fmt_money(item['contract_value'])} VND; "
                f"tổng nghiệm thu lũy kế={_fmt_money(item['certified_cumulative'])} VND."
            )

            if item.get("selected_source") == "invalid":
                lines.append(
                    "CẢNH BÁO NGHIÊM TRỌNG: các phép tính chi tiết không vượt qua kiểm tra trần/danh tính Claim. "
                    "AI KHÔNG ĐƯỢC trả một con số nhân công lũy kế từ các subtotal này; phải yêu cầu kiểm tra mapping workbook."
                )
                continue

            lines.extend([
                f"GIÁ TRỊ VẬT TƯ LŨY KẾ ĐƯỢC PHÉP DÙNG: {_fmt_money(item['selected_material_cumulative'])} VND.",
                f"GIÁ TRỊ NHÂN CÔNG LŨY KẾ ĐƯỢC PHÉP DÙNG: {_fmt_money(item['selected_labor_cumulative'])} VND.",
                f"GIÁ TRỊ NHÂN CÔNG KỲ NÀY ĐƯỢC PHÉP DÙNG: {_fmt_money(item['selected_labor_current'])} VND.",
                f"Nguồn lũy kế được chọn: {item['selected_source']}.",
            ])

            if item.get("summary_identity_ok"):
                lines.append(
                    f"Đối chiếu sheet Thanh toán: vật tư {_fmt_money(item['summary_material_cumulative'])} + "
                    f"nhân công {_fmt_money(item['summary_labor_cumulative'])} = "
                    f"{_fmt_money(item['summary_components'])} VND, khớp tổng nghiệm thu lũy kế."
                )
            if item.get("installation_measure") != "quantity":
                lines.append(
                    "Mẫu Claim này không xác nhận installation_*_pct là khối lượng; vì vậy phép nhân trực tiếp trường đó với đơn giá nhân công bị cấm."
                )
            elif not item.get("detail_quantity_labor_valid"):
                lines.append(
                    "Phép tính chi tiết Khối lượng lắp đặt × Đơn giá nhân công bị loại do không vượt qua kiểm tra trần hợp đồng/Claim."
                )

        if len(stats) > 1:
            lines.append(
                "LƯU Ý: Không cộng các giá trị LŨY KẾ của IPC-01 + IPC-02 + ... vì sẽ cộng trùng. "
                "Muốn biết lũy kế đến IPC-N thì dùng trực tiếp lũy kế đã kiểm soát của IPC-N."
            )
        return "\n".join(lines) + "\n"

    claim_ai._claim_appendix = claim_appendix_with_component_fullscan
    claim_ai._qlda_claim_component_fullscan_installed = True
    claim_ai._qlda_claim_component_fullscan_marker = PATCH_MARKER
