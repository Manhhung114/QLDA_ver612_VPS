from __future__ import annotations

import threading
from typing import Any


PATCH_MARKER = "V6.22 CLAIM MATERIAL PERIOD GUARD V1"
_STATE = threading.local()


def _num(value: Any) -> float:
    try:
        return float(value or 0)
    except Exception:
        return 0.0


def _close(a: float, b: float) -> bool:
    tolerance = max(2.0, abs(float(b)) * 2e-8)
    return abs(float(a) - float(b)) <= tolerance


def _fmt_money(value: Any) -> str:
    try:
        return f"{float(value or 0):,.0f}"
    except Exception:
        return "0"


def _decode_saved(connection, claim_id: str) -> dict[str, Any]:
    try:
        row = connection.execute(
            "SELECT payload FROM payment_claim_workbooks WHERE claim_id=?",
            (str(claim_id),),
        ).fetchone()
    except Exception:
        return {}
    if row is None:
        return {}
    try:
        payload = row["payload"]
    except Exception:
        try:
            payload = row[0]
        except Exception:
            return {}
    try:
        import qlda.runtime_core.ipc_claim as ipc
        return dict(ipc._decode_result(str(payload or "")) or {})
    except Exception:
        return {}


def _summary_for_saved(saved: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    summary = dict(saved.get("summary") or {})
    semantic: dict[str, Any] = {}
    try:
        from qlda.runtime_core.ipc_payment_semantic import semantic_payment_summary_from_saved_result
        semantic = dict(semantic_payment_summary_from_saved_result(saved) or {})
    except Exception:
        semantic = {}

    if semantic.get("ok"):
        for key in (
            "previous_acceptance", "previous_material_acceptance", "previous_installation_acceptance",
            "current_acceptance", "current_material_acceptance", "current_installation_acceptance",
            "cumulative_acceptance", "cumulative_material_acceptance", "cumulative_installation_acceptance",
        ):
            if key in semantic:
                summary[key] = float(semantic[key])
    return summary, semantic


def _direct_material_value_totals(connection, claim_id: str) -> dict[str, float]:
    try:
        row = connection.execute(
            """SELECT
                   COALESCE(SUM(material_previous_value),0) AS previous_value,
                   COALESCE(SUM(material_current_value),0) AS current_value,
                   COALESCE(SUM(material_cumulative_value),0) AS cumulative_value
               FROM payment_claim_items WHERE claim_id=?""",
            (str(claim_id),),
        ).fetchone()
    except Exception:
        return {"previous": 0.0, "current": 0.0, "cumulative": 0.0}
    try:
        return {
            "previous": _num(row["previous_value"]),
            "current": _num(row["current_value"]),
            "cumulative": _num(row["cumulative_value"]),
        }
    except Exception:
        try:
            return {"previous": _num(row[0]), "current": _num(row[1]), "cumulative": _num(row[2])}
        except Exception:
            return {"previous": 0.0, "current": 0.0, "cumulative": 0.0}


def _positive_or_zero(value: float, ceiling: float = 0.0) -> bool:
    if value < -1.0:
        return False
    if ceiling > 0 and value > ceiling * 1.02 + 10.0:
        return False
    return True


def _augment_one(connection, item: dict[str, Any]) -> dict[str, Any]:
    result = dict(item or {})
    claim_id = str(result.get("claim_id") or "")
    if not claim_id:
        return result

    saved = _decode_saved(connection, claim_id)
    summary, semantic = _summary_for_saved(saved)
    direct = _direct_material_value_totals(connection, claim_id)
    ceiling = _num(result.get("contract_value")) or _num(result.get("control_ceiling"))

    previous = summary.get("previous_material_acceptance")
    current = summary.get("current_material_acceptance")
    cumulative = summary.get("cumulative_material_acceptance")
    previous = None if previous is None else _num(previous)
    current = None if current is None else _num(current)
    cumulative = None if cumulative is None else _num(cumulative)

    # Business identity is authoritative for period values. It survives row/column
    # insertions because the three sections are recognized by their semantic labels.
    identity_derived = None
    if previous is not None and cumulative is not None:
        identity_derived = float(cumulative) - float(previous)
        if current is None or not _close(float(current), identity_derived):
            current = identity_derived

    semantic_period_ok = bool(
        previous is not None and current is not None and cumulative is not None
        and _close(float(previous) + float(current), float(cumulative))
        and _positive_or_zero(float(previous), ceiling)
        and _positive_or_zero(float(current), ceiling)
        and _positive_or_zero(float(cumulative), ceiling)
    )

    direct_period_ok = bool(
        _positive_or_zero(direct["previous"], ceiling)
        and _positive_or_zero(direct["current"], ceiling)
        and _positive_or_zero(direct["cumulative"], ceiling)
        and _close(direct["previous"] + direct["current"], direct["cumulative"])
        and direct["cumulative"] > 0
    )

    quantity_previous = _num(result.get("material_previous_total"))
    quantity_current = _num(result.get("material_current_total"))
    quantity_cumulative = _num(result.get("material_cumulative_total"))
    quantity_period_ok = bool(
        _positive_or_zero(quantity_previous, ceiling)
        and _positive_or_zero(quantity_current, ceiling)
        and _positive_or_zero(quantity_cumulative, ceiling)
        and _close(quantity_previous + quantity_current, quantity_cumulative)
        and quantity_cumulative > 0
    )

    if semantic_period_ok:
        selected_previous = float(previous)
        selected_current = float(current)
        selected_cumulative = float(cumulative)
        source = "payment_summary_semantic_period_identity"
    elif direct_period_ok:
        selected_previous = direct["previous"]
        selected_current = direct["current"]
        selected_cumulative = direct["cumulative"]
        source = "gtht_direct_material_values_period_identity"
    elif quantity_period_ok:
        selected_previous = quantity_previous
        selected_current = quantity_current
        selected_cumulative = quantity_cumulative
        source = "detail_quantity_x_material_price_period_identity"
    else:
        # Preserve the already validated cumulative selection from V3, but never
        # present an unvalidated current-period subtotal as authoritative.
        selected_previous = 0.0
        selected_current = 0.0
        selected_cumulative = _num(result.get("selected_material_cumulative"))
        source = "cumulative_only_no_valid_period_identity"

    result.update(
        {
            "selected_material_previous": selected_previous,
            "selected_material_current": selected_current,
            "selected_material_cumulative": selected_cumulative,
            "selected_material_period_source": source,
            "material_period_identity_ok": bool(source != "cumulative_only_no_valid_period_identity"),
            "summary_material_previous": 0.0 if previous is None else float(previous),
            "summary_material_current": 0.0 if current is None else float(current),
            "summary_material_cumulative": 0.0 if cumulative is None else float(cumulative),
            "summary_material_identity_derived_current": 0.0 if identity_derived is None else float(identity_derived),
            "gtht_material_previous_value_total": direct["previous"],
            "gtht_material_current_value_total": direct["current"],
            "gtht_material_cumulative_value_total": direct["cumulative"],
            "gtht_material_period_identity_ok": direct_period_ok,
            "quantity_material_period_identity_ok": quantity_period_ok,
            "semantic_payment_sheet": str(semantic.get("sheet") or ""),
            "semantic_payment_score": int(semantic.get("score") or 0),
            "semantic_payment_validation": dict(semantic.get("validation") or {}),
        }
    )
    return result


def install_claim_material_period_guard() -> None:
    """Augment deterministic Claim scans with validated period identities."""
    import qlda.runtime_core.claim_component_fullscan as fullscan
    if getattr(fullscan, "_qlda_claim_material_period_guard_installed", False):
        return
    original_fullscan = fullscan.fullscan_claim_components

    def guarded_fullscan(connection, project_id: int, question: str = "", *, persist: bool = False):
        rows = original_fullscan(connection, int(project_id), str(question or ""), persist=persist)
        return [_augment_one(connection, dict(row or {})) for row in rows]

    fullscan.fullscan_claim_components = guarded_fullscan
    fullscan._qlda_claim_material_period_guard_installed = True
    fullscan._qlda_claim_material_period_guard_marker = PATCH_MARKER
