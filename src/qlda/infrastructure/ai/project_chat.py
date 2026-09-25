from __future__ import annotations

"""Authoritative project-chat context built from live PostgreSQL data.

This module is intentionally native infrastructure: it has no ``runtime_core``
dependency. It combines deterministic operational summaries with Contractor Data
Hub evidence so the app-wide assistant does not depend on the legacy
``ProjectContextBuilder`` snapshot.
"""

import re
import time
import unicodedata
from datetime import date
from typing import Any, Sequence

from qlda.infrastructure.ai.provider_gateway import AIProviderError, NativeProviderGateway
from qlda.infrastructure.ai.telemetry import content_hash, record_ai_event
from qlda.infrastructure.postgres import connect


_PROJECT_WIDE_PHRASES = (
    "toan bo du an",
    "toan du an",
    "tong quan du an",
    "tong quan toan bo du an",
    "tong the du an",
    "tat ca nha thau",
    "cac nha thau",
)
_STOPWORDS = {
    "cho", "toi", "hay", "kiem", "tra", "du", "lieu", "cua", "va", "voi", "theo",
    "trong", "tren", "cac", "nhung", "bao", "nhieu", "tong", "hop", "duoc", "hien", "tai",
    "nha", "thau", "cong", "viec", "san", "luong", "tien", "do", "sheet", "worksheet",
    "danh", "gia", "hoan", "thanh", "lap", "dat", "he", "thong", "toan", "bo", "an",
}


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("đ", "d")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def project_wide_intent(question: str) -> bool:
    value = _norm(question)
    return any(phrase in value for phrase in _PROJECT_WIDE_PHRASES)


def _question_terms(question: str) -> list[str]:
    out: list[str] = []
    for token in _norm(question).split():
        if len(token) >= 2 and token not in _STOPWORDS and token not in out:
            out.append(token)
    return out[:16]


def _table_exists(connection, table: str) -> bool:
    try:
        row = connection.execute(
            """SELECT EXISTS(
                SELECT 1 FROM information_schema.tables
                WHERE table_schema=current_schema() AND table_name=%s
            ) AS ok""",
            (str(table),),
        ).fetchone()
        return bool(row and row.get("ok"))
    except Exception:
        return False


def _scalar(connection, sql: str, params: tuple[Any, ...], default: Any = 0) -> Any:
    try:
        row = connection.execute(sql, params).fetchone()
        if not row:
            return default
        value = next(iter(dict(row).values()))
        return default if value is None else value
    except Exception:
        return default


def _resolve_scope(
    connection,
    project_id: int,
    workspace_scope: int | None,
    question: str,
    allow_project_wide: bool,
) -> tuple[int, list[int], dict[int, str], bool]:
    selected = int(workspace_scope or project_id or 0)
    if selected <= 0:
        raise ValueError("AI cần project/workspace hợp lệ.")

    master = selected
    labels: dict[int, str] = {selected: f"Workspace {selected}"}
    if _table_exists(connection, "project_contractors"):
        try:
            row = connection.execute(
                "SELECT master_project_id FROM project_contractors WHERE workspace_project_id=%s LIMIT 1",
                (selected,),
            ).fetchone()
            if row:
                master = int(row.get("master_project_id") or selected)
            rows = connection.execute(
                """SELECT workspace_project_id,contractor_code,contractor_name,status
                   FROM project_contractors WHERE master_project_id=%s ORDER BY is_default DESC,id""",
                (master,),
            ).fetchall()
            for raw in rows:
                wid = int(raw.get("workspace_project_id") or 0)
                if wid <= 0:
                    continue
                label = f"{raw.get('contractor_code','')} - {raw.get('contractor_name','')}".strip(" -")
                labels[wid] = label or f"Workspace {wid}"
        except Exception:
            master = selected

    project_wide = bool(allow_project_wide and project_wide_intent(question))
    if project_wide:
        ids = [wid for wid in labels if wid > 0]
        if not ids:
            ids = [master]
        return master, list(dict.fromkeys(ids)), labels, True
    return master, [selected], labels, False


def _core_context(connection, master: int, workspace_ids: list[int], labels: dict[int, str]) -> list[str]:
    ids = [int(x) for x in workspace_ids if int(x) > 0]
    if not ids:
        return []
    lines = ["## DỮ LIỆU NGHIỆP VỤ LIVE"]
    try:
        project = connection.execute(
            "SELECT code,name,start_date,end_date FROM projects WHERE id=%s", (int(master),)
        ).fetchone()
        if project:
            lines.append(
                f"[PROJECT] {project.get('code','')} - {project.get('name','')} | "
                f"{project.get('start_date','')} -> {project.get('end_date','')}"
            )
    except Exception:
        pass

    def count(table: str) -> int:
        if not _table_exists(connection, table):
            return 0
        return int(_scalar(connection, f"SELECT COUNT(*) FROM {table} WHERE project_id=ANY(%s)", (ids,), 0) or 0)

    tasks = count("tasks")
    documents = count("documents")
    drawings = count("drawings")
    boq_rows = count("cost_budgets")
    materials = count("material_master")
    procurements = count("procurement_schedule")
    inventory = count("inventory_inspection")
    bac = float(_scalar(connection, "SELECT COALESCE(SUM(budget_total),0) FROM cost_budgets WHERE project_id=ANY(%s)", (ids,), 0) or 0) if _table_exists(connection, "cost_budgets") else 0.0
    paid = float(_scalar(connection, "SELECT COALESCE(SUM(paid_amount),0) FROM payment_tracking WHERE project_id=ANY(%s)", (ids,), 0) or 0) if _table_exists(connection, "payment_tracking") else 0.0
    vo = float(_scalar(connection, "SELECT COALESCE(SUM(approved_amount),0) FROM cost_variations WHERE project_id=ANY(%s)", (ids,), 0) or 0) if _table_exists(connection, "cost_variations") else 0.0

    planned = actual = done = critical = 0.0
    if tasks and _table_exists(connection, "tasks"):
        try:
            row = connection.execute(
                """SELECT AVG(COALESCE(planned_progress,0)) AS planned,
                          AVG(COALESCE(actual_progress,0)) AS actual,
                          SUM(CASE WHEN COALESCE(actual_progress,0)>=100 THEN 1 ELSE 0 END) AS done,
                          SUM(CASE WHEN COALESCE(critical,0)<>0 THEN 1 ELSE 0 END) AS critical
                   FROM tasks WHERE project_id=ANY(%s)""",
                (ids,),
            ).fetchone() or {}
            planned = float(row.get("planned") or 0)
            actual = float(row.get("actual") or 0)
            done = float(row.get("done") or 0)
            critical = float(row.get("critical") or 0)
        except Exception:
            pass

    lines += [
        f"[LIVE-SUMMARY] phạm vi workspace={','.join(str(x) for x in ids)} | công việc={tasks} | KH TB={planned:.1f}% | TT TB={actual:.1f}% | hoàn thành={int(done)} | critical={int(critical)}",
        f"[LIVE-SUMMARY] hồ sơ={documents} | bản vẽ={drawings} | BOQ={boq_rows} dòng | BAC={bac:,.0f} VND | đã thanh toán={paid:,.0f} VND | VO duyệt={vo:,.0f} VND",
        f"[LIVE-SUMMARY] vật tư={materials} | mua sắm={procurements} | nhập/xuất/kiểm định={inventory}",
    ]

    if _table_exists(connection, "project_contractors") and len(ids) > 1:
        lines.append("### PHẠM VI NHÀ THẦU")
        for wid in ids:
            lines.append(f"[WORKSPACE:{wid}] {labels.get(wid, f'Workspace {wid}')}")
    return lines


def _data_hub_context(
    connection,
    master: int,
    workspace_ids: list[int],
    labels: dict[int, str],
    question: str,
) -> list[str]:
    if not _table_exists(connection, "contractor_data_records"):
        return ["## CONTRACTOR DATA HUB", "[DATA-HUB] Chưa có bảng contractor_data_records trong database."]
    ids = [int(x) for x in workspace_ids if int(x) > 0]
    if not ids:
        return []
    params = (int(master), ids)
    try:
        summary = connection.execute(
            """SELECT COUNT(*) AS records,COUNT(DISTINCT source_id) AS sources,
                      COUNT(DISTINCT worksheet) AS worksheets,
                      SUM(CASE WHEN record_type='PRODUCTION' THEN 1 ELSE 0 END) AS production_points,
                      AVG(CASE WHEN record_type='PRODUCTION' THEN progress_percent ELSE NULL END) AS avg_progress,
                      MAX(synced_at) AS last_sync
               FROM contractor_data_records
               WHERE master_project_id=%s AND workspace_project_id=ANY(%s)""",
            params,
        ).fetchone() or {}
    except Exception:
        summary = {}
    total = int(summary.get("records") or 0)
    lines = [
        "## CONTRACTOR DATA HUB LIVE",
        (
            f"[DATA-HUB-SUMMARY] records={total} | nguồn={int(summary.get('sources') or 0)} | "
            f"worksheet={int(summary.get('worksheets') or 0)} | điểm sản lượng={int(summary.get('production_points') or 0)} | "
            f"sản lượng TB={float(summary.get('avg_progress') or 0):.1f}% | sync gần nhất={summary.get('last_sync') or ''}"
        ),
    ]
    if total <= 0:
        lines.append("[DATA-HUB] Không có record trong đúng phạm vi workspace hiện tại.")
        return lines

    try:
        groups = connection.execute(
            """SELECT workspace_project_id,source_name,worksheet,zone,COUNT(*) AS records,
                      SUM(CASE WHEN record_type='PRODUCTION' THEN 1 ELSE 0 END) AS production_points,
                      AVG(CASE WHEN record_type='PRODUCTION' THEN progress_percent ELSE NULL END) AS avg_progress,
                      MAX(synced_at) AS last_sync
               FROM contractor_data_records
               WHERE master_project_id=%s AND workspace_project_id=ANY(%s)
               GROUP BY workspace_project_id,source_name,worksheet,zone
               ORDER BY production_points DESC,records DESC LIMIT 120""",
            params,
        ).fetchall()
    except Exception:
        groups = []
    for row in groups:
        wid = int(row.get("workspace_project_id") or 0)
        avg = row.get("avg_progress")
        avg_text = "—" if avg is None else f"{float(avg):.1f}%"
        lines.append(
            f"[DATA-HUB] {labels.get(wid, f'Workspace {wid}')} | worksheet={row.get('worksheet','')} | "
            f"zone={row.get('zone','')} | records={int(row.get('records') or 0)} | "
            f"điểm sản lượng={int(row.get('production_points') or 0)} | TB={avg_text} | nguồn={row.get('source_name','')}"
        )

    terms = _question_terms(question)
    if not terms:
        return lines
    try:
        rows = connection.execute(
            """SELECT workspace_project_id,source_name,category,worksheet,record_type,record_ref,
                      work_item,zone,progress_percent,content,source_row,synced_at
               FROM contractor_data_records
               WHERE master_project_id=%s AND workspace_project_id=ANY(%s)
               ORDER BY synced_at DESC,source_row DESC LIMIT 8000""",
            params,
        ).fetchall()
    except Exception:
        rows = []
    scored: list[tuple[int, dict[str, Any]]] = []
    for raw in rows:
        row = dict(raw)
        haystack = _norm(" ".join(str(row.get(key) or "") for key in (
            "source_name", "category", "worksheet", "record_type", "work_item", "zone", "content"
        )))
        score = sum(1 for term in terms if term in haystack)
        if score:
            scored.append((score, row))
    scored.sort(key=lambda item: (item[0], int(item[1].get("source_row") or 0)), reverse=True)
    if scored:
        lines.append("### DATA HUB – DÒNG PHÙ HỢP CÂU HỎI")
    for _, row in scored[:60]:
        wid = int(row.get("workspace_project_id") or 0)
        content = str(row.get("content") or "").replace("\n", " ").strip()
        if len(content) > 550:
            content = content[:547] + "..."
        progress = row.get("progress_percent")
        progress_text = "" if progress is None else f" | tiến độ={float(progress):.1f}%"
        lines.append(
            f"[DATA-HUB-ROW] {labels.get(wid, f'Workspace {wid}')} | worksheet={row.get('worksheet','')} | "
            f"công tác={row.get('work_item','')} | zone={row.get('zone','')}{progress_text} | "
            f"dòng={row.get('source_row','')} | {content}"
        )
    return lines


def build_live_project_context(
    project_id: int,
    question: str,
    *,
    workspace_scope: int | None = None,
    allow_project_wide: bool = False,
    max_chars: int = 24000,
) -> tuple[str, dict[str, Any]]:
    """Return deterministic live context and resolved scope metadata."""
    with connect() as connection:
        master, ids, labels, project_wide = _resolve_scope(
            connection, int(project_id), workspace_scope, str(question or ""), bool(allow_project_wide)
        )
        lines = [
            "# NGỮ CẢNH QLDA LIVE – NGUỒN POSTGRESQL",
            f"Phạm vi={'TOÀN DỰ ÁN' if project_wide else 'WORKSPACE ĐANG CHỌN'} | master_project_id={master} | workspace_ids={','.join(str(x) for x in ids)}",
        ]
        lines.extend(_core_context(connection, master, ids, labels))
        lines.extend(_data_hub_context(connection, master, ids, labels, str(question or "")))
    context = "\n".join(lines)
    return context[: max(4000, min(int(max_chars), 50000))], {
        "master_project_id": master,
        "workspace_ids": ids,
        "project_wide": project_wide,
    }


def ask_project_chat(
    project_id: int,
    question: str,
    *,
    provider: str,
    history: Sequence[dict[str, Any]] | None = None,
    status_date: date | None = None,
    use_web: bool | None = None,
    workspace_scope: int | None = None,
    allow_project_wide: bool = False,
) -> str:
    """Answer project chat from live PostgreSQL/Data Hub evidence with telemetry."""
    started = time.perf_counter()
    context, scope = build_live_project_context(
        int(project_id), str(question or ""), workspace_scope=workspace_scope,
        allow_project_wide=allow_project_wide,
    )
    effective_project = int(scope.get("master_project_id") or project_id) if scope.get("project_wide") else int(project_id)
    effective_tenant = effective_project if scope.get("project_wide") else int(workspace_scope or project_id)
    report_date = status_date.isoformat() if isinstance(status_date, date) else str(status_date or "")
    grounded = (
        f"{question.strip()}\n\n"
        f"NGÀY BÁO CÁO CHÍNH XÁC: {report_date or 'không chỉ định'}. Không được tự đổi năm/ngày.\n"
        "Dữ liệu LIVE dưới đây có độ ưu tiên cao hơn lịch sử hội thoại và mọi snapshot cũ.\n\n"
        f"{context}\n\n"
        "Chỉ kết luận từ các dòng LIVE ở trên. Nếu DATA HUB có dữ liệu thì không được kết luận 'hệ thống không có dữ liệu' chỉ vì bảng tasks bằng 0. "
        "Khi viện dẫn số liệu sản lượng, nêu rõ worksheet/zone/công tác hoặc nhãn DATA-HUB tương ứng."
    )
    source_refs = [
        f"workspace:{wid}" for wid in list(scope.get("workspace_ids") or [])
    ]
    try:
        result = NativeProviderGateway.run(
            str(provider or "openai").lower(),
            "ask_project",
            effective_tenant,
            effective_project,
            grounded,
            history=history,
            status_date=status_date,
            use_web=use_web,
        )
        record_ai_event({
            "workspace_project_id": effective_tenant,
            "event_type": "AI_PROJECT_CHAT_LIVE",
            "provider": provider,
            "input": question,
            "input_hash": content_hash(question),
            "context": scope,
            "source_refs": source_refs,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "success": True,
        })
        return result
    except AIProviderError:
        raise
    except Exception as exc:
        record_ai_event({
            "workspace_project_id": effective_tenant,
            "event_type": "AI_PROJECT_CHAT_LIVE",
            "provider": provider,
            "input": question,
            "context": scope,
            "source_refs": source_refs,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "success": False,
            "error_code": exc.__class__.__name__,
        })
        raise


__all__ = ["ask_project_chat", "build_live_project_context", "project_wide_intent"]
