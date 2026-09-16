from __future__ import annotations

"""Use the independent VO approval records in Project Cost Management.

VO Excel lives in ``variation_orders``.  The legacy ``cost_variations`` table is
kept only as a fallback for older/manual records.  Signed values are preserved so
an approved decrease reduces the approved VO total instead of being clipped to 0.
"""

from typing import Any

PATCH_MARKER = "V7.6 VO APPROVAL FINANCE BRIDGE V1"


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


def install_vo_approval_finance_bridge() -> None:
    import qlda.runtime_core.project_cost_management as pcm

    if getattr(pcm, "_qlda_vo_approval_finance_bridge_installed", False):
        return

    original = pcm._vo_summary

    def vo_summary(db, pid: int) -> dict[str, float]:
        with db.connect() as connection:
            try:
                if pcm._table_exists(connection, "variation_orders"):
                    rows = connection.execute(
                        """SELECT proposed_amount,approved_amount,status
                           FROM variation_orders WHERE project_id=? ORDER BY vo_no""",
                        (int(pid),),
                    ).fetchall()
                    data = [_rowdict(r) for r in rows]
                    if data:
                        proposed = sum(_num(r.get("proposed_amount")) for r in data)
                        approved = sum(
                            _num(r.get("approved_amount"))
                            for r in data
                            if str(r.get("status") or "").strip() == "Đã duyệt"
                        )
                        return {"proposed": proposed, "approved": approved}
            except Exception:
                pass
        return original(db, int(pid))

    pcm._vo_summary = vo_summary
    pcm._qlda_vo_approval_finance_bridge_installed = True
    pcm._qlda_vo_approval_finance_bridge_marker = PATCH_MARKER


__all__ = ["install_vo_approval_finance_bridge"]
