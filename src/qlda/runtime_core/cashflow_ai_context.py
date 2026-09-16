from __future__ import annotations

"""Ngữ cảnh AI cho dự trù dòng tiền thanh toán IPC.

Chỉ đưa vào Trợ lý AI các IPC đã tồn tại và còn số dư chưa thanh toán.
Không tạo dự báo BOQ/tiến độ, xác suất, kịch bản hay mô phỏng tương lai.
"""

from typing import Any

PATCH_MARKER = "V7.6 UNPAID IPC CASH PLAN AI V2"
MAX_ROWS = 120


def _text(value: Any) -> str:
    return str(value or "").strip()


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or 0)
    except Exception:
        return float(default)


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


def _money(value: Any) -> str:
    n = _float(value)
    if abs(n) >= 1_000_000_000:
        return f"{n / 1_000_000_000:,.3f} tỷ"
    if abs(n) >= 1_000_000:
        return f"{n / 1_000_000:,.2f} triệu"
    return f"{n:,.0f} VND"


class _BuilderDB:
    def __init__(self, builder):
        self._builder = builder

    def connect(self):
        return self._builder.connect()


def _scope_workspaces(builder, project_id: int) -> list[dict[str, Any]]:
    pid = int(project_id)
    try:
        from qlda.runtime_core.contractor_access_control import _AI_WORKSPACE_SCOPE
        restricted = _AI_WORKSPACE_SCOPE.get()
    except Exception:
        restricted = None

    with builder.connect() as connection:
        if restricted:
            rid = int(restricted)
            try:
                row = connection.execute(
                    """SELECT pc.master_project_id,pc.workspace_project_id,pc.contractor_code,pc.contractor_name,
                              pc.contract_no,pc.package_name,p.code AS project_code,p.name AS project_name
                       FROM project_contractors pc JOIN projects p ON p.id=pc.workspace_project_id
                       WHERE pc.workspace_project_id=? LIMIT 1""",
                    (rid,),
                ).fetchone()
                if row:
                    return [_rowdict(row)]
            except Exception:
                pass
            try:
                row = connection.execute("SELECT id,code,name FROM projects WHERE id=? LIMIT 1", (rid,)).fetchone()
                d = _rowdict(row)
                if d:
                    return [{
                        "master_project_id": rid,
                        "workspace_project_id": rid,
                        "contractor_code": "",
                        "contractor_name": "",
                        "contract_no": "",
                        "package_name": "",
                        "project_code": d.get("code", ""),
                        "project_name": d.get("name", ""),
                    }]
            except Exception:
                pass
            return []

        master_id = pid
        try:
            from qlda.runtime_core.contractor_workspace import resolve_master_project_id_connection
            master_id = int(resolve_master_project_id_connection(connection, pid) or pid)
        except Exception:
            pass
        try:
            rows = connection.execute(
                """SELECT pc.master_project_id,pc.workspace_project_id,pc.contractor_code,pc.contractor_name,
                          pc.contract_no,pc.package_name,p.code AS project_code,p.name AS project_name
                   FROM project_contractors pc JOIN projects p ON p.id=pc.workspace_project_id
                   WHERE pc.master_project_id=? AND pc.status='Đang hoạt động'
                   ORDER BY pc.is_default DESC,pc.id""",
                (master_id,),
            ).fetchall()
            data = [_rowdict(r) for r in rows]
            if data:
                return data
        except Exception:
            pass
        try:
            row = connection.execute("SELECT id,code,name FROM projects WHERE id=? LIMIT 1", (pid,)).fetchone()
            d = _rowdict(row)
            if d:
                return [{
                    "master_project_id": pid,
                    "workspace_project_id": pid,
                    "contractor_code": "",
                    "contractor_name": "",
                    "contract_no": "",
                    "package_name": "",
                    "project_code": d.get("code", ""),
                    "project_name": d.get("name", ""),
                }]
        except Exception:
            pass
    return []


def _appendix(builder, project_id: int) -> str:
    from qlda.runtime_core.finance_management_ui import load_unpaid_ipcs, _date_text

    workspaces = _scope_workspaces(builder, int(project_id))
    if not workspaces:
        return ""
    db = _BuilderDB(builder)
    lines = [
        "",
        "## DỰ TRÙ DÒNG TIỀN THANH TOÁN IPC — DỮ LIỆU HIỆN HỮU",
        "QUY TẮC: chỉ dùng IPC đã tồn tại và còn số dư chưa thanh toán. Không dự báo BOQ, tiến độ, xác suất, kịch bản hoặc mô phỏng tương lai.",
        "Còn phải thanh toán = giá trị được duyệt (nếu có), nếu chưa có thì giá trị đề nghị/chứng nhận, trừ số đã thanh toán.",
    ]

    grand_total = 0.0
    grand_count = 0
    grand_overdue = 0.0
    grand_approved = 0.0

    for ws in workspaces:
        wid = int(ws.get("workspace_project_id") or 0)
        if wid <= 0:
            continue
        rows = load_unpaid_ipcs(db, wid)
        total = sum(_float(r.get("outstanding")) for r in rows)
        overdue = sum(_float(r.get("outstanding")) for r in rows if int(r.get("overdue_days") or 0) > 0)
        approved = sum(_float(r.get("outstanding")) for r in rows if bool(r.get("approved_waiting")))
        grand_total += total
        grand_count += len(rows)
        grand_overdue += overdue
        grand_approved += approved

        code = _text(ws.get("contractor_code")) or f"WS-{wid}"
        name = _text(ws.get("contractor_name")) or _text(ws.get("project_name"))
        lines += [
            "",
            f"### [IPC-CASH-WORKSPACE:{code}] {name} | workspace={wid} | HĐ={_text(ws.get('contract_no'))}",
            f"IPC chưa thanh toán={len(rows)} | Còn phải thanh toán={_money(total)} | Đã duyệt chờ thanh toán={_money(approved)} | Quá hạn={_money(overdue)}.",
        ]

        for row in rows[:MAX_ROWS]:
            due = _date_text(row.get("due_date")) or "Chưa ghi nhận"
            lines.append(
                f"[IPC-CASH:{code}:{_text(row.get('claim_code'))}] trạng thái={_text(row.get('status'))} | "
                f"đề nghị={_money(row.get('requested_amount'))} | duyệt={_money(row.get('approved_amount'))} | "
                f"đã thanh toán={_money(row.get('paid_amount'))} | còn phải thanh toán={_money(row.get('outstanding'))} | "
                f"hạn thanh toán={due} | quá hạn={int(row.get('overdue_days') or 0)} ngày."
            )

    lines += [
        "",
        "### [IPC-CASH-PROJECT-TOTAL] TỔNG PHẠM VI ĐƯỢC PHÉP ĐỌC",
        f"IPC chưa thanh toán={grand_count} | Còn phải thanh toán={_money(grand_total)} | Đã duyệt chờ thanh toán={_money(grand_approved)} | Quá hạn={_money(grand_overdue)}.",
        "Khi trả lời, gọi đây là dự trù/nhu cầu thanh toán IPC hiện hữu; không gọi là forecast hay dự báo tương lai.",
    ]
    return "\n".join(lines)


def install_cashflow_ai_context() -> None:
    import qlda.runtime_core.ai_service as ai_service

    cls = ai_service.ProjectContextBuilder
    if getattr(cls, "_qlda_cashflow_ai_context_installed", False):
        return
    original_build = cls.build

    def build_with_cashflow(self, project_id: int, question: str = "", status_date=None,
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
            appendix = f"\n## DỰ TRÙ DÒNG TIỀN THANH TOÁN IPC\nKhông đọc được dữ liệu IPC chưa thanh toán ở lượt này: {exc}"
        return snapshot.rstrip() + ("\n" + appendix if appendix else "") + "\n"

    cls.build = build_with_cashflow
    cls._qlda_cashflow_ai_context_installed = True
    cls._qlda_cashflow_ai_context_marker = PATCH_MARKER


__all__ = ["install_cashflow_ai_context"]
