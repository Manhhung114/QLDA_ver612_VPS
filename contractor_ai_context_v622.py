from __future__ import annotations

import re
import unicodedata
from datetime import date
from typing import Any

from contractor_workspace_v622 import contractor_rows_connection, resolve_master_project_id_connection


PATCH_MARKER = "V6.22 MULTI CONTRACTOR AI V1"


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("đ", "d")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


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


def _fmt_money(value: Any) -> str:
    try:
        return f"{float(value or 0):,.0f}"
    except Exception:
        return "0"


def _scalar(connection, sql: str, params=(), default=0):
    try:
        row = connection.execute(sql, params).fetchone()
        if not row:
            return default
        try:
            return row[0] if row[0] is not None else default
        except Exception:
            data = _rowdict(row)
            value = next(iter(data.values())) if data else default
            return default if value is None else value
    except Exception:
        return default


def _table_exists(connection, table: str) -> bool:
    try:
        connection.execute(f"SELECT 1 FROM {table} LIMIT 1")
        return True
    except Exception:
        return False


def _finance_intent(question: str) -> bool:
    q = _norm(question)
    return any(term in q for term in (
        "boq", "chi phi", "ngan sach", "vat tu", "vat lieu", "nhan cong",
        "claim", "ipc", "icp", "thanh toan", "giai ngan", "con lai", "hop dong",
        "nghiem thu", "phat sinh", "vo",
    ))


def _scope_stats(connection, workspace_id: int) -> dict[str, Any]:
    pid = int(workspace_id)
    tables = {
        "tasks": "tasks", "documents": "documents", "drawings": "drawings",
        "boq_rows": "cost_budgets", "claims": "payment_claims", "vo": "cost_variations",
        "materials": "material_master", "procurements": "procurement_schedule",
        "inventory": "inventory_inspection", "approvals": "approval_workflows",
    }
    out: dict[str, Any] = {}
    for key, table in tables.items():
        out[key] = int(_scalar(connection, f"SELECT COUNT(*) FROM {table} WHERE project_id=?", (pid,), 0)) if _table_exists(connection, table) else 0
    out["bac"] = float(_scalar(connection, "SELECT COALESCE(SUM(budget_total),0) FROM cost_budgets WHERE project_id=?", (pid,), 0)) if _table_exists(connection, "cost_budgets") else 0.0
    out["vo_approved"] = float(_scalar(connection, "SELECT COALESCE(SUM(approved_amount),0) FROM cost_variations WHERE project_id=?", (pid,), 0)) if _table_exists(connection, "cost_variations") else 0.0
    out["claim_disbursed"] = float(_scalar(connection, "SELECT COALESCE(SUM(disbursed_amount),0) FROM payment_claims WHERE project_id=?", (pid,), 0)) if _table_exists(connection, "payment_claims") else 0.0
    return out


def multi_contractor_component_aggregate(connection, contractors: list[dict[str, Any]]) -> dict[str, Any]:
    """Calculate project material/labor remainder as a sum of contractor remainders.

    Highest IPC is deliberately resolved inside each contractor workspace by the
    existing validated project_remaining_components() calculator. Cumulative IPC
    periods are never added within one contractor.
    """
    from project_remaining_components_v622 import project_remaining_components

    rows: list[dict[str, Any]] = []
    totals = {
        "boq_material_total": 0.0,
        "boq_labor_total": 0.0,
        "ipc_material_cumulative": 0.0,
        "ipc_labor_cumulative": 0.0,
        "remaining_material": 0.0,
        "remaining_labor": 0.0,
    }
    invalid: list[str] = []
    for contractor in contractors:
        wid = int(contractor.get("workspace_project_id") or 0)
        if wid <= 0:
            continue
        try:
            result = project_remaining_components(connection, wid)
        except Exception as exc:
            result = {"ok": False, "valid": False, "reason": str(exc)}
        row = {
            "contractor_code": str(contractor.get("contractor_code") or ""),
            "contractor_name": str(contractor.get("contractor_name") or ""),
            "workspace_project_id": wid,
            **dict(result or {}),
        }
        rows.append(row)
        if not row.get("valid"):
            invalid.append(row["contractor_code"] or row["contractor_name"] or str(wid))
            continue
        for key in totals:
            totals[key] += float(row.get(key) or 0)

    return {
        "ok": bool(rows),
        "valid": bool(rows) and not invalid,
        "contractor_count": len(rows),
        "valid_contractor_count": len(rows) - len(invalid),
        "invalid_contractors": invalid,
        "rows": rows,
        **totals,
        "remaining_total": totals["remaining_material"] + totals["remaining_labor"],
    }


def _project_aggregate_block(builder, master_id: int, contractors: list[dict[str, Any]], question: str) -> str:
    lines = [
        "# PROJECT AI — PHẠM VI TOÀN DỰ ÁN / TẤT CẢ NHÀ THẦU",
        f"[PROJECT-MULTI-CONTRACTOR] Dự án có {len(contractors)} workspace nhà thầu đang được AI quét.",
        "QUY TẮC BẮT BUỘC: mọi số liệu phải giữ ranh giới nhà thầu. Không trộn BOQ/Claim/VO của hai nhà thầu thành một IPC.",
        "IPC lớn nhất được xác định RIÊNG CHO TỪNG NHÀ THẦU. Không có khái niệm một IPC lớn nhất chung cho nhiều nhà thầu.",
        "Lũy kế của một nhà thầu = lũy kế IPC có số kỳ lớn nhất của chính nhà thầu đó; KHÔNG cộng IPC-01 + IPC-02 + ... trong cùng nhà thầu.",
    ]

    project_totals = {
        "tasks": 0, "documents": 0, "drawings": 0, "boq_rows": 0, "claims": 0,
        "vo": 0, "materials": 0, "approvals": 0, "bac": 0.0, "vo_approved": 0.0,
        "claim_disbursed": 0.0,
    }
    with builder.connect() as connection:
        master = connection.execute("SELECT code,name FROM projects WHERE id=?", (int(master_id),)).fetchone()
        master_data = _rowdict(master)
        if master_data:
            lines.insert(1, f"Dự án: {master_data.get('code','')} - {master_data.get('name','')}")
        lines += ["", "## TỔNG HỢP THEO NHÀ THẦU"]
        for contractor in contractors:
            stats = _scope_stats(connection, int(contractor["workspace_project_id"]))
            for key in project_totals:
                project_totals[key] += stats.get(key, 0) or 0
            lines.append(
                f"[CONTRACTOR-SUMMARY:{contractor.get('contractor_code','')}] {contractor.get('contractor_name','')} | "
                f"HĐ={contractor.get('contract_no','')} | gói={contractor.get('package_name','')} | "
                f"tiến độ={stats['tasks']:,} | hồ sơ={stats['documents']:,} | bản vẽ={stats['drawings']:,} | "
                f"BOQ={stats['boq_rows']:,} dòng/{_fmt_money(stats['bac'])} VND | Claim={stats['claims']:,} | "
                f"VO={stats['vo']:,}/{_fmt_money(stats['vo_approved'])} VND | giải ngân={_fmt_money(stats['claim_disbursed'])} VND"
            )

        lines += [
            "",
            "## PROJECT AGGREGATE — TOÀN BỘ NHÀ THẦU",
            f"Tổng tiến độ: {int(project_totals['tasks']):,} công việc | hồ sơ {int(project_totals['documents']):,} | bản vẽ {int(project_totals['drawings']):,}.",
            f"Tổng BOQ database: {int(project_totals['boq_rows']):,} dòng | BAC {_fmt_money(project_totals['bac'])} VND.",
            f"Tổng Claim: {int(project_totals['claims']):,} | tổng giải ngân {_fmt_money(project_totals['claim_disbursed'])} VND | VO duyệt {_fmt_money(project_totals['vo_approved'])} VND.",
        ]

        if _finance_intent(question):
            finance = multi_contractor_component_aggregate(connection, contractors)
            lines += [
                "",
                "## PROJECT-COMPONENT-AGGREGATE — VẬT TƯ / NHÂN CÔNG ĐÃ KIỂM SOÁT",
                "CÔNG THỨC TOÀN DỰ ÁN: Σ theo từng nhà thầu [BOQ FULL-SCAN nhà thầu − lũy kế IPC lớn nhất của chính nhà thầu].",
            ]
            for row in finance.get("rows") or []:
                code = row.get("contractor_code") or "?"
                name = row.get("contractor_name") or ""
                if row.get("valid"):
                    lines.append(
                        f"[CONTRACTOR-REMAINING:{code}] {name} | IPC lớn nhất={row.get('latest_claim_code','')} | "
                        f"BOQ VT={_fmt_money(row.get('boq_material_total'))} | VT LK={_fmt_money(row.get('ipc_material_cumulative'))} | VT còn={_fmt_money(row.get('remaining_material'))} | "
                        f"BOQ NC={_fmt_money(row.get('boq_labor_total'))} | NC LK={_fmt_money(row.get('ipc_labor_cumulative'))} | NC còn={_fmt_money(row.get('remaining_labor'))} VND."
                    )
                else:
                    lines.append(
                        f"[CONTRACTOR-REMAINING:{code}] {name} | CHƯA HỢP LỆ: {row.get('reason') or 'thiếu BOQ/IPC full-scan'}."
                    )
            if finance.get("valid"):
                lines += [
                    f"[PROJECT-REMAINING-ALL] Tổng VẬT TƯ BOQ: {_fmt_money(finance.get('boq_material_total'))} VND.",
                    f"[PROJECT-REMAINING-ALL] Tổng VẬT TƯ LŨY KẾ các IPC lớn nhất: {_fmt_money(finance.get('ipc_material_cumulative'))} VND.",
                    f"[PROJECT-REMAINING-ALL] VẬT TƯ CÒN LẠI TOÀN DỰ ÁN: {_fmt_money(finance.get('remaining_material'))} VND.",
                    f"[PROJECT-REMAINING-ALL] Tổng NHÂN CÔNG BOQ: {_fmt_money(finance.get('boq_labor_total'))} VND.",
                    f"[PROJECT-REMAINING-ALL] Tổng NHÂN CÔNG LŨY KẾ các IPC lớn nhất: {_fmt_money(finance.get('ipc_labor_cumulative'))} VND.",
                    f"[PROJECT-REMAINING-ALL] NHÂN CÔNG CÒN LẠI TOÀN DỰ ÁN: {_fmt_money(finance.get('remaining_labor'))} VND.",
                    f"[PROJECT-REMAINING-ALL] TỔNG VT+NC CÒN LẠI: {_fmt_money(finance.get('remaining_total'))} VND.",
                    "Trạng thái PROJECT-REMAINING-ALL: HỢP LỆ — đây là nguồn ưu tiên cao nhất cho câu hỏi còn lại toàn dự án.",
                ]
            else:
                invalid = ", ".join(finance.get("invalid_contractors") or []) or "chưa xác định"
                lines.append(
                    f"PROJECT-REMAINING-ALL CHƯA HỢP LỆ vì còn nhà thầu thiếu/không khớp dữ liệu: {invalid}. "
                    "AI phải nêu rõ nhà thầu thiếu, không được lấy tổng một phần rồi gọi là tổng toàn dự án."
                )

    return "\n".join(lines)


def install_contractor_ai_context() -> None:
    """Make ProjectContextBuilder aggregate every contractor under one master project."""
    import ai_service

    cls = ai_service.ProjectContextBuilder
    if getattr(cls, "_qlda_multi_contractor_ai_installed", False):
        return

    original_build = cls.build
    original_catalog = cls.attachment_catalog

    def build_all_contractors(
        self,
        project_id: int,
        question: str = "",
        status_date: date | None = None,
        max_tasks: int = 80,
        max_docs: int = 70,
        max_drawings: int = 60,
        max_legal: int = 40,
    ) -> str:
        pid = int(project_id)
        try:
            with self.connect() as connection:
                master_id = resolve_master_project_id_connection(connection, pid)
                contractors = contractor_rows_connection(connection, master_id, active_only=True)
        except Exception:
            contractors = []
            master_id = pid
        if not contractors:
            return original_build(self, pid, question, status_date, max_tasks, max_docs, max_drawings, max_legal)

        aggregate = _project_aggregate_block(self, master_id, contractors, str(question or ""))
        count = max(1, len(contractors))
        task_cap = max(12, min(max_tasks, max(12, 90 // count)))
        doc_cap = max(10, min(max_docs, max(10, 75 // count)))
        drawing_cap = max(8, min(max_drawings, max(8, 60 // count)))
        parts = [aggregate]
        for index, contractor in enumerate(contractors):
            wid = int(contractor.get("workspace_project_id") or 0)
            if wid <= 0:
                continue
            code = str(contractor.get("contractor_code") or "")
            name = str(contractor.get("contractor_name") or "")
            parts += [
                "",
                f"# [CONTRACTOR:{code}|{name}] WORKSPACE DATA",
                f"Hợp đồng={contractor.get('contract_no','')} | Gói thầu={contractor.get('package_name','')} | workspace={wid}",
                "LƯU Ý: mọi mục [TASK]/[DOC]/[DRAWING]/BOQ/IPC/VO trong block này thuộc đúng nhà thầu nêu trên.",
                original_build(
                    self, wid, question, status_date,
                    task_cap, doc_cap, drawing_cap, max_legal if index == 0 else 0,
                ),
            ]
        parts += [
            "",
            "# QUY TẮC KẾT LUẬN ĐA NHÀ THẦU",
            "Nếu người dùng không chỉ định nhà thầu, mặc định đánh giá TOÀN DỰ ÁN và tất cả nhà thầu.",
            "Nếu người dùng chỉ định một nhà thầu, có thể tập trung nhà thầu đó nhưng không được gán dữ liệu của nhà thầu khác cho họ.",
            "Khi tổng hợp chi phí/còn lại toàn dự án, ưu tiên PROJECT-COMPONENT-AGGREGATE; không tự cộng các subtotal prompt bị giới hạn dòng.",
        ]
        return "\n".join(str(part or "") for part in parts)

    def attachment_catalog_all_contractors(self, project_id: int) -> list[dict]:
        pid = int(project_id)
        try:
            with self.connect() as connection:
                master_id = resolve_master_project_id_connection(connection, pid)
                contractors = contractor_rows_connection(connection, master_id, active_only=True)
        except Exception:
            contractors = []
        if not contractors:
            return original_catalog(self, pid)
        out: list[dict] = []
        seen: set[int] = set()
        for contractor in contractors:
            wid = int(contractor.get("workspace_project_id") or 0)
            if wid <= 0:
                continue
            try:
                rows = original_catalog(self, wid)
            except Exception:
                rows = []
            for raw in rows:
                item = dict(raw or {})
                try:
                    attachment_id = int(item.get("id") or 0)
                except Exception:
                    attachment_id = 0
                if attachment_id and attachment_id in seen:
                    continue
                if attachment_id:
                    seen.add(attachment_id)
                item["contractor_code"] = str(contractor.get("contractor_code") or "")
                item["contractor_name"] = str(contractor.get("contractor_name") or "")
                item["workspace_project_id"] = wid
                out.append(item)
        return out

    cls.build = build_all_contractors
    cls.attachment_catalog = attachment_catalog_all_contractors
    cls._qlda_multi_contractor_ai_installed = True
    cls._qlda_multi_contractor_ai_marker = PATCH_MARKER
