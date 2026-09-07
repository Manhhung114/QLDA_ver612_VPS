from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Iterable

PATCH_MARKER = "V6.22 AI LIVE CONTEXT V1"
MAX_CONTEXT_ROWS = max(1000, min(50000, int(os.environ.get("QLDA_AI_CONTEXT_MAX_ROWS", "20000"))))


def _row_to_dict(row: Any) -> dict:
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    items = getattr(row, "items", None)
    if callable(items):
        try:
            return dict(items())
        except Exception:
            pass
    keys = getattr(row, "keys", None)
    if callable(keys):
        try:
            return {str(key): row[key] for key in keys()}
        except Exception:
            pass
    try:
        return dict(row)
    except Exception:
        return {}


def _rows_to_dicts_live(rows: Iterable[Any]) -> list[dict]:
    return [_row_to_dict(row) for row in rows]


def _scalar(connection, sql: str, params=(), default=0):
    try:
        row = connection.execute(sql, params).fetchone()
        if row is None:
            return default
        try:
            value = row[0]
        except Exception:
            data = _row_to_dict(row)
            value = next(iter(data.values())) if data else default
        return default if value is None else value
    except Exception:
        return default


def _fmt_money(value: Any) -> str:
    try:
        return f"{float(value or 0):,.0f}"
    except Exception:
        return "0"


def _live_project_appendix(builder, project_id: int) -> str:
    """Build an authoritative live section from the active project database."""
    try:
        import postgres_backend_v622 as pg
        backend = "PostgreSQL LIVE" if pg.resolve_database_url() else "SQLite LIVE"
    except Exception:
        backend = "SQLite LIVE"

    lines = [
        "",
        "## ĐỒNG BỘ LIVE – NGUỒN DỮ LIỆU HIỆN TẠI",
        f"Nguồn dữ liệu AI: {backend}. Snapshot này được đọc lại trực tiếp khi gửi mỗi câu hỏi AI; không dùng bản sao theo tài khoản/session.",
        "Nếu số liệu ở phần LIVE khác phần tóm tắt cũ phía trên, ưu tiên phần LIVE này.",
    ]

    with builder.connect() as c:
        def exists(table: str) -> bool:
            try:
                return bool(builder.table_exists(c, table))
            except Exception:
                return False

        counts: dict[str, int] = {}
        project_tables = (
            "tasks", "documents", "drawings", "cost_budgets", "payment_tracking",
            "cost_variations", "material_master", "procurement_schedule",
            "inventory_inspection", "approval_workflows",
        )
        for table in project_tables:
            if exists(table):
                counts[table] = int(_scalar(c, f"SELECT COUNT(*) FROM {table} WHERE project_id=?", (project_id,), 0) or 0)
            else:
                counts[table] = 0

        bac = _scalar(c, "SELECT COALESCE(SUM(budget_total),0) FROM cost_budgets WHERE project_id=?", (project_id,), 0) if exists("cost_budgets") else 0
        paid = _scalar(c, "SELECT COALESCE(SUM(paid_amount),0) FROM payment_tracking WHERE project_id=?", (project_id,), 0) if exists("payment_tracking") else 0
        vo = _scalar(c, "SELECT COALESCE(SUM(approved_amount),0) FROM cost_variations WHERE project_id=?", (project_id,), 0) if exists("cost_variations") else 0

        doc_files = 0
        drawing_files = 0
        recent_files: list[dict] = []
        if exists("document_attachments") and exists("documents"):
            doc_files = int(_scalar(
                c,
                "SELECT COUNT(*) FROM document_attachments a JOIN documents d ON d.id=a.document_id WHERE d.project_id=?",
                (project_id,), 0,
            ) or 0)
            try:
                recent_files += _rows_to_dicts_live(c.execute(
                    """SELECT a.id,a.file_name,a.storage_backend,a.created_at,d.doc_type AS kind,d.code AS record_code
                       FROM document_attachments a JOIN documents d ON d.id=a.document_id
                       WHERE d.project_id=? ORDER BY a.id DESC LIMIT 8""",
                    (project_id,),
                ).fetchall())
            except Exception:
                pass
        if exists("drawing_attachments") and exists("drawings"):
            drawing_files = int(_scalar(
                c,
                "SELECT COUNT(*) FROM drawing_attachments a JOIN drawings d ON d.id=a.drawing_id WHERE d.project_id=?",
                (project_id,), 0,
            ) or 0)
            try:
                recent_files += _rows_to_dicts_live(c.execute(
                    """SELECT a.id,a.file_name,a.storage_backend,a.created_at,d.drawing_type AS kind,d.drawing_no AS record_code
                       FROM drawing_attachments a JOIN drawings d ON d.id=a.drawing_id
                       WHERE d.project_id=? ORDER BY a.id DESC LIMIT 8""",
                    (project_id,),
                ).fetchall())
            except Exception:
                pass

        lines += [
            f"LIVE tiến độ: {counts['tasks']:,} công việc",
            f"LIVE hồ sơ: {counts['documents']:,} hồ sơ | bản vẽ: {counts['drawings']:,}",
            f"LIVE chi phí: {counts['cost_budgets']:,} dòng BOQ | BAC {_fmt_money(bac)} VND | đã thanh toán {_fmt_money(paid)} VND | VO duyệt {_fmt_money(vo)} VND",
            f"LIVE vật tư: {counts['material_master']:,} chủng loại | {counts['procurement_schedule']:,} kế hoạch mua sắm | {counts['inventory_inspection']:,} phiếu nhập/xuất/kiểm định",
            f"LIVE phê duyệt: {counts['approval_workflows']:,} workflow",
            f"LIVE file: {doc_files + drawing_files:,} file đính kèm ({doc_files:,} hồ sơ + {drawing_files:,} bản vẽ)",
        ]

        if exists("boq_excel_workbooks"):
            try:
                row = c.execute(
                    "SELECT filename,batch_id,updated_at FROM boq_excel_workbooks WHERE project_id=?",
                    (project_id,),
                ).fetchone()
                if row:
                    data = _row_to_dict(row)
                    lines.append(
                        f"[UPLOAD:BOQ] file={data.get('filename','')} | batch={data.get('batch_id','')} | cập nhật={data.get('updated_at','')}"
                    )
            except Exception:
                pass

        if exists("approval_workflows"):
            try:
                approvals = _rows_to_dicts_live(c.execute(
                    """SELECT id,record_kind,subtype,record_code,overall_status,current_stage,updated_at
                       FROM approval_workflows WHERE project_id=? ORDER BY id DESC LIMIT 12""",
                    (project_id,),
                ).fetchall())
                if approvals:
                    lines += ["", "### PHÊ DUYỆT CẬP NHẬT GẦN NHẤT"]
                    for row in approvals:
                        lines.append(
                            f"[APPROVAL:{row.get('id','')}] {row.get('record_kind','')}/{row.get('subtype','')} "
                            f"{row.get('record_code','')} | trạng thái={row.get('overall_status','')} | "
                            f"stage={row.get('current_stage','')} | cập nhật={row.get('updated_at','')}"
                        )
            except Exception:
                pass

        if exists("approval_history") and exists("approval_workflows"):
            try:
                history = _rows_to_dicts_live(c.execute(
                    """SELECT h.id,h.action,h.status,h.comment,h.actor_name,h.created_at,w.record_code,w.record_kind
                       FROM approval_history h JOIN approval_workflows w ON w.id=h.workflow_id
                       WHERE w.project_id=? ORDER BY h.id DESC LIMIT 12""",
                    (project_id,),
                ).fetchall())
                if history:
                    lines += ["", "### LỊCH SỬ PHÊ DUYỆT GẦN NHẤT"]
                    for row in history:
                        lines.append(
                            f"[APPROVAL-HISTORY:{row.get('id','')}] {row.get('record_kind','')} {row.get('record_code','')} | "
                            f"action={row.get('action','')} | status={row.get('status','')} | "
                            f"ý kiến={row.get('comment','')} | người={row.get('actor_name','')} | lúc={row.get('created_at','')}"
                        )
            except Exception:
                pass

        if recent_files:
            recent_files.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
            lines += ["", "### FILE ĐÍNH KÈM GẦN NHẤT"]
            for row in recent_files[:12]:
                lines.append(
                    f"[FILE:{row.get('id','')}] {row.get('kind','')} {row.get('record_code','')} | "
                    f"{row.get('file_name','')} | lưu={row.get('storage_backend','')} | tạo={row.get('created_at','')}"
                )

        recent_specs = (
            ("tasks", "id,name,actual_progress,actual_update_date,status", "COALESCE(actual_update_date,'') DESC,id DESC"),
            ("documents", "id,doc_type,code,subject,status,updated_at", "COALESCE(updated_at,'') DESC,id DESC"),
            ("drawings", "id,drawing_type,drawing_no,title,status,updated_at", "COALESCE(updated_at,'') DESC,id DESC"),
            ("cost_budgets", "id,boq_item,budget_total,updated_at", "COALESCE(updated_at,'') DESC,id DESC"),
            ("payment_tracking", "id,payment_code,paid_amount,payment_status,updated_at", "COALESCE(updated_at,'') DESC,id DESC"),
            ("cost_variations", "id,vo_code,approved_amount,status,updated_at", "COALESCE(updated_at,'') DESC,id DESC"),
            ("material_master", "id,material_code,material_name,updated_at", "COALESCE(updated_at,'') DESC,id DESC"),
            ("procurement_schedule", "id,material_code,supplier,status,updated_at", "COALESCE(updated_at,'') DESC,id DESC"),
            ("inventory_inspection", "id,slip_code,material_code,material_status,updated_at", "COALESCE(updated_at,'') DESC,id DESC"),
        )
        updates: list[str] = []
        for table, columns, order_by in recent_specs:
            if not exists(table):
                continue
            try:
                rows = _rows_to_dicts_live(c.execute(
                    f"SELECT {columns} FROM {table} WHERE project_id=? ORDER BY {order_by} LIMIT 3",
                    (project_id,),
                ).fetchall())
            except Exception:
                continue
            for row in rows:
                payload = " | ".join(f"{k}={v}" for k, v in row.items() if v not in (None, ""))
                updates.append(f"[LIVE:{table}] {payload}")
        if updates:
            lines += ["", "### CẬP NHẬT GẦN NHẤT TRÊN HỆ THỐNG"] + updates[:24]

    return "\n".join(lines)


def install_ai_live_context() -> None:
    """Make ProjectContextBuilder use the same active DB as the Streamlit UI.

    On VPS, the UI uses PostgreSQL via postgres_backend_v622 while the legacy AI
    builder historically opened DB_PATH as a local SQLite file. That split caused
    AI snapshots to show BAC/material/document counts as zero after users updated
    the real project. This hook routes AI reads to PostgreSQL and refreshes every
    prompt from live data.
    """
    import ai_service
    import postgres_backend_v622 as pg

    cls = ai_service.ProjectContextBuilder
    if getattr(cls, "_qlda_ai_live_context_installed", False):
        return

    original_connect = cls.connect
    original_table_exists = cls.table_exists
    original_build = cls.build

    def connect_live(self):
        url = pg.resolve_database_url()
        if url:
            return pg._PGConnectionContext(url)
        return original_connect(self)

    def table_exists_live(self, connection, table: str) -> bool:
        if connection.__class__.__name__ == "_CompatConnection":
            try:
                return bool(pg._table_exists(connection, table))
            except Exception:
                return False
        return original_table_exists(self, connection, table)

    def columns_live(connection, table: str) -> set[str]:
        if connection.__class__.__name__ == "_CompatConnection":
            try:
                return set(pg._table_columns(connection, table))
            except Exception:
                return set()
        try:
            return {str(row[1]) for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}
        except Exception:
            return set()

    def project_live(self, connection, project_id: int) -> dict:
        row = connection.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if not row:
            raise ai_service.AIServiceError("Không tìm thấy dự án đang chọn trong database.")
        return _row_to_dict(row)

    def cost_and_material_live(self, connection, project_id: int) -> dict:
        out = {
            "budgets": [], "payments": [], "variations": [],
            "materials": [], "procurements": [], "inventory": [],
        }
        table_map = {
            "budgets": "cost_budgets",
            "payments": "payment_tracking",
            "variations": "cost_variations",
            "materials": "material_master",
            "procurements": "procurement_schedule",
            "inventory": "inventory_inspection",
        }
        for key, table in table_map.items():
            if self.table_exists(connection, table):
                rows = connection.execute(
                    f"SELECT * FROM {table} WHERE project_id=? ORDER BY id DESC LIMIT ?",
                    (project_id, MAX_CONTEXT_ROWS),
                ).fetchall()
                out[key] = _rows_to_dicts_live(rows)
        return out

    def attachment_catalog_live(self, project_id: int) -> list[dict]:
        out: list[dict] = []
        with self.connect() as connection:
            if not (self.table_exists(connection, "documents") and self.table_exists(connection, "document_attachments")):
                return out
            cols = columns_live(connection, "document_attachments")
            has_blob = "file_content" in cols
            select = "a.id,a.document_id,a.file_path,a.file_name"
            for col in ("mime_type", "drive_file_id", "drive_web_url", "storage_backend", "created_at"):
                if col in cols:
                    select += f",a.{col}"
            if has_blob:
                select += ",length(a.file_content) AS blob_size"
            sql = f"""
                SELECT {select},d.doc_type,d.code,d.subject
                FROM document_attachments a JOIN documents d ON d.id=a.document_id
                WHERE d.project_id=? AND d.doc_type<>'VO' ORDER BY a.id DESC
            """
            for row in connection.execute(sql, (project_id,)).fetchall():
                data = _row_to_dict(row)
                data.setdefault("mime_type", "")
                data.setdefault("drive_file_id", "")
                data.setdefault("drive_web_url", "")
                data.setdefault("storage_backend", "")
                data.setdefault("blob_size", 0)
                out.append(data)
        return out

    def load_attachment_live(self, attachment_id: int) -> tuple[str, str, bytes]:
        with self.connect() as connection:
            if not self.table_exists(connection, "document_attachments"):
                raise ai_service.AIServiceError("Database chưa có bảng file đính kèm.")
            cols = columns_live(connection, "document_attachments")
            fields = ["file_path", "file_name"]
            for col in ("mime_type", "file_content", "drive_file_id", "storage_backend"):
                if col in cols:
                    fields.append(col)
            row = connection.execute(
                f"SELECT {','.join(fields)} FROM document_attachments WHERE id=?",
                (attachment_id,),
            ).fetchone()
            if not row:
                raise ai_service.AIServiceError("Không tìm thấy file đính kèm.")
            data = _row_to_dict(row)
            name = data.get("file_name") or Path(data.get("file_path") or "attachment").name
            mime = data.get("mime_type") or "application/octet-stream"
            blob = data.get("file_content")
            if blob:
                return str(name), str(mime), bytes(blob)
            path = str(data.get("file_path") or "")
            if path and Path(path).exists():
                return str(name), str(mime), Path(path).read_bytes()
            if data.get("drive_file_id"):
                raise ai_service.AIServiceError(
                    "File đang lưu trên Google Drive. Hãy dùng nút phân tích file đã lưu trong giao diện AI để tải file qua Drive Gateway."
                )
            raise ai_service.AIServiceError("File đính kèm không còn ở đường dẫn lưu trên máy và database không có BLOB.")

    def build_live(self, project_id: int, question: str = "", status_date=None,
                   max_tasks: int = 80, max_docs: int = 70,
                   max_drawings: int = 60, max_legal: int = 40) -> str:
        snapshot = original_build(
            self, project_id, question, status_date,
            max_tasks=max_tasks, max_docs=max_docs,
            max_drawings=max_drawings, max_legal=max_legal,
        )
        appendix = _live_project_appendix(self, int(project_id))

        try:
            with self.connect() as c:
                if self.table_exists(c, "cost_budgets"):
                    bac = _scalar(c, "SELECT COALESCE(SUM(budget_total),0) FROM cost_budgets WHERE project_id=?", (project_id,), 0)
                else:
                    bac = 0
                if self.table_exists(c, "payment_tracking"):
                    paid = _scalar(c, "SELECT COALESCE(SUM(paid_amount),0) FROM payment_tracking WHERE project_id=?", (project_id,), 0)
                else:
                    paid = 0
                if self.table_exists(c, "cost_variations"):
                    vo = _scalar(c, "SELECT COALESCE(SUM(approved_amount),0) FROM cost_variations WHERE project_id=?", (project_id,), 0)
                else:
                    vo = 0
                exact_cost = f"Chi phí: BAC {_fmt_money(bac)} VND | đã thanh toán {_fmt_money(paid)} VND | VO duyệt {_fmt_money(vo)} VND"
                snapshot = re.sub(r"(?m)^Chi phí: BAC .*?$", exact_cost, snapshot, count=1)
        except Exception:
            pass

        return snapshot.rstrip() + "\n" + appendix + "\n"

    ai_service._rows_to_dicts = _rows_to_dicts_live
    cls.connect = connect_live
    cls.table_exists = table_exists_live
    cls._project = project_live
    cls._cost_and_material = cost_and_material_live
    cls.attachment_catalog = attachment_catalog_live
    cls.load_attachment = load_attachment_live
    cls.build = build_live
    cls._qlda_ai_live_context_installed = True
    cls._qlda_ai_live_context_marker = PATCH_MARKER
