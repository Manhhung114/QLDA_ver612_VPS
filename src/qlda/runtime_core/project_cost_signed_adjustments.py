from __future__ import annotations

"""Preserve signed contract appendices and approved VO reductions in Project Cost Management.

Phụ lục/VO có thể tăng hoặc giảm giá trị; không được ép số âm về 0. Independent
VO records in variation_orders are authoritative for VO proposal/approval values.
"""

from typing import Any

PATCH_MARKER = "V7.6 PROJECT COST SIGNED ADJUSTMENTS V2 VO APPROVAL"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or 0)
    except Exception:
        return float(default)


def install_project_cost_signed_adjustments() -> None:
    import qlda.runtime_core.project_cost_management as pcm

    if getattr(pcm, "_qlda_project_cost_signed_adjustments_installed", False):
        return

    def contract_summary(db, pid: int, currency: str) -> dict[str, Any]:
        try:
            from qlda.runtime_core.contract_management import list_contract_records
            records = list_contract_records(db, int(pid))
        except Exception:
            records = []
        currency_code = _text(currency).upper() or "VND"
        same = [r for r in records if (_text(r.get("currency")).upper() or "VND") == currency_code]
        contracts = sum(max(0.0, _float(r.get("amount"))) for r in same if _text(r.get("record_type")) == "Hợp đồng")
        appendices = sum(_float(r.get("amount")) for r in same if _text(r.get("record_type")) == "Phụ lục")
        other_currency: dict[str, float] = {}
        for row in records:
            cur = _text(row.get("currency")).upper() or "VND"
            if cur != currency_code:
                other_currency[cur] = other_currency.get(cur, 0.0) + _float(row.get("amount"))
        return {
            "records": records,
            "contracts": contracts,
            "appendices": appendices,
            "committed": contracts + appendices,
            "other_currency": other_currency,
        }

    def vo_summary(db, pid: int) -> dict[str, float]:
        # New independent VO storage is authoritative. Only explicit Đã duyệt
        # records contribute to approved VO. Signed reductions remain negative.
        with db.connect() as connection:
            if pcm._table_exists(connection, "variation_orders"):
                try:
                    rows = connection.execute(
                        "SELECT proposed_amount,approved_amount,status FROM variation_orders WHERE project_id=? ORDER BY vo_no",
                        (int(pid),),
                    ).fetchall()
                except Exception:
                    rows = []
                if rows:
                    proposed = 0.0
                    approved = 0.0
                    for raw in rows:
                        row = pcm._rowdict(raw)
                        proposed += _float(row.get("proposed_amount"))
                        if _text(row.get("status")) == "Đã duyệt":
                            approved += _float(row.get("approved_amount"))
                    return {"proposed": proposed, "approved": approved}

            # Legacy/manual fallback.
            if not pcm._table_exists(connection, "cost_variations"):
                return {"proposed": 0.0, "approved": 0.0}
            try:
                rows = connection.execute("SELECT * FROM cost_variations WHERE project_id=?", (int(pid),)).fetchall()
            except Exception:
                rows = []
        proposed = approved = 0.0
        for raw in rows:
            row = pcm._rowdict(raw)
            proposed += _float(row.get("proposed_amount"))
            approved += _float(row.get("approved_amount"))
        return {"proposed": proposed, "approved": approved}

    pcm._contract_summary = contract_summary
    pcm._vo_summary = vo_summary
    pcm._qlda_project_cost_signed_adjustments_installed = True
    pcm._qlda_project_cost_signed_adjustments_marker = PATCH_MARKER


__all__ = ["install_project_cost_signed_adjustments"]
