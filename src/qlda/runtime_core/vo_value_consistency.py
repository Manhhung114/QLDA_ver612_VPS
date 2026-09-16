from __future__ import annotations

"""Keep one authoritative VO value across VO, reports and cost control.

The independent VO screen displays the signed net of parsed detail lines:
``increase_amount + decrease_amount``. Older code persisted ``proposed_amount``
from a summary-cell parser instead, so a workbook whose summary formula was read
incorrectly could show one value in VO and another in reports.

Policy:
- when parsed detail contains priced VO lines, signed detail net is authoritative
  for the *proposed* VO value;
- ``approved_amount`` remains a user-controlled business decision and is never
  rewritten automatically;
- only VO records with status ``Đã duyệt`` contribute to approved VO totals;
- legacy ``cost_variations`` remains a fallback when no independent VO exists.
"""

from typing import Any

PATCH_MARKER = "V7.6 VO VALUE CONSISTENCY V1"


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


def effective_proposed_value(row: dict[str, Any]) -> float:
    """Return the authoritative signed proposal value for one VO row."""
    increase = _num(row.get("increase_amount"))
    decrease = _num(row.get("decrease_amount"))
    # If detail parsing found priced lines, use the same signed net shown on the
    # VO screen. If no priced detail was parsed, keep the stored summary fallback.
    if abs(increase) + abs(decrease) >= 0.5:
        return increase + decrease
    return _num(row.get("proposed_amount"))


def _normalize_connection(connection, project_id: int) -> int:
    """Repair stale proposed_amount values for existing independent VO rows."""
    try:
        rows = connection.execute(
            """SELECT vo_id,increase_amount,decrease_amount,proposed_amount
               FROM variation_orders WHERE project_id=?""",
            (int(project_id),),
        ).fetchall()
    except Exception:
        return 0

    changed = 0
    for raw in rows:
        row = _rowdict(raw)
        increase = _num(row.get("increase_amount"))
        decrease = _num(row.get("decrease_amount"))
        if abs(increase) + abs(decrease) < 0.5:
            continue
        proposed = increase + decrease
        stored = _num(row.get("proposed_amount"))
        if abs(proposed - stored) < 0.5:
            continue
        connection.execute(
            "UPDATE variation_orders SET proposed_amount=? WHERE vo_id=?",
            (float(proposed), str(row.get("vo_id") or "")),
        )
        changed += 1
    return changed


def normalize_project_vo_values(db, project_id: int) -> int:
    try:
        with db.connect() as connection:
            return _normalize_connection(connection, int(project_id))
    except Exception:
        return 0


def _independent_rows(connection, project_id: int) -> list[dict[str, Any]]:
    try:
        rows = connection.execute(
            """SELECT vo_code,'' AS task_ref,filename AS description,
                      increase_amount,decrease_amount,proposed_amount,approved_amount,
                      funding_source,status,vo_date,note
               FROM variation_orders WHERE project_id=? ORDER BY vo_no""",
            (int(project_id),),
        ).fetchall()
    except Exception:
        return []
    out: list[dict[str, Any]] = []
    for raw in rows:
        row = _rowdict(raw)
        row["proposed_amount"] = effective_proposed_value(row)
        out.append(row)
    return out


def install_vo_value_consistency() -> None:
    import qlda.runtime_core.ai_vo_context as ai_vo
    import qlda.runtime_core.project_cost_management as pcm
    import qlda.runtime_core.report_cost as report_cost
    import qlda.runtime_core.vo_claim as core
    import qlda.runtime_core.vo_independent as vo_ui

    if getattr(pcm, "_qlda_vo_value_consistency_installed", False):
        return

    # 1) Future VO imports: keep total_after_vat for audit, but normalize the
    #    business proposal after save to the same detail-net value shown in UI.
    original_save = core.save_vo

    def save_vo_consistent(db, project_id: int, result: dict[str, Any]):
        saved = original_save(db, int(project_id), result)
        normalize_project_vo_values(db, int(project_id))
        try:
            code = str(saved.get("vo_code") or result.get("vo_code") or "")
            for row in core.list_vos(db, int(project_id)):
                if str(row.get("vo_code") or "") == code:
                    return row
        except Exception:
            pass
        return saved

    core.save_vo = save_vo_consistent

    # 2) Existing VO rows are repaired before the VO screen renders. Approval is
    #    intentionally untouched because it is a user decision.
    original_render_vo = vo_ui.render_vo_ui

    def render_vo_ui_consistent(db, project_id: int, can_update: bool = True):
        normalize_project_vo_values(db, int(project_id))
        return original_render_vo(db, int(project_id), can_update=bool(can_update))

    vo_ui.render_vo_ui = render_vo_ui_consistent
    vo_ui._proposed_value = effective_proposed_value

    # 3) Overview report reads the same independent VO source and same net rule.
    original_load_vo_rows = report_cost._load_vo_rows

    def load_vo_rows_consistent(connection, project_id: int):
        rows = _independent_rows(connection, int(project_id))
        if rows:
            return rows
        return original_load_vo_rows(connection, int(project_id))

    report_cost._load_vo_rows = load_vo_rows_consistent

    # 4) Cost classification / PMBOK snapshot uses exactly the same source.
    original_vo_summary = pcm._vo_summary

    def vo_summary_consistent(db, project_id: int) -> dict[str, float]:
        try:
            normalize_project_vo_values(db, int(project_id))
            with db.connect() as connection:
                rows = _independent_rows(connection, int(project_id))
            if rows:
                proposed = sum(effective_proposed_value(row) for row in rows)
                approved = sum(
                    _num(row.get("approved_amount"))
                    for row in rows
                    if str(row.get("status") or "").strip() == "Đã duyệt"
                )
                return {"proposed": proposed, "approved": approved}
        except Exception:
            pass
        return original_vo_summary(db, int(project_id))

    pcm._vo_summary = vo_summary_consistent

    # 5) AI context should not repeat a stale stored proposal from old records.
    original_ai_appendix = ai_vo._vo_appendix

    def ai_appendix_consistent(builder, project_id: int, question: str) -> str:
        try:
            with builder.connect() as connection:
                _normalize_connection(connection, int(project_id))
        except Exception:
            pass
        return original_ai_appendix(builder, int(project_id), question)

    ai_vo._vo_appendix = ai_appendix_consistent

    pcm._qlda_vo_value_consistency_installed = True
    pcm._qlda_vo_value_consistency_marker = PATCH_MARKER


__all__ = [
    "PATCH_MARKER",
    "effective_proposed_value",
    "normalize_project_vo_values",
    "install_vo_value_consistency",
]
