from __future__ import annotations

import re
from typing import Any


PATCH_MARKER = "V6.22 CLAIM COMPONENT FULLSCAN V1"
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
            "FROM payment_claims WHERE project_id=? ORDER BY claim_no LIMIT ?",
            (int(project_id), MAX_CLAIMS),
        ).fetchall()
    except Exception:
        return []
    out = [_rowdict(row) for row in rows]
    if wanted:
        out = [row for row in out if str(row.get("claim_no") or "").lstrip("0") == wanted]
    return out


def _scan_one_claim(connection, claim: dict[str, Any], *, persist: bool = True) -> dict[str, Any]:
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
                      installation_previous_value,installation_current_value,installation_cumulative_value
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
    }
    stored = {
        "material_current_total": 0.0,
        "material_cumulative_total": 0.0,
        "labor_current_total": 0.0,
        "labor_cumulative_total": 0.0,
    }
    updates: list[tuple[float, float, float, float, float, float, str, int]] = []
    rows_with_labor_qty = 0
    rows_with_material_qty = 0
    rows_with_labor_price = 0
    rows_with_material_price = 0

    for item in items:
        material_price = _num(item.get("material_unit_price"))
        labor_price = _num(item.get("labor_unit_price"))
        material_previous_qty = _num(item.get("material_previous_qty"))
        material_current_qty = _num(item.get("material_current_qty"))
        material_cumulative_qty = _num(item.get("material_cumulative_qty"))
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

        material_previous_value = material_previous_qty * material_price
        material_current_value = material_current_qty * material_price
        material_cumulative_value = material_cumulative_qty * material_price
        labor_previous_value = labor_previous_qty * labor_price
        labor_current_value = labor_current_qty * labor_price
        labor_cumulative_value = labor_cumulative_qty * labor_price

        totals["material_previous_total"] += material_previous_value
        totals["material_current_total"] += material_current_value
        totals["material_cumulative_total"] += material_cumulative_value
        totals["labor_previous_total"] += labor_previous_value
        totals["labor_current_total"] += labor_current_value
        totals["labor_cumulative_total"] += labor_cumulative_value

        stored["material_current_total"] += _num(item.get("material_current_value"))
        stored["material_cumulative_total"] += _num(item.get("material_cumulative_value"))
        stored["labor_current_total"] += _num(item.get("installation_current_value"))
        stored["labor_cumulative_total"] += _num(item.get("installation_cumulative_value"))

        try:
            row_no = int(item.get("row_no") or 0)
        except Exception:
            row_no = 0
        if row_no > 0:
            updates.append((
                float(material_previous_value),
                float(material_current_value),
                float(material_cumulative_value),
                float(labor_previous_value),
                float(labor_current_value),
                float(labor_cumulative_value),
                claim_id,
                row_no,
            ))

    if persist and updates:
        try:
            connection.executemany(
                """UPDATE payment_claim_items
                   SET material_previous_value=?,material_current_value=?,material_cumulative_value=?,
                       installation_previous_value=?,installation_current_value=?,installation_cumulative_value=?
                   WHERE claim_id=? AND row_no=?""",
                updates,
            )
        except Exception:
            # The totals are still valid for AI context even if a read-only connection
            # prevents the self-heal write.
            pass

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
        **totals,
        "stored_material_current_total_before_recalc": stored["material_current_total"],
        "stored_material_cumulative_total_before_recalc": stored["material_cumulative_total"],
        "stored_labor_current_total_before_recalc": stored["labor_current_total"],
        "stored_labor_cumulative_total_before_recalc": stored["labor_cumulative_total"],
    }
    result["material_current_delta"] = totals["material_current_total"] - stored["material_current_total"]
    result["material_cumulative_delta"] = totals["material_cumulative_total"] - stored["material_cumulative_total"]
    result["labor_current_delta"] = totals["labor_current_total"] - stored["labor_current_total"]
    result["labor_cumulative_delta"] = totals["labor_cumulative_total"] - stored["labor_cumulative_total"]
    return result


def fullscan_claim_components(connection, project_id: int, question: str = "", *, persist: bool = True) -> list[dict[str, Any]]:
    """Calculate material/labor values for every row of each selected Claim.

    Business formulas:
      material value = material quantity * material unit price
      labor value    = installation quantity * labor unit price

    Legacy DB columns named installation_*_pct are quantities in the current
    Claim template and are intentionally treated as quantities here.
    """
    _recover_prices(connection, int(project_id), str(question or ""))
    claims = _claim_headers(connection, int(project_id), str(question or ""))
    return [_scan_one_claim(connection, claim, persist=persist) for claim in claims]


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
                    stats = fullscan_claim_components(connection, int(project_id), q, persist=True)
            except Exception:
                stats = []

        # Run the existing appendix after recalculation so its detail evidence uses
        # the repaired component values too.
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
            ])
            if abs(float(item.get("labor_cumulative_delta") or 0)) > 0.5:
                lines.append(
                    f"Đã tự hiệu chỉnh trường chi phí nhân công lũy kế của các dòng Claim; "
                    f"chênh so với dữ liệu lưu trước khi tính lại = {_fmt_money(item['labor_cumulative_delta'])} VND."
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
