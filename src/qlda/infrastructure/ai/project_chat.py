from __future__ import annotations

"""Authoritative project-chat context built from live PostgreSQL data.

The native assistant reads deterministic business data and Contractor Data Hub
evidence directly from PostgreSQL.  During the production-data migration window,
``production_progress_current`` remains an authoritative live store for projects
whose normalized ``contractor_data_records`` have not been populated yet.  The
compatibility read below is data-only; this module never imports ``runtime_core``.
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
_CORE_TABLES = (
    "tasks",
    "documents",
    "drawings",
    "cost_budgets",
    "payment_tracking",
    "cost_variations",
    "material_master",
    "procurement_schedule",
    "inventory_inspection",
    # The production UI has historically stored synchronized Google-Sheet rows
    # here.  It is still live business data while Data Hub normalization rolls out.
    "production_progress_current",
)


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


def _project_scope_ids(master: int, labels: dict[int, str]) -> list[int]:
    """Return the complete management-visible project scope, including master data."""
    ids: list[int] = []
    for raw in (int(master), *labels.keys()):
        value = int(raw or 0)
        if value > 0 and value not in ids:
            ids.append(value)
    return ids or [int(master)]


def _scope_has_live_data(connection, master: int, workspace_ids: Sequence[int]) -> bool:
    """Cheap existence probe used only to recover an empty management workspace."""
    ids = [int(value) for value in workspace_ids if int(value or 0) > 0]
    if not ids:
        return False
    for table in _CORE_TABLES:
        if not _table_exists(connection, table):
            continue
        count = int(
            _scalar(
                connection,
                f"SELECT COUNT(*) FROM {table} WHERE project_id=ANY(%s)",
                (ids,),
                0,
            )
            or 0
        )
        if count > 0:
            return True
    if _table_exists(connection, "contractor_data_records"):
        count = int(
            _scalar(
                connection,
                """SELECT COUNT(*) FROM contractor_data_records
                   WHERE master_project_id=%s AND workspace_project_id=ANY(%s)""",
                (int(master), ids),
                0,
            )
            or 0
        )
        if count > 0:
            return True
    return False


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

    labels.setdefault(int(master), "Dự án tổng")
    all_ids = _project_scope_ids(master, labels)
    explicit_project_wide = bool(allow_project_wide and project_wide_intent(question))
    if explicit_project_wide:
        return master, all_ids, labels, True

    # Management-safe recovery only.  Contractor identities always call this with
    # allow_project_wide=False, so this branch can never cross a contractor tenant.
    if allow_project_wide and len(all_ids) > 1:
        selected_has_data = _scope_has_live_data(connection, master, [selected])
        if not selected_has_data and _scope_has_live_data(connection, master, all_ids):
            return master, all_ids, labels, True

    return master, [selected], labels, False


def _task_detail_context(
    connection,
    workspace_ids: list[int],
    labels: dict[int, str],
    question: str,
    total_tasks: int,
) -> list[str]:
    if total_tasks <= 0 or not _table_exists(connection, "tasks"):
        return []
    ids = [int(x) for x in workspace_ids if int(x) > 0]
    terms = _question_terms(question)
    qnorm = _norm(question)
    generic_schedule_intent = any(
        phrase in qnorm
        for phrase in (
            "tien do",
            "cong viec",
            "ke hoach",
            "cham tien do",
            "critical",
            "milestone",
        )
    )
    try:
        rows = connection.execute(
            """SELECT id,project_id,wbs,name,responsible,start_date,end_date,duration,
                      planned_progress,actual_progress,actual_override,actual_update_date,
                      actual_finish_date,status,predecessor,note,critical,total_slack,
                      resource_names,source_type
               FROM tasks WHERE project_id=ANY(%s)
               ORDER BY critical DESC,id DESC LIMIT 6000""",
            (ids,),
        ).fetchall()
    except Exception:
        try:
            rows = connection.execute(
                """SELECT id,project_id,wbs,name,responsible,start_date,end_date,duration,
                          planned_progress,actual_progress,status,predecessor,note,critical,
                          total_slack,resource_names,source_type
                   FROM tasks WHERE project_id=ANY(%s)
                   ORDER BY critical DESC,id DESC LIMIT 6000""",
                (ids,),
            ).fetchall()
        except Exception:
            rows = []

    scored: list[tuple[int, dict[str, Any]]] = []
    all_rows: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        all_rows.append(row)
        haystack = _norm(
            " ".join(
                str(row.get(key) or "")
                for key in (
                    "wbs", "name", "responsible", "status", "note",
                    "resource_names", "source_type",
                )
            )
        )
        score = sum(1 for term in terms if term in haystack)
        if score > 0:
            scored.append((score, row))

    scored.sort(
        key=lambda item: (
            item[0],
            int(item[1].get("critical") or 0),
            int(item[1].get("id") or 0),
        ),
        reverse=True,
    )
    if scored:
        selected = [row for _, row in scored[:40]]
        lines = ["### CÔNG VIỆC LIVE PHÙ HỢP CÂU HỎI"]
    elif generic_schedule_intent:
        selected = all_rows[:40]
        lines = ["### CÔNG VIỆC LIVE ƯU TIÊN"]
    else:
        return [
            f"[TASK-SEARCH] Có {total_tasks} công việc trong phạm vi nhưng chưa có dòng khớp trực tiếp từ khóa câu hỏi."
        ]

    for row in selected:
        wid = int(row.get("project_id") or 0)
        override = row.get("actual_override")
        actual = row.get("actual_progress") if override is None else override
        lines.append(
            f"[TASK:{row.get('id','')}] {labels.get(wid, f'Workspace {wid}')} | "
            f"WBS={row.get('wbs','')} | {row.get('name','')} | "
            f"KH={float(row.get('planned_progress') or 0):.1f}% | TT={float(actual or 0):.1f}% | "
            f"trạng thái={row.get('status','')} | {row.get('start_date','')} -> {row.get('end_date','')} | "
            f"phụ trách={row.get('responsible','')} | critical={int(row.get('critical') or 0)} | "
            f"ghi chú={str(row.get('note') or '')[:240]}"
        )
    if len(scored) > len(selected):
        lines.append(
            f"[TASK-SEARCH] Còn {len(scored) - len(selected)} công việc khớp chưa đưa nguyên văn vào context do giới hạn prompt."
        )
    return lines


def _core_context(
    connection,
    master: int,
    workspace_ids: list[int],
    labels: dict[int, str],
    question: str,
) -> list[str]:
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
        return int(
            _scalar(
                connection,
                f"SELECT COUNT(*) FROM {table} WHERE project_id=ANY(%s)",
                (ids,),
                0,
            )
            or 0
        )

    tasks = count("tasks")
    documents = count("documents")
    drawings = count("drawings")
    boq_rows = count("cost_budgets")
    materials = count("material_master")
    procurements = count("procurement_schedule")
    inventory = count("inventory_inspection")
    legacy_production = count("production_progress_current")
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
        f"[LIVE-SUMMARY] vật tư={materials} | mua sắm={procurements} | nhập/xuất/kiểm định={inventory} | dòng sản lượng live={legacy_production}",
    ]
    lines.extend(_task_detail_context(connection, ids, labels, question, tasks))

    if _table_exists(connection, "project_contractors") and len(ids) > 1:
        lines.append("### PHẠM VI NHÀ THẦU")
        for wid in ids:
            lines.append(f"[WORKSPACE:{wid}] {labels.get(wid, f'Workspace {wid}')}")
    return lines


def _legacy_production_rows(connection, workspace_ids: Sequence[int]) -> list[dict[str, Any]]:
    """Read the live pre-Data-Hub production store without importing legacy code.

    This table is populated by the production-progress synchronization used by the
    app UI.  It is tenant-safe because every query is constrained by ``project_id``
    to the already-resolved workspace ids.
    """
    ids = [int(x) for x in workspace_ids if int(x or 0) > 0]
    if not ids or not _table_exists(connection, "production_progress_current"):
        return []
    try:
        if _table_exists(connection, "production_sheet_sources"):
            rows = connection.execute(
                """SELECT c.project_id,c.source_id,c.worksheet,c.work_item,c.zone,
                          c.progress_percent,c.source_row,c.synced_at,
                          COALESCE(s.name,'') AS source_name,
                          COALESCE(s.spreadsheet_title,'') AS spreadsheet_title
                   FROM production_progress_current c
                   JOIN production_sheet_sources s
                     ON s.source_id=c.source_id AND s.project_id=c.project_id
                   WHERE c.project_id=ANY(%s) AND COALESCE(s.enabled,1)<>0
                   ORDER BY c.synced_at DESC,c.source_row DESC LIMIT 8000""",
                (ids,),
            ).fetchall()
        else:
            rows = connection.execute(
                """SELECT project_id,source_id,worksheet,work_item,zone,
                          progress_percent,source_row,synced_at,
                          '' AS source_name,'' AS spreadsheet_title
                   FROM production_progress_current
                   WHERE project_id=ANY(%s)
                   ORDER BY synced_at DESC,source_row DESC LIMIT 8000""",
                (ids,),
            ).fetchall()
    except Exception:
        return []
    return [dict(row) for row in rows]


def _legacy_production_context(
    connection,
    workspace_ids: list[int],
    labels: dict[int, str],
    question: str,
) -> list[str]:
    rows = _legacy_production_rows(connection, workspace_ids)
    if not rows:
        return []

    progress_values = [
        float(row.get("progress_percent") or 0)
        for row in rows
        if row.get("progress_percent") is not None
    ]
    source_ids = {str(row.get("source_id") or "") for row in rows if str(row.get("source_id") or "")}
    worksheets = {str(row.get("worksheet") or "") for row in rows if str(row.get("worksheet") or "")}
    last_sync = max((str(row.get("synced_at") or "") for row in rows), default="")
    avg = sum(progress_values) / len(progress_values) if progress_values else 0.0

    lines = [
        "## CONTRACTOR DATA HUB LIVE",
        (
            f"[DATA-HUB-SUMMARY] records={len(rows)} | nguồn={len(source_ids)} | "
            f"worksheet={len(worksheets)} | điểm sản lượng={len(progress_values)} | "
            f"sản lượng TB={avg:.1f}% | sync gần nhất={last_sync} | "
            "nguồn lưu=production_progress_current"
        ),
        "[DATA-HUB-COMPAT] Data Hub chuẩn chưa có record; đang dùng dữ liệu sản lượng live đã đồng bộ của chính workspace này.",
    ]

    terms = _question_terms(question)
    scored: list[tuple[int, dict[str, Any]]] = []
    for row in rows:
        haystack = _norm(
            " ".join(
                str(row.get(key) or "")
                for key in (
                    "source_name", "spreadsheet_title", "worksheet", "work_item", "zone"
                )
            )
        )
        score = sum(1 for term in terms if term in haystack)
        if score > 0:
            scored.append((score, row))
    scored.sort(
        key=lambda item: (item[0], int(item[1].get("source_row") or 0)),
        reverse=True,
    )

    if scored:
        lines.append("### DATA HUB – DÒNG SẢN LƯỢNG PHÙ HỢP CÂU HỎI")
        chosen = [row for _, row in scored[:80]]
    else:
        lines.append("[DATA-HUB-SEARCH] Có dữ liệu sản lượng live nhưng chưa có dòng khớp trực tiếp từ khóa câu hỏi.")
        chosen = rows[:40] if not terms else []

    for row in chosen:
        wid = int(row.get("project_id") or 0)
        lines.append(
            f"[DATA-HUB-ROW] {labels.get(wid, f'Workspace {wid}')} | "
            f"worksheet={row.get('worksheet','')} | công tác={row.get('work_item','')} | "
            f"zone={row.get('zone','')} | tiến độ={float(row.get('progress_percent') or 0):.1f}% | "
            f"dòng={row.get('source_row','')} | nguồn={row.get('source_name','')} | "
            f"sync={row.get('synced_at','')}"
        )

    # Compact grouped evidence remains useful for questions without one exact row.
    groups: dict[tuple[int, str, str, str], list[float]] = {}
    for row in rows:
        key = (
            int(row.get("project_id") or 0),
            str(row.get("source_name") or ""),
            str(row.get("worksheet") or ""),
            str(row.get("zone") or ""),
        )
        groups.setdefault(key, []).append(float(row.get("progress_percent") or 0))
    lines.append("### DATA HUB – TỔNG QUAN NGUỒN / WORKSHEET")
    for (wid, source_name, worksheet, zone), values in list(groups.items())[:60]:
        group_avg = sum(values) / len(values) if values else 0.0
        lines.append(
            f"[DATA-HUB] {labels.get(wid, f'Workspace {wid}')} | worksheet={worksheet} | "
            f"zone={zone} | records={len(values)} | điểm sản lượng={len(values)} | "
            f"TB={group_avg:.1f}% | nguồn={source_name}"
        )
    return lines


def _data_hub_context(
    connection,
    master: int,
    workspace_ids: list[int],
    labels: dict[int, str],
    question: str,
) -> list[str]:
    ids = [int(x) for x in workspace_ids if int(x) > 0]
    if not ids:
        return []

    # First prefer the normalized Data Hub.  If it is not yet populated, read the
    # production store that the live Sản lượng UI already uses.  This fixes the
    # false records=0 state without widening tenant scope.
    if not _table_exists(connection, "contractor_data_records"):
        legacy = _legacy_production_context(connection, ids, labels, question)
        if legacy:
            return legacy
        return ["## CONTRACTOR DATA HUB", "[DATA-HUB] Chưa có dữ liệu Data Hub/sản lượng live trong workspace hiện tại."]

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
    if total <= 0:
        legacy = _legacy_production_context(connection, ids, labels, question)
        if legacy:
            return legacy
        return [
            "## CONTRACTOR DATA HUB LIVE",
            "[DATA-HUB-SUMMARY] records=0 | nguồn=0 | worksheet=0 | điểm sản lượng=0 | sản lượng TB=0.0%",
            "[DATA-HUB] Không có record trong đúng phạm vi workspace hiện tại.",
        ]

    lines = [
        "## CONTRACTOR DATA HUB LIVE",
        (
            f"[DATA-HUB-SUMMARY] records={total} | nguồn={int(summary.get('sources') or 0)} | "
            f"worksheet={int(summary.get('worksheets') or 0)} | điểm sản lượng={int(summary.get('production_points') or 0)} | "
            f"sản lượng TB={float(summary.get('avg_progress') or 0):.1f}% | sync gần nhất={summary.get('last_sync') or ''}"
        ),
    ]

    # Put query-relevant evidence before broad summaries so important rows survive
    # the context cap even on a large contractor warehouse.
    terms = _question_terms(question)
    scored: list[tuple[int, dict[str, Any]]] = []
    if terms:
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
        for raw in rows:
            row = dict(raw)
            haystack = _norm(
                " ".join(
                    str(row.get(key) or "")
                    for key in (
                        "source_name", "category", "worksheet", "record_type",
                        "work_item", "zone", "content",
                    )
                )
            )
            score = sum(1 for term in terms if term in haystack)
            if score:
                scored.append((score, row))
        scored.sort(
            key=lambda item: (item[0], int(item[1].get("source_row") or 0)),
            reverse=True,
        )
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
        if not scored:
            lines.append("[DATA-HUB-SEARCH] Data Hub có dữ liệu nhưng chưa có dòng khớp trực tiếp từ khóa câu hỏi.")

    try:
        groups = connection.execute(
            """SELECT workspace_project_id,source_name,worksheet,zone,COUNT(*) AS records,
                      SUM(CASE WHEN record_type='PRODUCTION' THEN 1 ELSE 0 END) AS production_points,
                      AVG(CASE WHEN record_type='PRODUCTION' THEN progress_percent ELSE NULL END) AS avg_progress,
                      MAX(synced_at) AS last_sync
               FROM contractor_data_records
               WHERE master_project_id=%s AND workspace_project_id=ANY(%s)
               GROUP BY workspace_project_id,source_name,worksheet,zone
               ORDER BY production_points DESC,records DESC LIMIT 60""",
            params,
        ).fetchall()
    except Exception:
        groups = []
    if groups:
        lines.append("### DATA HUB – TỔNG QUAN NGUỒN / WORKSHEET")
    for row in groups:
        wid = int(row.get("workspace_project_id") or 0)
        avg = row.get("avg_progress")
        avg_text = "—" if avg is None else f"{float(avg):.1f}%"
        lines.append(
            f"[DATA-HUB] {labels.get(wid, f'Workspace {wid}')} | worksheet={row.get('worksheet','')} | "
            f"zone={row.get('zone','')} | records={int(row.get('records') or 0)} | "
            f"điểm sản lượng={int(row.get('production_points') or 0)} | TB={avg_text} | nguồn={row.get('source_name','')}"
        )
    return lines


def build_live_project_context(
    project_id: int,
    question: str,
    *,
    workspace_scope: int | None = None,
    allow_project_wide: bool = False,
    max_chars: int = 32000,
) -> tuple[str, dict[str, Any]]:
    """Return deterministic live context and resolved scope metadata."""
    explicit_project_wide = bool(allow_project_wide and project_wide_intent(question))
    with connect() as connection:
        master, ids, labels, project_wide = _resolve_scope(
            connection,
            int(project_id),
            workspace_scope,
            str(question or ""),
            bool(allow_project_wide),
        )
        auto_scope_recovery = bool(project_wide and not explicit_project_wide)
        if auto_scope_recovery:
            scope_label = "TOÀN DỰ ÁN – TỰ KHÔI PHỤC VÌ WORKSPACE ĐANG CHỌN KHÔNG CÓ DỮ LIỆU"
        else:
            scope_label = "TOÀN DỰ ÁN" if project_wide else "WORKSPACE ĐANG CHỌN"
        lines = [
            "# NGỮ CẢNH QLDA LIVE – NGUỒN POSTGRESQL",
            f"Phạm vi={scope_label} | master_project_id={master} | workspace_ids={','.join(str(x) for x in ids)}",
        ]
        if auto_scope_recovery:
            lines.append(
                "[SCOPE-RECOVERY] Workspace được chọn không có dữ liệu live. Vì người dùng có quyền quản lý dự án, AI đã mở rộng CHỈ yêu cầu đọc này sang các workspace được phép trong cùng dự án để tránh kết luận sai rằng hệ thống không có dữ liệu."
            )
        lines.extend(_core_context(connection, master, ids, labels, str(question or "")))
        lines.extend(_data_hub_context(connection, master, ids, labels, str(question or "")))
    context = "\n".join(lines)
    cap = max(6000, min(int(max_chars), 50000))
    return context[:cap], {
        "master_project_id": master,
        "workspace_ids": ids,
        "project_wide": project_wide,
        "auto_scope_recovery": auto_scope_recovery,
        "scope_reason": (
            "empty_workspace_recovered"
            if auto_scope_recovery
            else "explicit_project_wide"
            if explicit_project_wide
            else "selected_workspace"
        ),
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
        int(project_id),
        str(question or ""),
        workspace_scope=workspace_scope,
        allow_project_wide=allow_project_wide,
    )
    effective_project = (
        int(scope.get("master_project_id") or project_id)
        if scope.get("project_wide")
        else int(project_id)
    )
    effective_tenant = (
        effective_project
        if scope.get("project_wide")
        else int(workspace_scope or project_id)
    )
    report_date = status_date.isoformat() if isinstance(status_date, date) else str(status_date or "")
    grounded = (
        f"{question.strip()}\n\n"
        f"NGÀY BÁO CÁO CHÍNH XÁC: {report_date or 'không chỉ định'}. Không được tự đổi năm/ngày.\n"
        "Dữ liệu LIVE dưới đây có độ ưu tiên cao hơn lịch sử hội thoại và mọi snapshot cũ.\n\n"
        f"{context}\n\n"
        "Chỉ kết luận từ các dòng LIVE ở trên. Nếu DATA HUB có dữ liệu thì không được kết luận 'hệ thống không có dữ liệu' chỉ vì bảng tasks bằng 0. "
        "Dòng [DATA-HUB-COMPAT] và [DATA-HUB-ROW] từ production_progress_current là dữ liệu sản lượng LIVE hợp lệ của đúng workspace, không được bỏ qua chỉ vì Data Hub chuẩn chưa normalize xong. "
        "Nếu có TASK phù hợp câu hỏi thì phải dùng các dòng TASK đó khi đánh giá tiến độ. "
        "Khi viện dẫn số liệu sản lượng, nêu rõ worksheet/zone/công tác hoặc nhãn DATA-HUB/TASK tương ứng. "
        "Nếu có [SCOPE-RECOVERY], phải nói rõ dữ liệu được tìm thấy sau khi mở rộng phạm vi quản lý hợp lệ; không được nói workspace ban đầu có dữ liệu."
    )
    source_refs = [f"workspace:{wid}" for wid in list(scope.get("workspace_ids") or [])]
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
        record_ai_event(
            {
                "workspace_project_id": effective_tenant,
                "event_type": "AI_PROJECT_CHAT_LIVE",
                "provider": provider,
                "input": question,
                "input_hash": content_hash(question),
                "context": scope,
                "source_refs": source_refs,
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "success": True,
            }
        )
        return result
    except AIProviderError:
        raise
    except Exception as exc:
        record_ai_event(
            {
                "workspace_project_id": effective_tenant,
                "event_type": "AI_PROJECT_CHAT_LIVE",
                "provider": provider,
                "input": question,
                "context": scope,
                "source_refs": source_refs,
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "success": False,
                "error_code": exc.__class__.__name__,
            }
        )
        raise


__all__ = ["ask_project_chat", "build_live_project_context", "project_wide_intent"]
