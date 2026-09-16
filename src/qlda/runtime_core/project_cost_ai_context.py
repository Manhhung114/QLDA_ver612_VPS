from __future__ import annotations

"""Ngữ cảnh AI cho Project Cost Management.

Trợ lý AI đọc trực tiếp dữ liệu ngân sách, baseline, Hợp đồng/Phụ lục, VO,
IPC, thanh toán và EVM trong đúng phạm vi workspace được phép truy cập.
"""

from typing import Any

PATCH_MARKER = "V7.6 PROJECT COST AI CONTEXT V2"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or 0)
    except Exception:
        return float(default)


def _money(value: Any) -> str:
    n = _float(value)
    sign = "-" if n < 0 else ""
    n = abs(n)
    if n >= 1_000_000_000:
        return f"{sign}{n / 1_000_000_000:,.3f} tỷ"
    if n >= 1_000_000:
        return f"{sign}{n / 1_000_000:,.2f} triệu"
    return f"{sign}{n:,.0f} VND"


def _ratio(value: Any) -> str:
    return "Chưa đủ dữ liệu" if value is None else f"{float(value):.4f}"


class _BuilderDB:
    def __init__(self, builder):
        self._builder = builder

    def connect(self):
        return self._builder.connect()


def _scope_workspaces(builder, project_id: int) -> list[dict[str, Any]]:
    try:
        from qlda.runtime_core.cashflow_ai_context import _scope_workspaces as cash_scope
        return cash_scope(builder, int(project_id))
    except Exception:
        pass
    pid = int(project_id)
    try:
        with builder.connect() as connection:
            row = connection.execute("SELECT id,code,name FROM projects WHERE id=? LIMIT 1", (pid,)).fetchone()
            if row:
                try:
                    d = {str(k): row[k] for k in row.keys()}
                except Exception:
                    d = dict(row)
                return [{
                    "master_project_id": pid,
                    "workspace_project_id": pid,
                    "contractor_code": "",
                    "contractor_name": "",
                    "project_code": d.get("code", ""),
                    "project_name": d.get("name", ""),
                }]
    except Exception:
        pass
    return []


def _appendix(builder, project_id: int) -> str:
    from qlda.runtime_core.project_cost_management import build_cost_snapshot

    workspaces = _scope_workspaces(builder, int(project_id))
    if not workspaces:
        return ""
    db = _BuilderDB(builder)
    lines = [
        "",
        "## QUẢN LÝ CHI PHÍ DỰ ÁN — DỮ LIỆU TÀI CHÍNH LIVE",
        "QUY TẮC NGHIỆP VỤ:",
        "- Hợp đồng/Phụ lục chỉ được quản lý tại Hồ sơ Hợp đồng; phần Tài chính chỉ đọc lại để tính Chi phí đã cam kết.",
        "- Chi phí đã cam kết = Hợp đồng gốc + Phụ lục cùng tiền tệ baseline.",
        "- VO đã duyệt được hiển thị riêng và KHÔNG tự cộng vào Chi phí đã cam kết để tránh cộng trùng nếu VO đã được đưa vào Phụ lục.",
        "- Cost Baseline = Chi phí công việc baseline + Contingency Reserve; Management Reserve nằm ngoài Cost Baseline.",
        "- AC là Actual Cost thực tế phát sinh, KHÔNG đồng nhất với tiền đã thanh toán.",
        "- Nếu AC chưa được nhập thì không tự suy diễn CPI/EAC/ETC/VAC từ Paid Cash.",
        "- Khi trả lời người dùng bằng tiếng Việt, dùng thuật ngữ 'Chi phí đã cam kết', không dùng 'Committed Cost'.",
    ]

    totals = {
        "boq": 0.0, "baseline": 0.0, "budget": 0.0, "committed": 0.0,
        "vo": 0.0, "certified": 0.0, "paid": 0.0,
    }

    for ws in workspaces:
        wid = int(ws.get("workspace_project_id") or 0)
        if wid <= 0:
            continue
        snap = build_cost_snapshot(db, wid)
        code = _text(ws.get("contractor_code")) or f"WS-{wid}"
        name = _text(ws.get("contractor_name")) or _text(ws.get("project_name"))
        settings = snap.get("settings") or {}
        lines += [
            "",
            f"### [PROJECT-COST:{code}] {name} | workspace={wid}",
            f"Tiền tệ baseline={_text(settings.get('currency')) or 'VND'} | ngày baseline={_text(settings.get('baseline_date')) or 'chưa ghi nhận'}.",
            f"BOQ hiện tại={_money(snap.get('boq_estimate'))} | Chi phí công việc baseline={_money(snap.get('baseline_work_cost'))} | Contingency={_money(snap.get('contingency_reserve'))} | Cost Baseline(BAC)={_money(snap.get('cost_baseline'))} | Management Reserve={_money(snap.get('management_reserve'))} | Total Budget={_money(snap.get('total_budget'))}.",
            f"Hợp đồng gốc={_money(snap.get('contract_value'))} | Phụ lục={_money(snap.get('appendix_value'))} | Chi phí đã cam kết={_money(snap.get('committed_cost'))} | VO đã duyệt riêng={_money(snap.get('vo_approved'))}.",
            f"Certified Cost/IPC lũy kế={_money(snap.get('certified_cost'))} | Paid Cash={_money(snap.get('paid_cash'))}.",
            f"PV={_money(snap.get('pv'))} | EV={_money(snap.get('ev'))} | AC={_money(snap.get('ac')) if snap.get('ac') is not None else 'CHƯA NHẬP'} | AC date={_text(snap.get('ac_status_date')) or '—'}.",
            f"CV={_money(snap.get('cv')) if snap.get('cv') is not None else 'Chưa đủ dữ liệu'} | SV={_money(snap.get('sv'))} | CPI={_ratio(snap.get('cpi'))} | SPI={_ratio(snap.get('spi'))} | EAC={_money(snap.get('eac')) if snap.get('eac') is not None else 'Chưa đủ dữ liệu'} | ETC={_money(snap.get('etc')) if snap.get('etc') is not None else 'Chưa đủ dữ liệu'} | VAC={_money(snap.get('vac')) if snap.get('vac') is not None else 'Chưa đủ dữ liệu'} | TCPI={_ratio(snap.get('tcpi'))}.",
            f"BOQ tham gia PV/EV={_money(snap.get('linked_boq'))} | BOQ chưa liên kết công việc={_money(snap.get('unlinked_boq'))} | Ngưỡng kiểm soát={_float(settings.get('control_threshold_pct')):.1f}%.",
        ]
        if snap.get("other_currency"):
            lines.append("Hợp đồng/phụ lục khác tiền tệ chưa cộng vào Chi phí đã cam kết: " + " | ".join(f"{k}={_money(v)}" for k, v in snap["other_currency"].items()))

        totals["boq"] += _float(snap.get("boq_estimate"))
        totals["baseline"] += _float(snap.get("cost_baseline"))
        totals["budget"] += _float(snap.get("total_budget"))
        totals["committed"] += _float(snap.get("committed_cost"))
        totals["vo"] += _float(snap.get("vo_approved"))
        totals["certified"] += _float(snap.get("certified_cost"))
        totals["paid"] += _float(snap.get("paid_cash"))

    lines += [
        "",
        "### [PROJECT-COST-TOTAL] TỔNG PHẠM VI ĐƯỢC PHÉP ĐỌC",
        f"BOQ={_money(totals['boq'])} | Cost Baseline={_money(totals['baseline'])} | Total Budget={_money(totals['budget'])} | Chi phí đã cam kết={_money(totals['committed'])} | VO đã duyệt riêng={_money(totals['vo'])} | Certified={_money(totals['certified'])} | Paid={_money(totals['paid'])}.",
        "Khi người dùng hỏi ngân sách/chi phí/hợp đồng/EVM, dùng các số live ở trên. Không suy đoán AC nếu chưa nhập và không cộng VO đã duyệt vào Chi phí đã cam kết trừ khi dữ liệu sau này có liên kết rõ ràng VO→Phụ lục.",
    ]
    return "\n".join(lines)


def install_project_cost_ai_context() -> None:
    import qlda.runtime_core.ai_service as ai_service

    cls = ai_service.ProjectContextBuilder
    if getattr(cls, "_qlda_project_cost_ai_context_installed", False):
        return
    original_build = cls.build

    def build_with_project_cost(self, project_id: int, question: str = "", status_date=None,
                                max_tasks: int = 80, max_docs: int = 70,
                                max_drawings: int = 60, max_legal: int = 40) -> str:
        snapshot = original_build(
            self, project_id, question, status_date,
            max_tasks=max_tasks, max_docs=max_docs,
            max_drawings=max_drawings, max_legal=max_legal,
        )
        try:
            appendix = _appendix(self, int(project_id))
        except Exception as exc:
            appendix = f"\n## QUẢN LÝ CHI PHÍ DỰ ÁN\nKhông đọc được dữ liệu chi phí ở lượt này: {exc}"
        return snapshot.rstrip() + ("\n" + appendix if appendix else "") + "\n"

    cls.build = build_with_project_cost
    cls._qlda_project_cost_ai_context_installed = True
    cls._qlda_project_cost_ai_context_marker = PATCH_MARKER


__all__ = ["install_project_cost_ai_context"]
