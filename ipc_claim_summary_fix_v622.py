from __future__ import annotations

from typing import Any


PATCH_MARKER = "V6.22 IPC CLAIM PAYMENT SUMMARY FIX V2"
MAX_PAYMENT_ROWS = 80
MAX_PAYMENT_COLS = 12  # A:L


def _rows(ws) -> list[tuple[Any, ...]]:
    if ws is None:
        return []
    max_row = min(int(getattr(ws, "max_row", 0) or 0), MAX_PAYMENT_ROWS)
    if max_row <= 0:
        return []
    return [
        tuple(values)
        for values in ws.iter_rows(
            min_row=1,
            max_row=max_row,
            min_col=1,
            max_col=MAX_PAYMENT_COLS,
            values_only=True,
        )
    ]


def _number(ipc, value: Any) -> float:
    return float(ipc._to_number(value) or 0)


def _find_value(
    ipc,
    rows: list[tuple[Any, ...]],
    phrase: str,
    value_cols: tuple[int, ...],
    *,
    exclude: tuple[str, ...] = (),
) -> float:
    wanted = ipc._norm(phrase)
    excluded = tuple(ipc._norm(x) for x in exclude)
    for row in rows:
        labels = " | ".join(ipc._norm(v) for v in row if isinstance(v, str) and str(v).strip())
        if wanted and wanted not in labels:
            continue
        if any(x and x in labels for x in excluded):
            continue
        for idx in value_cols:
            if 0 <= idx < len(row):
                n = ipc._to_number(row[idx])
                if n is not None:
                    return float(n)
    return 0.0


def _find_text(
    ipc,
    rows: list[tuple[Any, ...]],
    phrase: str,
    value_cols: tuple[int, ...],
) -> str:
    wanted = ipc._norm(phrase)
    for row in rows:
        labels = " | ".join(ipc._norm(v) for v in row if isinstance(v, str) and str(v).strip())
        if wanted and wanted not in labels:
            continue
        for idx in value_cols:
            if 0 <= idx < len(row):
                value = row[idx]
                if value not in (None, ""):
                    return str(value).strip()
    return ""


def _fallback_num(ipc, ws, *refs: str) -> float:
    for ref in refs:
        value = ipc._to_number(ipc._safe_cell(ws, ref))
        if value is not None:
            return float(value)
    return 0.0


def _fallback_text(ipc, ws, *refs: str) -> str:
    for ref in refs:
        value = str(ipc._safe_cell(ws, ref) or "").strip()
        if value:
            return value
    return ""


def corrected_payment_summary(ws) -> dict[str, Any]:
    """Read IPC payment summary from row labels instead of hard-coded row numbers.

    The SIGMA IPC#3 template shifted the summary one row compared with the older
    sample. In that workbook the authoritative cells are:
      - Contract value: D10 = 510,000,000,000
      - Cumulative acceptance: D15 = 75,053,143,710
      - Gross current payment: D32 = 24,785,540,084
      - Current deductions: K25 = 426,906,333.34
      - Final payment/Claim: K31 = 24,358,633,750.66
    K30 is only the 1% utility deduction, not the Claim amount.

    Label-based lookup also tolerates later row insertions/deletions. Legacy cell
    fallbacks are retained for older IPC files without labels.
    """
    if ws is None:
        return {}

    import ipc_claim_v622 as ipc

    rows = _rows(ws)

    contract_value = _find_value(ipc, rows, "Giá trị hợp đồng + PLHĐ", (3, 5, 10))
    if not contract_value:
        contract_value = _find_value(ipc, rows, "Giá trị HĐ gốc", (5, 3, 10))
    if not contract_value:
        contract_value = _fallback_num(ipc, ws, "D10", "F11", "D11")

    contract_advance = _find_value(ipc, rows, "Giá trị tạm ứng HĐ + PLHĐ", (3, 5, 10))
    if not contract_advance:
        contract_advance = _fallback_num(ipc, ws, "D12")

    material_advance = _find_value(ipc, rows, "Giá trị tạm ứng vật tư", (3, 5, 10))
    if not material_advance:
        material_advance = _fallback_num(ipc, ws, "D14", "D13")

    cumulative_acceptance = _find_value(ipc, rows, "Lũy kế giá trị nghiệm thu đến nay", (3, 5, 10))
    if not cumulative_acceptance:
        cumulative_acceptance = _fallback_num(ipc, ws, "D15", "D14")

    cumulative_material_acceptance = _find_value(ipc, rows, "Lũy kế giá trị nghiệm thu vật tư", (3, 5, 10))
    if not cumulative_material_acceptance:
        cumulative_material_acceptance = _fallback_num(ipc, ws, "D16", "D15")

    cumulative_installation_acceptance = _find_value(ipc, rows, "Lũy kế giá trị nghiệm thu lắp đặt", (5, 3, 10))
    if not cumulative_installation_acceptance:
        cumulative_installation_acceptance = _fallback_num(ipc, ws, "F17", "F16", "D16")

    cumulative_material_deduction = _find_value(ipc, rows, "Lũy kế khấu trừ giá trị vật tư lắp đặt", (5, 3, 10))
    if not cumulative_material_deduction:
        cumulative_material_deduction = _fallback_num(ipc, ws, "F18", "F17", "D17")

    previous_approved = _find_value(ipc, rows, "Lũy kế giá trị đã duyệt các kỳ trước", (3, 5, 10))
    if not previous_approved:
        previous_approved = _fallback_num(ipc, ws, "D20", "D19")

    cumulative_retention = _find_value(ipc, rows, "Lũy kế bảo lưu đến nay", (3, 5, 10))
    if not cumulative_retention:
        cumulative_retention = _fallback_num(ipc, ws, "D26", "D25")

    cumulative_advance_recovery = _find_value(ipc, rows, "Lũy kế thu hồi tạm ứng đến nay", (3, 5, 10))
    if not cumulative_advance_recovery:
        cumulative_advance_recovery = _fallback_num(ipc, ws, "D29", "D28")

    cumulative_material_advance_recovery = _find_value(ipc, rows, "Lũy kế thu hồi tạm ứng vật tư", (3, 5, 10))
    if not cumulative_material_advance_recovery:
        cumulative_material_advance_recovery = _fallback_num(ipc, ws, "D30", "D29")

    current_gross = _find_value(ipc, rows, "Giá trị thanh toán kỳ này chưa trừ", (3, 5, 10))
    if not current_gross:
        current_gross = _fallback_num(ipc, ws, "D32", "D31")

    requested_amount = _find_value(
        ipc,
        rows,
        "Giá trị thanh toán kỳ này",
        (10, 3, 5),
        exclude=("chưa trừ", "bằng chữ", "giá trị nghiệm thu kỳ này"),
    )
    if not requested_amount:
        # New SIGMA layout stores final payment in K31; old sample used K30.
        requested_amount = _fallback_num(ipc, ws, "K31", "K30")

    current_deductions = _find_value(ipc, rows, "17 17a 17b 17c 17d", (10, 3, 5))
    if not current_deductions and current_gross and requested_amount:
        current_deductions = max(0.0, current_gross - requested_amount)
    if not current_deductions:
        current_deductions = _fallback_num(ipc, ws, "K25", "K24")

    prior_total_deductions = previous_approved + cumulative_retention + cumulative_advance_recovery + cumulative_material_advance_recovery
    if not prior_total_deductions:
        prior_total_deductions = _fallback_num(ipc, ws, "D31", "D30")

    amount_in_words = _find_text(ipc, rows, "Giá trị thanh toán kỳ này Bằng chữ", (2, 3, 10))
    if not amount_in_words:
        amount_in_words = _fallback_text(ipc, ws, "C33", "C32")

    return {
        "contract_value": contract_value,
        "contract_advance": contract_advance,
        "material_advance": material_advance,
        "cumulative_acceptance": cumulative_acceptance,
        "cumulative_material_acceptance": cumulative_material_acceptance,
        "cumulative_installation_acceptance": cumulative_installation_acceptance,
        "cumulative_material_deduction": cumulative_material_deduction,
        # This is the certified cumulative amount stored in payment_claims.
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
    }


def install_ipc_claim_summary_fix() -> None:
    import ipc_claim_v622 as ipc

    if getattr(ipc, "_qlda_ipc_summary_fix_installed", False):
        return
    ipc._payment_summary = corrected_payment_summary
    ipc._qlda_ipc_summary_fix_installed = True
    ipc._qlda_ipc_summary_fix_marker = PATCH_MARKER
