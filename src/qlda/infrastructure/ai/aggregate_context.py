from __future__ import annotations

"""Authoritative aggregate context for exhaustive/statistical project-chat queries.

Relevance retrieval (RAG/top-k/keyword ranking) is useful for finding details, but it
must never be used as the source of truth for totals.  This module runs deterministic
SQL aggregates against the already-authorized workspace scope and, for document
subtypes, exposes every matching row up to a generous prompt-safe detail cap.
"""

import re
import unicodedata
from typing import Any, Sequence


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

# Order matters: specific phrases must be resolved before generic document words.
_DOC_TYPE_PHRASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("BBHT", ("bien ban hien truong", "bbht")),
    ("BBHOP", ("bien ban hop", "bbhop", "cuoc hop")),
    ("NTCV", ("nghiem thu cong viec", "ntcv")),
    ("NTVL", ("nghiem thu vat lieu dau vao", "nghiem thu vat lieu", "ntvl")),
    ("KDVT", ("kiem dinh vat tu", "kiem dinh vat lieu", "kdvt")),
    ("NKCT", ("nhat ky cong truong", "nhat ky thi cong", "nkct")),
    ("NCR", ("ncr", "khong phu hop")),
    ("RFI", ("rfi", "yeu cau thong tin")),
    ("RFA", ("rfa", "yeu cau phe duyet")),
)

_EXHAUSTIVE_PHRASES = (
    "thong ke",
    "tat ca",
    "toan bo",
    "liet ke",
    "danh sach",
    "bao nhieu",
    "tong so",
    "dem",
    "count",
    "how many",
    "all records",
)

_DOMAIN_SPECS: dict[str, tuple[str, str, str, tuple[str, ...]]] = {
    "tasks": ("tasks", "project_id", "Công việc/tiến độ", ("cong viec", "tien do", "task", "wbs")),
    "documents": (
        "documents",
        "project_id",
        "Hồ sơ/văn bản",
        ("ho so", "van ban", "bien ban", "rfi", "rfa", "ncr", "nghiem thu", "kiem dinh", "nhat ky"),
    ),
    "drawings": ("drawings", "project_id", "Bản vẽ", ("ban ve", "drawing", "shopdrawing", "hoan cong", "as built")),
    "boq": ("cost_budgets", "project_id", "BOQ/ngân sách", ("boq", "khoi luong", "du toan", "ngan sach")),
    "payments": ("payment_tracking", "project_id", "Thanh toán", ("thanh toan", "giai ngan", "payment")),
    "ipc": ("payment_claims", "project_id", "IPC/hồ sơ thanh toán", ("ipc", "claim", "ho so thanh toan")),
    "vo": ("cost_variations", "project_id", "Phát sinh/VO", ("vo", "phat sinh", "variation")),
    "vo_excel": ("variation_orders", "project_id", "VO Excel", ("vo excel", "variation order")),
    "materials": ("material_master", "project_id", "Vật tư/thiết bị", ("vat tu", "vat lieu", "thiet bi", "material")),
    "procurement": ("procurement_schedule", "project_id", "Mua sắm", ("mua sam", "dat hang", "giao hang", "procurement")),
    "inventory": ("inventory_inspection", "project_id", "Kho/kiểm định", ("nhap kho", "xuat kho", "ton kho", "kiem dinh", "inventory")),
    "work_tasks": ("project_work_tasks", "workspace_project_id", "Nhiệm vụ/giao việc", ("nhiem vu", "giao viec", "viec duoc giao")),
    "contracts": ("project_contract_records", "workspace_project_id", "Hợp đồng/phụ lục", ("hop dong", "phu luc", "contract")),
    "approvals": ("approval_workflows", "project_id", "Luồng phê duyệt", ("phe duyet", "duyet", "approval")),
    "owner_plans": ("owner_material_plans", "workspace_project_id", "Kế hoạch vật tư CĐT cấp", ("chu dau tu cap", "cdt cap", "vat tu cdt")),
    "owner_ledger": ("owner_material_ledger", "workspace_project_id", "Sổ vật tư CĐT cấp", ("ban giao vat tu", "hao hut", "so vat tu")),
    "cost_snapshots": ("project_cost_control_snapshots", "workspace_project_id", "Kiểm soát chi phí", ("chi phi", "evm", "actual cost")),
    "production": ("production_progress_current", "project_id", "Sản lượng live", ("san luong", "production")),
}


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("đ", "d")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def is_exhaustive_intent(question: str) -> bool:
    qnorm = _norm(question)
    return any(_norm(phrase) in qnorm for phrase in _EXHAUSTIVE_PHRASES)


def document_type_intent(question: str) -> str | None:
    qnorm = _norm(question)
    for doc_type, phrases in _DOC_TYPE_PHRASES:
        if any(_norm(phrase) in qnorm for phrase in phrases):
            return doc_type
    return None


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


def _domain_requested(question: str, phrases: Sequence[str]) -> bool:
    qnorm = _norm(question)
    return any(_norm(phrase) in qnorm for phrase in phrases)


def _clip(value: Any, limit: int = 320) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text if len(text) <= limit else text[: max(1, limit - 3)] + "..."


def _document_type_counts(connection, ids: Sequence[int]) -> dict[str, int]:
    if not _table_exists(connection, "documents"):
        return {}
    try:
        rows = connection.execute(
            """SELECT UPPER(COALESCE(doc_type,'')) AS doc_type, COUNT(*) AS n
               FROM documents WHERE project_id=ANY(%s)
               GROUP BY UPPER(COALESCE(doc_type,''))""",
            ([int(x) for x in ids],),
        ).fetchall()
        return {
            str(_rowdict(row).get("doc_type") or "").upper(): int(_rowdict(row).get("n") or 0)
            for row in rows
        }
    except Exception:
        return {}


def _document_rows(
    connection,
    ids: Sequence[int],
    doc_type: str | None,
    *,
    limit: int,
) -> list[dict[str, Any]]:
    if not _table_exists(connection, "documents"):
        return []
    try:
        if doc_type:
            rows = connection.execute(
                """SELECT id,project_id,doc_type,code,subject,discipline,contractor,issuer,assignee,
                          issue_date,status,priority,related_wbs,description,response,note,cost_impact,time_impact_days
                   FROM documents
                   WHERE project_id=ANY(%s) AND UPPER(COALESCE(doc_type,''))=%s
                   ORDER BY id DESC LIMIT %s""",
                ([int(x) for x in ids], str(doc_type).upper(), int(limit)),
            ).fetchall()
        else:
            rows = connection.execute(
                """SELECT id,project_id,doc_type,code,subject,discipline,contractor,issuer,assignee,
                          issue_date,status,priority,related_wbs,description,response,note,cost_impact,time_impact_days
                   FROM documents WHERE project_id=ANY(%s)
                   ORDER BY id DESC LIMIT %s""",
                ([int(x) for x in ids], int(limit)),
            ).fetchall()
        return [_rowdict(row) for row in rows]
    except Exception:
        return []


def build_authoritative_aggregate_context(
    connection,
    workspace_ids: Sequence[int],
    question: str,
    *,
    detail_limit: int = 120,
    max_chars: int = 18000,
) -> str:
    """Return deterministic exact totals for exhaustive/statistical questions.

    Exact totals are computed from SQL COUNT/GROUP BY over the authorized scope.
    Detail rows are supplementary and must never be used to infer a total.
    """
    ids = [int(value) for value in workspace_ids if int(value or 0) > 0]
    if not ids or not is_exhaustive_intent(question):
        return ""

    targeted = {
        name
        for name, (_table, _scope, _label, phrases) in _DOMAIN_SPECS.items()
        if _domain_requested(question, phrases)
    }
    doc_type = document_type_intent(question)
    if doc_type:
        targeted.add("documents")

    # A generic "thống kê tất cả/toàn bộ" without a named domain is treated as a
    # project-wide inventory of exact sheet counts.  Otherwise keep the prompt tight.
    selected_names = list(_DOMAIN_SPECS) if not targeted else [name for name in _DOMAIN_SPECS if name in targeted]

    lines = [
        "## THỐNG KÊ CHÍNH XÁC TỪ DATABASE – CÙNG PHẠM VI WORKSPACE",
        f"[AGGREGATE-AUTHORITY] workspace={','.join(str(x) for x in ids)} | chế độ=EXACT-SQL",
        "Các số [EXACT-*] dưới đây là tổng chính xác. Tuyệt đối không suy tổng từ số dòng chi tiết/top-k/RAG xuất hiện trong prompt.",
    ]

    for name in selected_names:
        table, scope_column, label, _phrases = _DOMAIN_SPECS[name]
        total = _count(connection, table, scope_column, ids)
        lines.append(f"[EXACT-DOMAIN] {name}={total} | {label}")

    if "documents" in selected_names:
        type_counts = _document_type_counts(connection, ids)
        ordered_types = list(_DOC_LABELS)
        extras = sorted(key for key in type_counts if key and key not in _DOC_LABELS)
        for dtype in [*ordered_types, *extras]:
            if dtype in type_counts or dtype == doc_type:
                lines.append(
                    f"[EXACT-DOC-TYPE] {dtype}={int(type_counts.get(dtype, 0))} | {_DOC_LABELS.get(dtype, dtype)}"
                )

        exact_total = int(type_counts.get(str(doc_type or "").upper(), 0)) if doc_type else sum(type_counts.values())
        requested_label = _DOC_LABELS.get(str(doc_type or "").upper(), "Tất cả hồ sơ/văn bản")
        lines.append(
            f"[EXACT-DOC-SUMMARY] loại={str(doc_type or 'ALL').upper()} | {requested_label} | tổng bản ghi={exact_total}"
        )

        safe_limit = max(1, min(int(detail_limit), 500))
        rows = _document_rows(connection, ids, doc_type, limit=safe_limit)
        for row in rows:
            dtype = str(row.get("doc_type") or "").upper()
            lines.append(
                f"[EXACT-DOC:{row.get('id','')}] {_DOC_LABELS.get(dtype, dtype or 'Hồ sơ')} | "
                f"mã={row.get('code','')} | ngày={row.get('issue_date','')} | "
                f"tiêu đề={_clip(row.get('subject'),220)} | trạng thái={row.get('status','')} | "
                f"đơn vị={_clip(row.get('contractor'),120)} | người lập/phát hành={_clip(row.get('issuer'),120)} | "
                f"thành phần/phụ trách={_clip(row.get('assignee'),160)} | nội dung={_clip(row.get('description'),360)} | "
                f"kết luận/phản hồi={_clip(row.get('response'),320)} | ghi chú={_clip(row.get('note'),220)}"
            )
        if exact_total > len(rows):
            lines.append(
                f"[EXACT-DOC-DETAIL-LIMIT] đã đưa chi tiết={len(rows)}/{exact_total}; tổng chính xác vẫn là {exact_total}."
            )

    value = "\n".join(lines)
    cap = max(6000, min(int(max_chars), 24000))
    return value[:cap]


__all__ = [
    "build_authoritative_aggregate_context",
    "document_type_intent",
    "is_exhaustive_intent",
]
