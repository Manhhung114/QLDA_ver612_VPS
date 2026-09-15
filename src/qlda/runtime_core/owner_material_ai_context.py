from __future__ import annotations

"""Shared-AI context for ERP vật tư do Chủ đầu tư cấp.

This module does not create a separate assistant. It appends live owner-supplied
material balances, plans, warehouse stock, transactions and scoped VPS evidence
to the existing ``ProjectContextBuilder`` used by Công cụ -> Trợ lý AI.
"""

import re
import unicodedata
from typing import Any

PATCH_MARKER = "V7.6 OWNER MATERIAL SHARED AI CONTEXT V1"
MAX_PLAN_ROWS = 180
MAX_LEDGER_ROWS = 220
MAX_FILE_CATALOG = 80
MAX_PDF_CONTEXT_CHARS = 120_000
MAX_SCAN_FILES = 4


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


def _text(value: Any) -> str:
    return str(value or "").strip()


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or 0)
    except Exception:
        return float(default)


def _fmt(value: Any) -> str:
    number = _float(value)
    if abs(number - round(number)) < 1e-9:
        return f"{number:,.0f}"
    return f"{number:,.3f}".rstrip("0").rstrip(".")


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", _text(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("đ", "d")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _tokens(question: str) -> list[str]:
    stop = {
        "cho", "toi", "minh", "cac", "cua", "va", "voi", "trong", "noi", "dung",
        "hay", "giup", "bao", "nhieu", "tong", "hop", "kiem", "tra", "phan", "tich",
    }
    out: list[str] = []
    for token in _norm(question).split():
        if len(token) >= 2 and token not in stop and token not in out:
            out.append(token)
    return out[:32]


def _material_intent(question: str) -> bool:
    q = _norm(question)
    terms = (
        "vat tu", "vat lieu", "cdt cap", "chu dau tu cap", "chu dau tu", "erp", "kho",
        "ban giao", "cap nha thau", "nha thau dang giu", "lap dat", "hao hut", "hu hong",
        "mat", "tra kho", "tra lai", "dieu chuyen", "boq", "vo", "quyet toan", "ton kho",
        "phieu cap", "phieu tra", "bien ban", "chung tu",
    )
    return any(term in q for term in terms)


def _document_intent(question: str) -> bool:
    q = _norm(question)
    return any(term in q for term in (
        "pdf", "file", "bien ban", "phieu", "chung tu", "ban giao", "doc", "tai lieu",
        "co cq", "kiem dinh", "nghiem thu",
    ))


def _table_exists(builder, connection, table: str) -> bool:
    try:
        return bool(builder.table_exists(connection, table))
    except Exception:
        try:
            connection.execute(f"SELECT 1 FROM {table} LIMIT 1")
            return True
        except Exception:
            return False


def _scope_workspaces(builder, project_id: int) -> list[dict[str, Any]]:
    """Resolve only workspaces the current shared-AI session may read."""
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
                    """SELECT pc.master_project_id,pc.workspace_project_id,pc.contractor_code,
                              pc.contractor_name,pc.contract_no,pc.package_name,p.code AS project_code,
                              p.name AS project_name
                       FROM project_contractors pc
                       JOIN projects p ON p.id=pc.workspace_project_id
                       WHERE pc.workspace_project_id=? LIMIT 1""",
                    (rid,),
                ).fetchone()
                if row:
                    return [_rowdict(row)]
            except Exception:
                pass
            try:
                p = connection.execute("SELECT id,code,name FROM projects WHERE id=? LIMIT 1", (rid,)).fetchone()
                d = _rowdict(p)
                if d:
                    return [{
                        "master_project_id": rid,
                        "workspace_project_id": rid,
                        "contractor_code": "",
                        "contractor_name": "",
                        "contract_no": "",
                        "package_name": "",
                        "project_code": _text(d.get("code")),
                        "project_name": _text(d.get("name")),
                    }]
            except Exception:
                return []
            return []

        master_id = pid
        try:
            from qlda.runtime_core.contractor_workspace import resolve_master_project_id_connection
            master_id = int(resolve_master_project_id_connection(connection, pid) or pid)
        except Exception:
            pass

        rows: list[dict[str, Any]] = []
        try:
            raw = connection.execute(
                """SELECT pc.master_project_id,pc.workspace_project_id,pc.contractor_code,
                          pc.contractor_name,pc.contract_no,pc.package_name,p.code AS project_code,
                          p.name AS project_name
                   FROM project_contractors pc
                   JOIN projects p ON p.id=pc.workspace_project_id
                   WHERE pc.master_project_id=? AND pc.status='Đang hoạt động'
                   ORDER BY pc.is_default DESC,pc.id""",
                (master_id,),
            ).fetchall()
            rows = [_rowdict(x) for x in raw]
        except Exception:
            rows = []

        if rows:
            return rows

        try:
            p = connection.execute("SELECT id,code,name FROM projects WHERE id=? LIMIT 1", (pid,)).fetchone()
            d = _rowdict(p)
            if d:
                return [{
                    "master_project_id": pid,
                    "workspace_project_id": pid,
                    "contractor_code": "",
                    "contractor_name": "",
                    "contract_no": "",
                    "package_name": "",
                    "project_code": _text(d.get("code")),
                    "project_name": _text(d.get("name")),
                }]
        except Exception:
            pass
    return []


def _workspace_data(builder, workspace_id: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    from qlda.runtime_core import owner_supplied_materials as om

    with builder.connect() as connection:
        if not _table_exists(builder, connection, om.PLAN_TABLE):
            return [], [], []
        try:
            plans = [
                _rowdict(r) for r in connection.execute(
                    f"SELECT * FROM {om.PLAN_TABLE} WHERE workspace_project_id=? ORDER BY material_code,location,id",
                    (int(workspace_id),),
                ).fetchall()
            ]
        except Exception:
            plans = []
        try:
            ledger = [
                _rowdict(r) for r in connection.execute(
                    f"SELECT * FROM {om.LEDGER_TABLE} WHERE workspace_project_id=? AND voided=0 "
                    "ORDER BY txn_date DESC,id DESC",
                    (int(workspace_id),),
                ).fetchall()
            ] if _table_exists(builder, connection, om.LEDGER_TABLE) else []
        except Exception:
            ledger = []
        try:
            warehouses = [
                _rowdict(r) for r in connection.execute(
                    f"SELECT * FROM {om.WAREHOUSE_TABLE} WHERE workspace_project_id=? AND active=1 "
                    "ORDER BY warehouse_code,id",
                    (int(workspace_id),),
                ).fetchall()
            ] if _table_exists(builder, connection, om.WAREHOUSE_TABLE) else []
        except Exception:
            warehouses = []
    return plans, ledger, warehouses


def _summary_rows(plans: list[dict[str, Any]], ledger: list[dict[str, Any]]) -> list[dict[str, Any]]:
    from qlda.runtime_core import owner_supplied_materials as om

    agg = om._aggregate(ledger)
    stock_map = om.stock_by_warehouse(ledger)
    allowed: dict[str, float] = {}
    meta: dict[str, dict[str, str]] = {}

    for row in plans:
        code = _text(row.get("material_code")).upper()
        if not code:
            continue
        boq_vo = _float(row.get("boq_qty")) + _float(row.get("vo_qty"))
        planned = _float(row.get("planned_qty"))
        allowed[code] = allowed.get(code, 0.0) + max(0.0, boq_vo if boq_vo > 0 else planned)
        rec = meta.setdefault(code, {"name": "", "unit": ""})
        rec["name"] = rec["name"] or _text(row.get("material_name"))
        rec["unit"] = rec["unit"] or _text(row.get("unit"))

    for row in ledger:
        code = _text(row.get("material_code")).upper()
        if not code:
            continue
        rec = meta.setdefault(code, {"name": "", "unit": ""})
        rec["name"] = rec["name"] or _text(row.get("material_name"))
        rec["unit"] = rec["unit"] or _text(row.get("unit"))

    result: list[dict[str, Any]] = []
    for code in sorted(set(meta) | set(agg) | set(allowed)):
        a = agg.get(code, {})
        received = _float(a.get("received"))
        issued = _float(a.get("issued"))
        installed = _float(a.get("installed"))
        returned_stock = _float(a.get("returned_stock"))
        returned_owner = _float(a.get("returned_owner"))
        loss = _float(a.get("loss"))
        stock = received + returned_stock - issued - returned_owner
        holding = issued - installed - returned_stock - loss
        reconciliation = received - (stock + holding + installed + loss + returned_owner)
        limit = _float(allowed.get(code))
        result.append({
            "material_code": code,
            "material_name": meta.get(code, {}).get("name", ""),
            "unit": meta.get(code, {}).get("unit", ""),
            "allowed_qty": limit,
            "received": received,
            "issued": issued,
            "installed": installed,
            "holding": holding,
            "stock": stock,
            "loss": loss,
            "returned_owner": returned_owner,
            "reconciliation": reconciliation,
            "over_limit": max(0.0, issued - limit) if limit > 0 else 0.0,
            "warehouse_stock": {
                warehouse: qty for (warehouse, mat_code), qty in stock_map.items()
                if mat_code == code and abs(qty) > 1e-9
            },
        })
    return result


def _score_row(row: dict[str, Any], tokens: list[str]) -> int:
    if not tokens:
        return 0
    haystack = _norm(" ".join(_text(row.get(k)) for k in (
        "txn_code", "txn_type", "txn_date", "material_code", "material_name", "unit",
        "warehouse_from", "warehouse_to", "contractor_code", "contractor_name",
        "location", "document_no", "task_ref", "note",
    )))
    words = set(haystack.split())
    score = 0
    for token in tokens:
        if token in words:
            score += 4
        elif len(token) >= 4 and token in haystack:
            score += 2
    return score


def _owner_pdf_rows(project_codes: list[str]) -> list[dict[str, Any]]:
    if not project_codes:
        return []
    try:
        from qlda.runtime_core import local_vps_backend as local

        safe_codes = [local._safe_segment(code, "DU_AN") for code in project_codes if _text(code)]
        if not safe_codes:
            return []
        local.ensure_schema()
        placeholders = ",".join(["%s"] * len(safe_codes))
        with local._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""SELECT id,project_code,kind,subtype,record_code,name,mime_type,size,sha256,
                               storage_path,uploaded_by,created_at,modified_at
                        FROM qlda_local_files
                        WHERE project_code IN ({placeholders})
                          AND kind='owner_material' AND trashed=FALSE AND history=FALSE
                          AND (LOWER(name) LIKE '%%.pdf' OR LOWER(mime_type)='application/pdf')
                        ORDER BY modified_at DESC,created_at DESC""",
                    tuple(safe_codes),
                )
                return [dict(r) for r in cur.fetchall()]
    except Exception:
        return []


def _pdf_evidence(project_codes: list[str], question: str) -> str:
    rows = _owner_pdf_rows(project_codes)
    if not rows:
        return ""

    from qlda.runtime_core import ai_vps_pdf_fullscan as fullscan

    tokens = fullscan._tokens(question)
    document_intent = _document_intent(question)
    material_intent = _material_intent(question)

    catalog = [
        f"[ERP-CDT-FILE:{r.get('record_code','')}] {r.get('name','')} | "
        f"loại={r.get('subtype','')} | workspace={r.get('project_code','')} | "
        f"người tải={r.get('uploaded_by','')} | cập nhật={r.get('modified_at','')}"
        for r in rows[:MAX_FILE_CATALOG]
    ]
    lines = [
        "",
        "## CHỨNG TỪ PDF ERP VẬT TƯ CĐT CẤP TRÊN VPS",
        f"Có {len(rows):,} PDF thuộc phạm vi ERP vật tư CĐT cấp mà người dùng hiện tại được phép đọc.",
        *catalog,
    ]

    if not (document_intent or material_intent):
        return "\n".join(lines)

    used = sum(len(x) + 1 for x in lines)
    candidates: list[tuple[int, dict[str, Any], int, str]] = []
    scan_candidates: list[tuple[int, dict[str, Any]]] = []

    for row in rows:
        meta_score = int(fullscan._metadata_score(row, question, tokens))
        pages = fullscan._extract_pages(row)
        total_text = sum(len(_text(text)) for _, text in pages)
        if total_text < max(500, len(pages) * 120):
            if meta_score > 0 or (document_intent and len(rows) <= 4):
                scan_candidates.append((max(1, meta_score), row))
            continue
        for page_no, page_text in pages:
            for chunk in fullscan._chunks(page_text):
                score = meta_score + fullscan._score_text(chunk, tokens)
                if tokens and score <= 0:
                    continue
                candidates.append((score, row, int(page_no), chunk))

    candidates.sort(key=lambda item: item[0], reverse=True)
    added = 0
    for score, row, page_no, chunk in candidates:
        name = _text(row.get("name")) or "PDF"
        block = f"\n[ERP-CDT-PDF:{name}|p.{page_no}|score={score}] {chunk}"
        remain = MAX_PDF_CONTEXT_CHARS - used
        if remain <= 800:
            break
        if len(block) > remain:
            block = block[:remain]
        lines.append(block)
        used += len(block)
        added += 1
        if added >= 24:
            break

    if scan_candidates and used < MAX_PDF_CONTEXT_CHARS:
        try:
            from qlda.runtime_core import ai_vps_pdf_vision as vision
            scan_candidates.sort(key=lambda item: item[0], reverse=True)
            processed = 0
            for score, row in scan_candidates:
                if processed >= MAX_SCAN_FILES:
                    break
                text = vision._extract_with_provider(row)
                if not _text(text):
                    continue
                name = _text(row.get("name")) or "PDF scan"
                block = (
                    f"\n[ERP-CDT-PDF-VISION:{name}|match={score}] "
                    f"loại={row.get('subtype','')} | chứng từ={row.get('record_code','')}\n{text}"
                )
                remain = MAX_PDF_CONTEXT_CHARS - used
                if remain <= 800:
                    break
                if len(block) > remain:
                    block = block[:remain]
                lines.append(block)
                used += len(block)
                processed += 1
        except Exception as exc:
            lines.append(f"\nVision chứng từ ERP chưa hoàn tất ở lượt này: {exc}")

    return "\n".join(lines)


def _erp_appendix(builder, project_id: int, question: str) -> str:
    workspaces = _scope_workspaces(builder, int(project_id))
    if not workspaces:
        return ""

    lines = [
        "",
        "## ERP VẬT TƯ DO CHỦ ĐẦU TƯ CẤP — LIVE DATA",
        "Đây là dữ liệu LIVE của ERP vật tư CĐT cấp dùng chung trong Trợ lý AI, không phải AI riêng.",
        "QUY TẮC: phải giữ ranh giới từng nhà thầu/workspace. Không cộng chéo số liệu rồi gán cho một nhà thầu.",
        "Công thức kiểm soát: CĐT đã giao = tồn kho + nhà thầu đang giữ + đã lắp đặt + hao hụt/hư hỏng/mất + đã trả CĐT.",
        "Tồn kho được tính từ sổ giao dịch; không được suy ra từ số nhập tay ngoài ledger.",
    ]

    tokens = _tokens(question)
    wants_detail = _material_intent(question)
    project_codes: list[str] = []
    total_received = total_issued = total_installed = total_holding = total_stock = total_loss = total_returned = 0.0
    total_materials = 0
    total_txns = 0

    any_data = False
    for ws in workspaces:
        wid = int(ws.get("workspace_project_id") or 0)
        if wid <= 0:
            continue
        project_code = _text(ws.get("project_code"))
        if project_code and project_code not in project_codes:
            project_codes.append(project_code)

        plans, ledger, warehouses = _workspace_data(builder, wid)
        if not plans and not ledger and not warehouses:
            continue
        any_data = True
        summary = _summary_rows(plans, ledger)
        total_materials += len(summary)
        total_txns += len(ledger)

        ccode = _text(ws.get("contractor_code")) or f"WS-{wid}"
        cname = _text(ws.get("contractor_name")) or _text(ws.get("project_name"))
        lines += [
            "",
            f"### [ERP-CDT-CONTRACTOR:{ccode}] {cname} | workspace={wid} | HĐ={_text(ws.get('contract_no'))} | gói={_text(ws.get('package_name'))}",
            f"Số vật tư={len(summary):,} | kế hoạch={len(plans):,} dòng | giao dịch={len(ledger):,} | kho={len(warehouses):,}.",
        ]

        for rec in summary:
            total_received += rec["received"]
            total_issued += rec["issued"]
            total_installed += rec["installed"]
            total_holding += rec["holding"]
            total_stock += rec["stock"]
            total_loss += rec["loss"]
            total_returned += rec["returned_owner"]
            wh = "; ".join(f"{k}={_fmt(v)}" for k, v in sorted(rec["warehouse_stock"].items())) or "không có tồn"
            flags: list[str] = []
            if rec["over_limit"] > 1e-9:
                flags.append(f"CẤP VƯỢT={_fmt(rec['over_limit'])}")
            if rec["stock"] < -1e-9:
                flags.append(f"TỒN ÂM={_fmt(rec['stock'])}")
            if abs(rec["reconciliation"]) > 1e-6:
                flags.append(f"LỆCH={_fmt(rec['reconciliation'])}")
            flag_text = " | CẢNH BÁO: " + ", ".join(flags) if flags else ""
            lines.append(
                f"[ERP-CDT-MAT:{ccode}:{rec['material_code']}] {rec['material_name']} | ĐVT={rec['unit']} | "
                f"BOQ+VO/KH={_fmt(rec['allowed_qty'])} | CĐT giao={_fmt(rec['received'])} | "
                f"cấp NT={_fmt(rec['issued'])} | đã lắp={_fmt(rec['installed'])} | NT giữ={_fmt(rec['holding'])} | "
                f"tồn={_fmt(rec['stock'])} | hao hụt/mất={_fmt(rec['loss'])} | trả CĐT={_fmt(rec['returned_owner'])} | "
                f"tồn theo kho: {wh}{flag_text}"
            )

        if wants_detail:
            for p in plans[:MAX_PLAN_ROWS]:
                lines.append(
                    f"[ERP-CDT-PLAN:{ccode}:{p.get('id','')}] {p.get('material_code','')} - {p.get('material_name','')} | "
                    f"ĐVT={p.get('unit','')} | BOQ={_fmt(p.get('boq_qty'))} | VO={_fmt(p.get('vo_qty'))} | "
                    f"KH cấp={_fmt(p.get('planned_qty'))} | vị trí={p.get('location','')} | ghi chú={p.get('note','')}"
                )

            scored: list[tuple[int, dict[str, Any]]] = []
            for row in ledger:
                score = _score_row(row, tokens)
                if not tokens or score > 0:
                    scored.append((score, row))
            if tokens and not scored:
                scored = [(0, row) for row in ledger[:30]]
            scored.sort(key=lambda item: (item[0], _text(item[1].get("txn_date")), int(item[1].get("id") or 0)), reverse=True)
            for score, row in scored[:MAX_LEDGER_ROWS]:
                lines.append(
                    f"[ERP-CDT-TXN:{row.get('txn_code','')}] {row.get('txn_date','')} | "
                    f"{row.get('txn_type','')} | {row.get('material_code','')} - {row.get('material_name','')} | "
                    f"SL={_fmt(row.get('quantity'))} {row.get('unit','')} | "
                    f"kho đi={row.get('warehouse_from','')} | kho đến={row.get('warehouse_to','')} | "
                    f"vị trí={row.get('location','')} | chứng từ={row.get('document_no','')} | task={row.get('task_ref','')} | "
                    f"ghi chú={row.get('note','')} | người lập={row.get('created_by','')} | match={score}"
                )

    if not any_data:
        return "\n".join(lines + ["Chưa có dữ liệu ERP vật tư CĐT cấp trong phạm vi hiện tại."])

    lines += [
        "",
        "### [ERP-CDT-PROJECT-TOTAL] TỔNG PHẠM VI ĐƯỢC PHÉP ĐỌC",
        f"Vật tư={total_materials:,} | giao dịch={total_txns:,} | CĐT giao={_fmt(total_received)} | "
        f"cấp nhà thầu={_fmt(total_issued)} | đã lắp={_fmt(total_installed)} | "
        f"nhà thầu đang giữ={_fmt(total_holding)} | tồn kho={_fmt(total_stock)} | "
        f"hao hụt/mất={_fmt(total_loss)} | trả CĐT={_fmt(total_returned)}.",
    ]

    pdf = _pdf_evidence(project_codes, question)
    if pdf:
        lines.append(pdf)
    return "\n".join(lines)


def install_owner_material_ai_context() -> None:
    """Attach owner-supplied material ERP to the existing shared assistant."""
    import qlda.runtime_core.ai_service as ai_service

    cls = ai_service.ProjectContextBuilder
    if getattr(cls, "_qlda_owner_material_ai_context_installed", False):
        return

    original_build = cls.build

    def build_with_owner_materials(
        self,
        project_id: int,
        question: str = "",
        status_date=None,
        max_tasks: int = 80,
        max_docs: int = 70,
        max_drawings: int = 60,
        max_legal: int = 40,
    ) -> str:
        snapshot = original_build(
            self,
            project_id,
            question,
            status_date,
            max_tasks=max_tasks,
            max_docs=max_docs,
            max_drawings=max_drawings,
            max_legal=max_legal,
        )
        try:
            appendix = _erp_appendix(self, int(project_id), str(question or ""))
        except Exception as exc:
            appendix = f"\n## ERP VẬT TƯ CĐT CẤP\nKhông đọc được dữ liệu ERP live ở lượt này: {exc}"
        return snapshot.rstrip() + "\n" + appendix + "\n"

    cls.build = build_with_owner_materials
    cls._qlda_owner_material_ai_context_installed = True
    cls._qlda_owner_material_ai_context_marker = PATCH_MARKER


__all__ = ["install_owner_material_ai_context"]
