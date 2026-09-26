from __future__ import annotations

"""Query-aware detailed PostgreSQL context for project chat.

The base project-chat context intentionally keeps compact deterministic summaries.
This module adds the actual business rows behind those counts so the model cannot
say "there is one record but no detail" when the detail is already stored in the
same authorized workspace.  It is deliberately data-only and has no runtime_core
imports.
"""

import re
import unicodedata
from typing import Any, Iterable, Sequence


_DOC_LABELS = {
    "BBHOP": "Biên bản họp",
    "BBHT": "Biên bản hiện trường",
    "NCR": "NCR",
    "RFI": "RFI",
    "RFA": "RFA",
    "NKCT": "Nhật ký công trường",
    "NTCV": "Nghiệm thu công việc",
    "NTVL": "Nghiệm thu vật liệu đầu vào",
    "KDVT": "Kiểm định vật tư",
}
_STOPWORDS = {
    "cho", "toi", "hay", "kiem", "tra", "du", "lieu", "cua", "va", "voi", "theo",
    "trong", "tren", "cac", "nhung", "bao", "nhieu", "tong", "hop", "duoc", "hien", "tai",
    "du", "an", "nay", "gan", "day", "nhat", "moi", "ve", "thong", "tin", "chi", "tiet",
}


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("đ", "d")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _terms(question: str) -> list[str]:
    out: list[str] = []
    for token in _norm(question).split():
        if len(token) >= 2 and token not in _STOPWORDS and token not in out:
            out.append(token)
    return out[:18]


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


def _table_exists(connection, table: str) -> bool:
    try:
        row = connection.execute(
            """SELECT EXISTS(
                SELECT 1 FROM information_schema.tables
                WHERE table_schema=current_schema() AND table_name=%s
            ) AS ok""",
            (str(table),),
        ).fetchone()
        return bool(row and _rowdict(row).get("ok"))
    except Exception:
        return False


def _fetch(connection, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    try:
        return [_rowdict(row) for row in connection.execute(sql, params).fetchall()]
    except Exception:
        return []


def _count(connection, table: str, scope_column: str, ids: Sequence[int]) -> int:
    if not _table_exists(connection, table):
        return 0
    try:
        row = connection.execute(
            f"SELECT COUNT(*) AS n FROM {table} WHERE {scope_column}=ANY(%s)",
            ([int(x) for x in ids],),
        ).fetchone()
        return int(_rowdict(row).get("n") or 0)
    except Exception:
        return 0


def _money(value: Any) -> str:
    try:
        return f"{float(value or 0):,.0f}"
    except Exception:
        return "0"


def _qty(value: Any) -> str:
    try:
        number = float(value or 0)
        if abs(number - round(number)) < 1e-9:
            return f"{number:,.0f}"
        return f"{number:,.4f}".rstrip("0").rstrip(".")
    except Exception:
        return str(value or "0")


def _clip(value: Any, limit: int = 420) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text if len(text) <= limit else text[: max(1, limit - 3)] + "..."


def _contains(qnorm: str, *phrases: str) -> bool:
    return any(_norm(phrase) in qnorm for phrase in phrases)


def _score(row: dict[str, Any], terms: Iterable[str], keys: Sequence[str]) -> int:
    haystack = _norm(" ".join(str(row.get(key) or "") for key in keys))
    return sum(1 for term in terms if term and term in haystack)


def _rank(
    rows: Sequence[dict[str, Any]],
    terms: Sequence[str],
    keys: Sequence[str],
    *,
    limit: int = 30,
) -> list[dict[str, Any]]:
    if not rows:
        return []
    scored = [(_score(row, terms, keys), idx, row) for idx, row in enumerate(rows)]
    matched = [item for item in scored if item[0] > 0]
    if matched:
        matched.sort(key=lambda item: (item[0], -item[1]), reverse=True)
        return [item[2] for item in matched[:limit]]
    return list(rows[:limit])


def _expand(count: int, intent: bool) -> bool:
    return bool(intent or (0 < int(count) <= 8))


def _domain_counts(connection, ids: list[int]) -> dict[str, int]:
    specs = {
        "documents": ("documents", "project_id"),
        "drawings": ("drawings", "project_id"),
        "boq": ("cost_budgets", "project_id"),
        "payments": ("payment_tracking", "project_id"),
        "ipc": ("payment_claims", "project_id"),
        "vo": ("cost_variations", "project_id"),
        "vo_excel": ("variation_orders", "project_id"),
        "materials": ("material_master", "project_id"),
        "procurement": ("procurement_schedule", "project_id"),
        "inventory": ("inventory_inspection", "project_id"),
        "work_tasks": ("project_work_tasks", "workspace_project_id"),
        "contracts": ("project_contract_records", "workspace_project_id"),
        "approvals": ("approval_workflows", "project_id"),
        "owner_plans": ("owner_material_plans", "workspace_project_id"),
        "owner_ledger": ("owner_material_ledger", "workspace_project_id"),
        "cost_snapshots": ("project_cost_control_snapshots", "workspace_project_id"),
    }
    return {name: _count(connection, table, column, ids) for name, (table, column) in specs.items()}


def _document_context(connection, ids: list[int], question: str, count: int) -> list[str]:
    qnorm = _norm(question)
    intent = _contains(
        qnorm,
        "ho so", "rfi", "ncr", "rfa", "bien ban", "nghiem thu", "kiem dinh", "nhat ky",
        "bbhop", "bbht", "ntcv", "ntvl", "kdvt", "nkct", "cuoc hop",
    )
    if not _expand(count, intent):
        return []
    rows = _fetch(
        connection,
        """SELECT id,project_id,doc_type,code,subject,discipline,contractor,issuer,assignee,
                  issue_date,due_date,closed_date,status,priority,related_wbs,description,response,note,
                  cost_impact,time_impact_days,created_at,updated_at
           FROM documents WHERE project_id=ANY(%s)
           ORDER BY COALESCE(NULLIF(issue_date,''),NULLIF(updated_at,''),NULLIF(created_at,'')) DESC,id DESC
           LIMIT 2500""",
        (ids,),
    )
    if not rows:
        return []
    meeting = _contains(qnorm, "bien ban hop", "cuoc hop", "hop gan day", "bbhop")
    candidates = [r for r in rows if str(r.get("doc_type") or "").upper() == "BBHOP"] if meeting else rows
    if meeting and not candidates:
        candidates = rows
    selected = _rank(
        candidates,
        _terms(question),
        ("doc_type", "code", "subject", "discipline", "contractor", "issuer", "assignee", "description", "response", "note", "related_wbs"),
        limit=35,
    )
    lines = ["### HỒ SƠ / RFI / NCR / BIÊN BẢN LIVE"]
    for row in selected:
        dtype = str(row.get("doc_type") or "").upper()
        label = _DOC_LABELS.get(dtype, dtype or "Hồ sơ")
        lines.append(
            f"[DOC:{row.get('id','')}] {label} | mã={row.get('code','')} | ngày={row.get('issue_date','')} | "
            f"tiêu đề={_clip(row.get('subject'),220)} | trạng thái={row.get('status','')} | ưu tiên={row.get('priority','')} | "
            f"đơn vị={_clip(row.get('contractor'),120)} | phát hành/lập={_clip(row.get('issuer'),120)} | "
            f"phụ trách/thành phần={_clip(row.get('assignee'),180)} | WBS={row.get('related_wbs','')} | "
            f"nội dung={_clip(row.get('description'),520)} | kết luận/phản hồi={_clip(row.get('response'),520)} | "
            f"ghi chú={_clip(row.get('note'),260)} | ảnh hưởng chi phí={_money(row.get('cost_impact'))} | "
            f"ảnh hưởng thời gian={int(row.get('time_impact_days') or 0)} ngày"
        )
    doc_ids = [int(row.get("id") or 0) for row in selected if int(row.get("id") or 0) > 0]
    if doc_ids and _table_exists(connection, "document_attachments"):
        files = _fetch(
            connection,
            """SELECT document_id,file_name,mime_type,storage_backend,created_at
               FROM document_attachments WHERE document_id=ANY(%s)
               ORDER BY id DESC LIMIT 120""",
            (doc_ids,),
        )
        for row in files:
            lines.append(
                f"[DOC-FILE:{row.get('document_id','')}] file={_clip(row.get('file_name'),220)} | "
                f"mime={row.get('mime_type','')} | lưu trữ={row.get('storage_backend','')} | tạo={row.get('created_at','')}"
            )
    return lines


def _drawing_context(connection, ids: list[int], question: str, count: int) -> list[str]:
    qnorm = _norm(question)
    intent = _contains(qnorm, "ban ve", "drawing", "shopdrawing", "shop drawing", "hoan cong", "as built", "revision", "rev")
    if not _expand(count, intent):
        return []
    rows = _fetch(
        connection,
        """SELECT id,project_id,drawing_type,drawing_no,title,discipline,revision,issuer,receiver,
                  received_date,issue_date,due_date,priority,description,status,related_wbs,reference_no,note,
                  file_updated_at,created_at,updated_at
           FROM drawings WHERE project_id=ANY(%s)
           ORDER BY COALESCE(NULLIF(received_date,''),NULLIF(issue_date,''),NULLIF(updated_at,'')) DESC,id DESC
           LIMIT 2500""",
        (ids,),
    )
    selected = _rank(rows, _terms(question), ("drawing_type", "drawing_no", "title", "discipline", "revision", "description", "status", "related_wbs", "reference_no", "note"), limit=35)
    if not selected:
        return []
    lines = ["### BẢN VẼ LIVE"]
    for row in selected:
        lines.append(
            f"[DRAWING:{row.get('id','')}] loại={row.get('drawing_type','')} | số={row.get('drawing_no','')} | "
            f"rev={row.get('revision','')} | tiêu đề={_clip(row.get('title'),260)} | bộ môn={row.get('discipline','')} | "
            f"trạng thái={row.get('status','')} | nhận={row.get('received_date','')} | phát hành={row.get('issue_date','')} | "
            f"hạn={row.get('due_date','')} | WBS={row.get('related_wbs','')} | mô tả={_clip(row.get('description'),420)} | "
            f"ghi chú={_clip(row.get('note'),240)}"
        )
    drawing_ids = [int(row.get("id") or 0) for row in selected if int(row.get("id") or 0) > 0]
    if drawing_ids and _table_exists(connection, "drawing_attachments"):
        files = _fetch(
            connection,
            """SELECT drawing_id,file_name,mime_type,storage_backend,created_at
               FROM drawing_attachments WHERE drawing_id=ANY(%s)
               ORDER BY id DESC LIMIT 120""",
            (drawing_ids,),
        )
        for row in files:
            lines.append(
                f"[DRAWING-FILE:{row.get('drawing_id','')}] file={_clip(row.get('file_name'),220)} | "
                f"mime={row.get('mime_type','')} | lưu trữ={row.get('storage_backend','')}"
            )
    return lines


def _finance_context(connection, ids: list[int], question: str, counts: dict[str, int]) -> list[str]:
    qnorm = _norm(question)
    boq_intent = _contains(qnorm, "boq", "khoi luong", "don gia", "du toan", "ngan sach", "budget")
    pay_intent = _contains(qnorm, "thanh toan", "ipc", "claim", "giai ngan", "tam ung", "thu hoi tam ung")
    vo_intent = _contains(qnorm, "vo", "phat sinh", "variation", "thay doi gia tri")
    cost_intent = _contains(qnorm, "chi phi", "tai chinh", "evm", "actual cost", "cpi", "spi")
    lines: list[str] = []

    if _expand(counts.get("boq", 0), boq_intent or cost_intent):
        rows = _fetch(
            connection,
            """SELECT id,project_id,task_ref,boq_item,quantity,unit,unit_price,budget_total,contract_type,contractor,note,updated_at
               FROM cost_budgets WHERE project_id=ANY(%s) ORDER BY id DESC LIMIT 20000""",
            (ids,),
        )
        selected = _rank(rows, _terms(question), ("task_ref", "boq_item", "unit", "contract_type", "contractor", "note"), limit=45)
        if selected:
            lines.append("### BOQ / NGÂN SÁCH LIVE")
            qty_sum = sum(float(row.get("quantity") or 0) for row in selected)
            amount_sum = sum(float(row.get("budget_total") or 0) for row in selected)
            lines.append(f"[BOQ-MATCH-SUMMARY] dòng đưa vào={len(selected)} | tổng SL các dòng={_qty(qty_sum)} | tổng giá trị={_money(amount_sum)} VND")
            for row in selected:
                lines.append(
                    f"[BOQ:{row.get('id','')}] { _clip(row.get('boq_item'),300)} | SL={_qty(row.get('quantity'))} {row.get('unit','')} | "
                    f"đơn giá={_money(row.get('unit_price'))} | thành tiền={_money(row.get('budget_total'))} VND | "
                    f"WBS/task={row.get('task_ref','')} | loại HĐ={row.get('contract_type','')} | nhà thầu={_clip(row.get('contractor'),150)} | ghi chú={_clip(row.get('note'),220)}"
                )

    if _expand(counts.get("payments", 0), pay_intent or cost_intent):
        rows = _fetch(
            connection,
            """SELECT id,project_id,payment_code,task_ref,installment,certified_cumulative,paid_amount,
                      advance_amount,advance_recovery,planned_disbursement_pct,payment_status,payment_date,note,updated_at
               FROM payment_tracking WHERE project_id=ANY(%s)
               ORDER BY COALESCE(NULLIF(payment_date,''),NULLIF(updated_at,'')) DESC,id DESC LIMIT 500""",
            (ids,),
        )
        selected = _rank(rows, _terms(question), ("payment_code", "task_ref", "installment", "payment_status", "payment_date", "note"), limit=30)
        if selected:
            lines.append("### THANH TOÁN LIVE")
            for row in selected:
                lines.append(
                    f"[PAYMENT:{row.get('id','')}] mã={row.get('payment_code','')} | kỳ={row.get('installment','')} | "
                    f"ngày={row.get('payment_date','')} | trạng thái={row.get('payment_status','')} | "
                    f"lũy kế xác nhận={_money(row.get('certified_cumulative'))} | đã trả={_money(row.get('paid_amount'))} | "
                    f"tạm ứng={_money(row.get('advance_amount'))} | thu hồi={_money(row.get('advance_recovery'))} | "
                    f"task={row.get('task_ref','')} | ghi chú={_clip(row.get('note'),320)}"
                )

    if _expand(counts.get("ipc", 0), pay_intent):
        rows = _fetch(
            connection,
            """SELECT claim_id,project_id,claim_no,claim_code,filename,contractor,contract_no,package_name,
                      from_date,to_date,contract_value,requested_amount,approved_amount,disbursed_amount,
                      certified_cumulative,previous_approved,retention_cumulative,advance_amount,advance_recovery,
                      current_deductions,payment_status,disbursement_date,latest_revision,note,updated_at
               FROM payment_claims WHERE project_id=ANY(%s) ORDER BY updated_at DESC LIMIT 300""",
            (ids,),
        )
        selected = _rank(rows, _terms(question), ("claim_no", "claim_code", "filename", "contractor", "contract_no", "package_name", "payment_status", "note"), limit=25)
        if selected:
            lines.append("### IPC / HỒ SƠ THANH TOÁN LIVE")
            for row in selected:
                lines.append(
                    f"[IPC:{row.get('claim_id','')}] {row.get('claim_code','')} | HĐ={row.get('contract_no','')} | nhà thầu={_clip(row.get('contractor'),140)} | "
                    f"kỳ={row.get('from_date','')}→{row.get('to_date','')} | yêu cầu={_money(row.get('requested_amount'))} | "
                    f"duyệt={_money(row.get('approved_amount'))} | giải ngân={_money(row.get('disbursed_amount'))} | "
                    f"trạng thái={row.get('payment_status','')} | file={_clip(row.get('filename'),180)} | ghi chú={_clip(row.get('note'),260)}"
                )

    if _expand(counts.get("vo", 0), vo_intent or cost_intent):
        rows = _fetch(
            connection,
            """SELECT id,project_id,vo_code,task_ref,description,proposed_amount,approved_amount,funding_source,status,vo_date,note,updated_at
               FROM cost_variations WHERE project_id=ANY(%s)
               ORDER BY COALESCE(NULLIF(vo_date,''),NULLIF(updated_at,'')) DESC,id DESC LIMIT 500""",
            (ids,),
        )
        selected = _rank(rows, _terms(question), ("vo_code", "task_ref", "description", "funding_source", "status", "note"), limit=30)
        if selected:
            lines.append("### PHÁT SINH / VO LIVE")
            for row in selected:
                lines.append(
                    f"[VO:{row.get('id','')}] {row.get('vo_code','')} | ngày={row.get('vo_date','')} | "
                    f"nội dung={_clip(row.get('description'),360)} | đề xuất={_money(row.get('proposed_amount'))} | "
                    f"duyệt={_money(row.get('approved_amount'))} | nguồn={row.get('funding_source','')} | "
                    f"trạng thái={row.get('status','')} | task={row.get('task_ref','')} | ghi chú={_clip(row.get('note'),260)}"
                )

    if _expand(counts.get("vo_excel", 0), vo_intent):
        rows = _fetch(
            connection,
            """SELECT vo_id,project_id,vo_no,vo_code,filename,revision_label,vo_date,project_name,package_name,
                      subtotal_before_vat,vat_amount,total_after_vat,increase_amount,decrease_amount,proposed_amount,
                      approved_amount,funding_source,status,latest_revision,note,updated_at
               FROM variation_orders WHERE project_id=ANY(%s) ORDER BY updated_at DESC LIMIT 300""",
            (ids,),
        )
        selected = _rank(rows, _terms(question), ("vo_code", "filename", "revision_label", "project_name", "package_name", "funding_source", "status", "note"), limit=25)
        if selected:
            lines.append("### VO EXCEL LIVE")
            for row in selected:
                lines.append(
                    f"[VO-EXCEL:{row.get('vo_id','')}] {row.get('vo_code','')} {row.get('revision_label','')} | ngày={row.get('vo_date','')} | "
                    f"gói={_clip(row.get('package_name'),180)} | trước VAT={_money(row.get('subtotal_before_vat'))} | "
                    f"sau VAT={_money(row.get('total_after_vat'))} | tăng={_money(row.get('increase_amount'))} | giảm={_money(row.get('decrease_amount'))} | "
                    f"duyệt={_money(row.get('approved_amount'))} | trạng thái={row.get('status','')} | file={_clip(row.get('filename'),180)}"
                )

    if cost_intent and _table_exists(connection, "project_cost_settings"):
        settings = _fetch(
            connection,
            """SELECT workspace_project_id,currency,baseline_work_cost,contingency_reserve,management_reserve,
                      estimate_tolerance_pct,control_threshold_pct,baseline_date,note,updated_at
               FROM project_cost_settings WHERE workspace_project_id=ANY(%s) ORDER BY updated_at DESC""",
            (ids,),
        )
        snaps = _fetch(
            connection,
            """SELECT workspace_project_id,status_date,actual_cost,note,updated_at
               FROM project_cost_control_snapshots WHERE workspace_project_id=ANY(%s)
               ORDER BY status_date DESC,id DESC LIMIT 60""",
            (ids,),
        ) if _table_exists(connection, "project_cost_control_snapshots") else []
        if settings or snaps:
            lines.append("### KIỂM SOÁT CHI PHÍ / EVM LIVE")
            for row in settings:
                lines.append(
                    f"[COST-SETTING] workspace={row.get('workspace_project_id','')} | baseline={_money(row.get('baseline_work_cost'))} | "
                    f"contingency={_money(row.get('contingency_reserve'))} | management reserve={_money(row.get('management_reserve'))} | "
                    f"ngày baseline={row.get('baseline_date','')} | ghi chú={_clip(row.get('note'),220)}"
                )
            for row in snaps[:20]:
                lines.append(
                    f"[COST-SNAPSHOT] workspace={row.get('workspace_project_id','')} | ngày={row.get('status_date','')} | "
                    f"actual cost={_money(row.get('actual_cost'))} | ghi chú={_clip(row.get('note'),220)}"
                )
    return lines


def _material_context(connection, ids: list[int], question: str, counts: dict[str, int]) -> list[str]:
    qnorm = _norm(question)
    material_intent = _contains(qnorm, "vat tu", "vat lieu", "thiet bi", "material", "spec", "brand")
    procurement_intent = _contains(qnorm, "mua sam", "dat hang", "giao hang", "supplier", "nha cung cap")
    inventory_intent = _contains(qnorm, "nhap kho", "xuat kho", "kiem dinh", "inspection", "ton kho", "kho")
    owner_intent = _contains(qnorm, "chu dau tu cap", "cdt cap", "vat tu cdt", "cđt cap", "hao hut", "ban giao vat tu")
    lines: list[str] = []

    if _expand(counts.get("materials", 0), material_intent):
        rows = _fetch(
            connection,
            """SELECT id,project_id,material_code,material_name,spec_brand,legal_ref,supply_type,task_ref,note,updated_at
               FROM material_master WHERE project_id=ANY(%s) ORDER BY id DESC LIMIT 5000""",
            (ids,),
        )
        selected = _rank(rows, _terms(question), ("material_code", "material_name", "spec_brand", "legal_ref", "supply_type", "task_ref", "note"), limit=35)
        if selected:
            lines.append("### VẬT TƯ / THIẾT BỊ LIVE")
            for row in selected:
                lines.append(
                    f"[MATERIAL:{row.get('id','')}] mã={row.get('material_code','')} | { _clip(row.get('material_name'),260)} | "
                    f"spec/brand={_clip(row.get('spec_brand'),220)} | hồ sơ pháp lý={_clip(row.get('legal_ref'),180)} | "
                    f"nguồn cấp={row.get('supply_type','')} | task={row.get('task_ref','')} | ghi chú={_clip(row.get('note'),240)}"
                )

    if _expand(counts.get("procurement", 0), procurement_intent or material_intent):
        rows = _fetch(
            connection,
            """SELECT id,project_id,material_code,task_ref,supplier,sample_approval_date,order_date,
                      planned_delivery_date,actual_delivery_date,status,note,updated_at
               FROM procurement_schedule WHERE project_id=ANY(%s)
               ORDER BY COALESCE(NULLIF(planned_delivery_date,''),NULLIF(order_date,''),NULLIF(updated_at,'')) DESC,id DESC LIMIT 2000""",
            (ids,),
        )
        selected = _rank(rows, _terms(question), ("material_code", "task_ref", "supplier", "status", "note"), limit=35)
        if selected:
            lines.append("### MUA SẮM / GIAO HÀNG LIVE")
            for row in selected:
                lines.append(
                    f"[PROCUREMENT:{row.get('id','')}] vật tư={row.get('material_code','')} | NCC={_clip(row.get('supplier'),180)} | "
                    f"duyệt mẫu={row.get('sample_approval_date','')} | đặt hàng={row.get('order_date','')} | "
                    f"KH giao={row.get('planned_delivery_date','')} | TT giao={row.get('actual_delivery_date','')} | "
                    f"trạng thái={row.get('status','')} | task={row.get('task_ref','')} | ghi chú={_clip(row.get('note'),240)}"
                )

    if _expand(counts.get("inventory", 0), inventory_intent or material_intent):
        rows = _fetch(
            connection,
            """SELECT id,project_id,slip_code,transaction_date,material_code,quantity_in,quantity_out,task_ref,
                      inspection_code,material_status,note,updated_at
               FROM inventory_inspection WHERE project_id=ANY(%s)
               ORDER BY COALESCE(NULLIF(transaction_date,''),NULLIF(updated_at,'')) DESC,id DESC LIMIT 2500""",
            (ids,),
        )
        selected = _rank(rows, _terms(question), ("slip_code", "material_code", "task_ref", "inspection_code", "material_status", "note"), limit=40)
        if selected:
            lines.append("### NHẬP / XUẤT / KIỂM ĐỊNH VẬT TƯ LIVE")
            for row in selected:
                lines.append(
                    f"[INVENTORY:{row.get('id','')}] phiếu={row.get('slip_code','')} | ngày={row.get('transaction_date','')} | "
                    f"vật tư={row.get('material_code','')} | nhập={_qty(row.get('quantity_in'))} | xuất={_qty(row.get('quantity_out'))} | "
                    f"kiểm định={row.get('inspection_code','')} | trạng thái={row.get('material_status','')} | "
                    f"task={row.get('task_ref','')} | ghi chú={_clip(row.get('note'),240)}"
                )

    if owner_intent or (0 < counts.get("owner_plans", 0) <= 8) or (0 < counts.get("owner_ledger", 0) <= 8):
        plans = _fetch(
            connection,
            """SELECT id,workspace_project_id,material_code,material_name,unit,boq_qty,vo_qty,planned_qty,
                      location,contractor_code,contractor_name,note,updated_at
               FROM owner_material_plans WHERE workspace_project_id=ANY(%s) ORDER BY id DESC LIMIT 2000""",
            (ids,),
        ) if _table_exists(connection, "owner_material_plans") else []
        ledger = _fetch(
            connection,
            """SELECT id,txn_code,workspace_project_id,txn_type,txn_date,material_code,material_name,unit,quantity,
                      warehouse_from,warehouse_to,contractor_code,contractor_name,location,document_no,task_ref,note,created_at
               FROM owner_material_ledger WHERE workspace_project_id=ANY(%s) AND COALESCE(voided,0)=0
               ORDER BY txn_date DESC,id DESC LIMIT 2500""",
            (ids,),
        ) if _table_exists(connection, "owner_material_ledger") else []
        plans_sel = _rank(plans, _terms(question), ("material_code", "material_name", "location", "contractor_code", "contractor_name", "note"), limit=30)
        ledger_sel = _rank(ledger, _terms(question), ("txn_code", "txn_type", "material_code", "material_name", "warehouse_from", "warehouse_to", "contractor_code", "contractor_name", "location", "document_no", "task_ref", "note"), limit=40)
        if plans_sel or ledger_sel:
            lines.append("### VẬT TƯ CHỦ ĐẦU TƯ CẤP LIVE")
            for row in plans_sel:
                lines.append(
                    f"[OWNER-MAT-PLAN:{row.get('id','')}] {row.get('material_code','')} - {_clip(row.get('material_name'),220)} | "
                    f"BOQ={_qty(row.get('boq_qty'))} | VO={_qty(row.get('vo_qty'))} | KH={_qty(row.get('planned_qty'))} {row.get('unit','')} | "
                    f"vị trí={row.get('location','')} | nhà thầu={_clip(row.get('contractor_name'),140)} | ghi chú={_clip(row.get('note'),220)}"
                )
            for row in ledger_sel:
                lines.append(
                    f"[OWNER-MAT-TXN:{row.get('id','')}] {row.get('txn_code','')} | {row.get('txn_type','')} | ngày={row.get('txn_date','')} | "
                    f"{row.get('material_code','')} - {_clip(row.get('material_name'),180)} | SL={_qty(row.get('quantity'))} {row.get('unit','')} | "
                    f"kho đi={row.get('warehouse_from','')} | kho đến={row.get('warehouse_to','')} | vị trí={row.get('location','')} | "
                    f"chứng từ={row.get('document_no','')} | task={row.get('task_ref','')} | ghi chú={_clip(row.get('note'),220)}"
                )
    return lines


def _work_contract_approval_context(connection, ids: list[int], question: str, counts: dict[str, int]) -> list[str]:
    qnorm = _norm(question)
    task_intent = _contains(qnorm, "nhiem vu", "giao viec", "viec duoc giao", "qua han", "cong viec noi bo")
    contract_intent = _contains(qnorm, "hop dong", "phu luc", "contract", "gia tri hop dong", "hieu luc")
    approval_intent = _contains(qnorm, "phe duyet", "duyet", "approval", "cho duyet", "trinh duyet", "tra sua", "chinh sua")
    lines: list[str] = []

    if _expand(counts.get("work_tasks", 0), task_intent):
        rows = _fetch(
            connection,
            """SELECT id,task_code,workspace_project_id,title,description,assignee_email,assignee_name,priority,status,
                      progress_percent,source_module,source_type,source_id,source_code,source_title,start_at,due_at,
                      completion_requested_at,completed_at,closed_at,created_at,updated_at
               FROM project_work_tasks WHERE workspace_project_id=ANY(%s) AND COALESCE(archived,0)=0
               ORDER BY COALESCE(NULLIF(due_at,''),NULLIF(updated_at,'')) DESC,id DESC LIMIT 2000""",
            (ids,),
        )
        selected = _rank(rows, _terms(question), ("task_code", "title", "description", "assignee_name", "priority", "status", "source_module", "source_type", "source_code", "source_title"), limit=35)
        if selected:
            lines.append("### NHIỆM VỤ / GIAO VIỆC LIVE")
            for row in selected:
                lines.append(
                    f"[WORK-TASK:{row.get('id','')}] {row.get('task_code','')} | {_clip(row.get('title'),280)} | "
                    f"trạng thái={row.get('status','')} | tiến độ={int(row.get('progress_percent') or 0)}% | ưu tiên={row.get('priority','')} | "
                    f"phụ trách={_clip(row.get('assignee_name'),140)} | bắt đầu={row.get('start_at','')} | hạn={row.get('due_at','')} | "
                    f"nguồn={row.get('source_module','')}/{row.get('source_code','')} | mô tả={_clip(row.get('description'),360)}"
                )

    if _expand(counts.get("contracts", 0), contract_intent):
        rows = _fetch(
            connection,
            """SELECT id,workspace_project_id,record_type,record_no,title,signed_date,amount,currency,effective_date,
                      expiry_date,note,current_file_id,current_file_name,current_mime_type,current_file_size,
                      created_by_name,created_at,updated_by_name,updated_at
               FROM project_contract_records WHERE workspace_project_id=ANY(%s)
               ORDER BY COALESCE(NULLIF(signed_date,''),NULLIF(updated_at,''),NULLIF(created_at,'')) DESC,id DESC LIMIT 500""",
            (ids,),
        )
        selected = _rank(rows, _terms(question), ("record_type", "record_no", "title", "signed_date", "effective_date", "expiry_date", "note", "current_file_name"), limit=30)
        if selected:
            lines.append("### HỢP ĐỒNG / PHỤ LỤC LIVE")
            for row in selected:
                lines.append(
                    f"[CONTRACT:{row.get('id','')}] {row.get('record_type','')} {row.get('record_no','')} | {_clip(row.get('title'),280)} | "
                    f"ký={row.get('signed_date','')} | hiệu lực={row.get('effective_date','')} | hết hiệu lực={row.get('expiry_date','')} | "
                    f"giá trị={_money(row.get('amount'))} {row.get('currency','VND')} | file hiện hành={_clip(row.get('current_file_name'),220)} | "
                    f"ghi chú={_clip(row.get('note'),300)}"
                )

    if _expand(counts.get("approvals", 0), approval_intent):
        rows = _fetch(
            connection,
            """SELECT id,project_id,record_kind,subtype,record_id,record_code,overall_status,current_stage,
                      submitted_by,submitted_at,final_approved_at,updated_at,COALESCE(revision_no,0) AS revision_no,
                      COALESCE(return_stage,'') AS return_stage
               FROM approval_workflows WHERE project_id=ANY(%s) ORDER BY updated_at DESC,id DESC LIMIT 1000""",
            (ids,),
        )
        selected = _rank(rows, _terms(question), ("record_kind", "subtype", "record_code", "overall_status", "current_stage", "submitted_by", "return_stage"), limit=35)
        if selected:
            lines.append("### LUỒNG PHÊ DUYỆT LIVE")
            for row in selected:
                lines.append(
                    f"[APPROVAL:{row.get('id','')}] {row.get('record_kind','')}/{row.get('subtype','')} {row.get('record_code','')} | "
                    f"trạng thái={row.get('overall_status','')} | cấp hiện tại={row.get('current_stage','')} | "
                    f"revision={row.get('revision_no',0)} | trả về={row.get('return_stage','')} | trình={row.get('submitted_at','')} | "
                    f"duyệt cuối={row.get('final_approved_at','')}"
                )
            workflow_ids = [int(row.get("id") or 0) for row in selected if int(row.get("id") or 0) > 0]
            if workflow_ids and _table_exists(connection, "approval_steps"):
                steps = _fetch(
                    connection,
                    """SELECT workflow_id,stage_code,stage_order,stage_label,approver_name,status,comment,acted_by,acted_at
                       FROM approval_steps WHERE workflow_id=ANY(%s) ORDER BY workflow_id,stage_order""",
                    (workflow_ids,),
                )
                for row in steps[:120]:
                    lines.append(
                        f"[APPROVAL-STEP:{row.get('workflow_id','')}] {row.get('stage_label','')} | trạng thái={row.get('status','')} | "
                        f"người duyệt={_clip(row.get('approver_name'),120)} | xử lý={row.get('acted_at','')} | ý kiến={_clip(row.get('comment'),220)}"
                    )
    return lines


def build_live_domain_context(
    connection,
    workspace_ids: Sequence[int],
    question: str,
    *,
    max_chars: int = 22000,
) -> str:
    """Return detailed rows for every operational domain in the resolved scope."""
    ids = [int(value) for value in workspace_ids if int(value or 0) > 0]
    if not ids:
        return ""
    counts = _domain_counts(connection, ids)
    coverage = " | ".join(f"{name}={value}" for name, value in counts.items())
    lines = [
        "## CHI TIẾT NGHIỆP VỤ LIVE – CÙNG PHẠM VI WORKSPACE",
        f"[DOMAIN-COVERAGE] {coverage}",
        "Các dòng dưới đây là dữ liệu chi tiết đứng sau các số đếm. Nếu có dòng chi tiết phù hợp, không được kết luận rằng hệ thống chỉ có số tổng hợp hoặc thiếu nội dung.",
    ]
    lines.extend(_document_context(connection, ids, question, counts.get("documents", 0)))
    lines.extend(_drawing_context(connection, ids, question, counts.get("drawings", 0)))
    lines.extend(_finance_context(connection, ids, question, counts))
    lines.extend(_material_context(connection, ids, question, counts))
    lines.extend(_work_contract_approval_context(connection, ids, question, counts))
    value = "\n".join(lines)
    cap = max(6000, min(int(max_chars), 30000))
    return value[:cap]


__all__ = ["build_live_domain_context"]
