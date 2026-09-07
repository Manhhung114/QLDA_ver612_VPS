from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

DATE_FMT = "%Y-%m-%d"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def progress_delta(planned, actual) -> int:
    try:
        return int(round(float(actual))) - int(round(float(planned)))
    except Exception:
        return 0


def calc_progress_status(start_s: str, end_s: str, planned, actual, status_date: date | None = None) -> str:
    status_date = status_date or date.today()
    try:
        planned = int(round(float(planned)))
        actual = int(round(float(actual)))
    except Exception:
        return "Chưa xác định"
    if actual >= 100:
        return "Hoàn thành"
    try:
        start = datetime.strptime(start_s, DATE_FMT).date()
        end = datetime.strptime(end_s, DATE_FMT).date()
    except Exception:
        return "Chưa xác định"
    if status_date < start and actual <= 0:
        return "Chưa bắt đầu"
    if status_date > end:
        return "Chậm tiến độ"
    delta = actual - planned
    if delta < -1:
        return "Chậm tiến độ"
    if delta > 1:
        return "Nhanh tiến độ"
    return "Đúng tiến độ"


def calculate_delay_days(end_s: str, actual, actual_finish_date: str = "", status_date: date | None = None) -> int:
    """Số ngày vượt ngày Kết thúc; khi TT=100% khóa tại ngày đạt 100%."""
    status_date = status_date or date.today()
    try:
        end = datetime.strptime(str(end_s), DATE_FMT).date()
        actual = int(round(float(actual)))
    except Exception:
        return 0
    if actual >= 100:
        if not actual_finish_date:
            return 0
        try:
            finish = datetime.strptime(str(actual_finish_date), DATE_FMT).date()
        except Exception:
            return 0
        return max(0, (finish - end).days)
    return max(0, (status_date - end).days)


def planned_progress(start_s: str, finish_s: str, status_date: date | None = None) -> int:
    status_date = status_date or date.today()
    try:
        start = datetime.strptime(start_s, DATE_FMT).date()
        finish = datetime.strptime(finish_s, DATE_FMT).date()
    except Exception:
        return 0
    if finish < start or status_date < start:
        return 0
    if status_date >= finish:
        return 100
    total = max(1, (finish - start).days + 1)
    elapsed = max(0, (status_date - start).days + 1)
    return max(0, min(100, round(elapsed * 100 / total)))


class CloudDatabase:
    """SQLite backend used by the Streamlit build.

    On Render, use a Persistent Disk mounted at /var/data for durable SQLite storage.
    The UI therefore exposes DB backup/restore and stores uploaded attachments
    inside the DB instead of keeping client-side Windows paths.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.create_tables()
        self.migrate()

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=60, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=60000")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def create_tables(self):
        with self.connect() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                start_date TEXT DEFAULT '',
                end_date TEXT DEFAULT '',
                manager TEXT DEFAULT '',
                note TEXT DEFAULT '',
                source_mpp_path TEXT DEFAULT '',
                last_sync TEXT DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                wbs TEXT DEFAULT '',
                name TEXT NOT NULL,
                responsible TEXT DEFAULT '',
                start_date TEXT NOT NULL,
                end_date TEXT NOT NULL,
                duration REAL DEFAULT 1,
                planned_progress INTEGER DEFAULT 0,
                actual_progress INTEGER DEFAULT 0,
                actual_override INTEGER DEFAULT NULL,
                actual_update_date TEXT DEFAULT '',
                actual_finish_date TEXT DEFAULT '',
                status TEXT DEFAULT 'Chưa bắt đầu',
                predecessor TEXT DEFAULT '',
                note TEXT DEFAULT '',
                source_type TEXT DEFAULT 'manual',
                source_uid INTEGER,
                source_task_id INTEGER,
                outline_level INTEGER DEFAULT 1,
                is_summary INTEGER DEFAULT 0,
                is_milestone INTEGER DEFAULT 0,
                critical INTEGER DEFAULT 0,
                total_slack REAL DEFAULT 0,
                resource_names TEXT DEFAULT '',
                baseline_start TEXT DEFAULT '',
                baseline_finish TEXT DEFAULT '',
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                doc_type TEXT NOT NULL,
                code TEXT NOT NULL,
                subject TEXT NOT NULL,
                discipline TEXT DEFAULT '',
                contractor TEXT DEFAULT '',
                issuer TEXT DEFAULT '',
                assignee TEXT DEFAULT '',
                issue_date TEXT DEFAULT '',
                due_date TEXT DEFAULT '',
                closed_date TEXT DEFAULT '',
                status TEXT DEFAULT '',
                priority TEXT DEFAULT 'Trung bình',
                related_wbs TEXT DEFAULT '',
                description TEXT DEFAULT '',
                response TEXT DEFAULT '',
                note TEXT DEFAULT '',
                cost_impact REAL DEFAULT 0,
                time_impact_days INTEGER DEFAULT 0,
                created_at TEXT DEFAULT '',
                updated_at TEXT DEFAULT '',
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
                UNIQUE(project_id, doc_type, code)
            );

            CREATE TABLE IF NOT EXISTS document_attachments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER NOT NULL,
                file_path TEXT DEFAULT '',
                file_name TEXT DEFAULT '',
                mime_type TEXT DEFAULT '',
                file_content BLOB,
                drive_file_id TEXT DEFAULT '',
                drive_web_url TEXT DEFAULT '',
                storage_backend TEXT DEFAULT 'sqlite',
                created_at TEXT DEFAULT '',
                FOREIGN KEY(document_id) REFERENCES documents(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS drawings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                drawing_type TEXT NOT NULL,
                drawing_no TEXT NOT NULL,
                title TEXT NOT NULL,
                discipline TEXT DEFAULT '',
                revision TEXT DEFAULT '',
                issuer TEXT DEFAULT '',
                receiver TEXT DEFAULT '',
                received_date TEXT DEFAULT '',
                issue_date TEXT DEFAULT '',
                due_date TEXT DEFAULT '',
                priority TEXT DEFAULT '',
                description TEXT DEFAULT '',
                status TEXT DEFAULT 'Mới nhận',
                related_wbs TEXT DEFAULT '',
                reference_no TEXT DEFAULT '',
                note TEXT DEFAULT '',
                file_updated_at TEXT DEFAULT '',
                created_at TEXT DEFAULT '',
                updated_at TEXT DEFAULT '',
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
                UNIQUE(project_id, drawing_type, drawing_no, revision)
            );

            CREATE TABLE IF NOT EXISTS drawing_attachments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                drawing_id INTEGER NOT NULL,
                file_path TEXT DEFAULT '',
                file_name TEXT DEFAULT '',
                mime_type TEXT DEFAULT '',
                file_content BLOB,
                drive_file_id TEXT DEFAULT '',
                drive_web_url TEXT DEFAULT '',
                storage_backend TEXT DEFAULT 'sqlite',
                created_at TEXT DEFAULT '',
                FOREIGN KEY(drawing_id) REFERENCES drawings(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS approval_workflows (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                record_kind TEXT NOT NULL,
                subtype TEXT NOT NULL,
                record_id INTEGER NOT NULL,
                record_code TEXT NOT NULL,
                overall_status TEXT DEFAULT 'Chưa trình duyệt',
                current_stage TEXT DEFAULT '',
                submitted_by TEXT DEFAULT '',
                submitted_at TEXT DEFAULT '',
                final_approved_at TEXT DEFAULT '',
                updated_at TEXT DEFAULT '',
                UNIQUE(project_id, record_kind, subtype, record_id)
            );

            CREATE TABLE IF NOT EXISTS approval_steps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workflow_id INTEGER NOT NULL,
                stage_code TEXT NOT NULL,
                stage_order INTEGER NOT NULL,
                stage_label TEXT NOT NULL,
                approver_email TEXT DEFAULT '',
                approver_name TEXT DEFAULT '',
                status TEXT DEFAULT 'Chờ',
                comment TEXT DEFAULT '',
                acted_by TEXT DEFAULT '',
                acted_at TEXT DEFAULT '',
                FOREIGN KEY(workflow_id) REFERENCES approval_workflows(id) ON DELETE CASCADE,
                UNIQUE(workflow_id, stage_code)
            );

            CREATE TABLE IF NOT EXISTS approval_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                workflow_id INTEGER NOT NULL,
                revision_no INTEGER DEFAULT 0,
                stage_code TEXT DEFAULT '',
                stage_label TEXT DEFAULT '',
                action TEXT NOT NULL,
                status TEXT DEFAULT '',
                comment TEXT DEFAULT '',
                actor_email TEXT DEFAULT '',
                actor_name TEXT DEFAULT '',
                created_at TEXT DEFAULT '',
                FOREIGN KEY(workflow_id) REFERENCES approval_workflows(id) ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_approval_history_workflow
                ON approval_history(workflow_id, id);

            CREATE TABLE IF NOT EXISTS cost_budgets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                task_ref TEXT DEFAULT '',
                boq_item TEXT NOT NULL,
                quantity REAL DEFAULT 0,
                unit TEXT DEFAULT '',
                unit_price REAL DEFAULT 0,
                budget_total REAL DEFAULT 0,
                contract_type TEXT DEFAULT '',
                contractor TEXT DEFAULT '',
                note TEXT DEFAULT '',
                created_at TEXT DEFAULT '',
                updated_at TEXT DEFAULT '',
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS payment_tracking (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                payment_code TEXT NOT NULL,
                task_ref TEXT DEFAULT '',
                installment TEXT DEFAULT '',
                certified_cumulative REAL DEFAULT 0,
                paid_amount REAL DEFAULT 0,
                advance_amount REAL DEFAULT 0,
                advance_recovery REAL DEFAULT 0,
                planned_disbursement_pct REAL DEFAULT 0,
                payment_status TEXT DEFAULT '',
                payment_date TEXT DEFAULT '',
                note TEXT DEFAULT '',
                created_at TEXT DEFAULT '',
                updated_at TEXT DEFAULT '',
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
                UNIQUE(project_id, payment_code)
            );

            CREATE TABLE IF NOT EXISTS cost_variations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                vo_code TEXT NOT NULL,
                task_ref TEXT DEFAULT '',
                description TEXT NOT NULL,
                proposed_amount REAL DEFAULT 0,
                approved_amount REAL DEFAULT 0,
                funding_source TEXT DEFAULT '',
                status TEXT DEFAULT '',
                vo_date TEXT DEFAULT '',
                note TEXT DEFAULT '',
                created_at TEXT DEFAULT '',
                updated_at TEXT DEFAULT '',
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
                UNIQUE(project_id, vo_code)
            );

            CREATE TABLE IF NOT EXISTS material_master (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                material_code TEXT NOT NULL,
                material_name TEXT NOT NULL,
                spec_brand TEXT DEFAULT '',
                legal_ref TEXT DEFAULT '',
                supply_type TEXT DEFAULT '',
                task_ref TEXT DEFAULT '',
                note TEXT DEFAULT '',
                created_at TEXT DEFAULT '',
                updated_at TEXT DEFAULT '',
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
                UNIQUE(project_id, material_code)
            );

            CREATE TABLE IF NOT EXISTS procurement_schedule (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                material_code TEXT NOT NULL,
                task_ref TEXT DEFAULT '',
                supplier TEXT DEFAULT '',
                sample_approval_date TEXT DEFAULT '',
                order_date TEXT DEFAULT '',
                planned_delivery_date TEXT DEFAULT '',
                actual_delivery_date TEXT DEFAULT '',
                status TEXT DEFAULT '',
                note TEXT DEFAULT '',
                created_at TEXT DEFAULT '',
                updated_at TEXT DEFAULT '',
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS inventory_inspection (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                slip_code TEXT NOT NULL,
                transaction_date TEXT DEFAULT '',
                material_code TEXT NOT NULL,
                quantity_in REAL DEFAULT 0,
                quantity_out REAL DEFAULT 0,
                task_ref TEXT DEFAULT '',
                inspection_code TEXT DEFAULT '',
                material_status TEXT DEFAULT '',
                note TEXT DEFAULT '',
                created_at TEXT DEFAULT '',
                updated_at TEXT DEFAULT '',
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
                UNIQUE(project_id, slip_code)
            );

            CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project_id);
            CREATE INDEX IF NOT EXISTS idx_documents_project_type ON documents(project_id, doc_type);
            CREATE INDEX IF NOT EXISTS idx_drawings_project_type ON drawings(project_id, drawing_type);
            CREATE INDEX IF NOT EXISTS idx_cost_budget_project ON cost_budgets(project_id);
            CREATE INDEX IF NOT EXISTS idx_payment_project ON payment_tracking(project_id);
            CREATE INDEX IF NOT EXISTS idx_variation_project ON cost_variations(project_id);
            CREATE INDEX IF NOT EXISTS idx_material_project ON material_master(project_id);
            CREATE INDEX IF NOT EXISTS idx_procurement_project ON procurement_schedule(project_id);
            CREATE INDEX IF NOT EXISTS idx_inventory_project ON inventory_inspection(project_id);
            """)

    def _columns(self, c, table: str) -> set[str]:
        return {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}

    def migrate(self):
        with self.connect() as c:
            additions = {
                "projects": {
                    "source_mpp_path": "TEXT DEFAULT ''",
                    "last_sync": "TEXT DEFAULT ''",
                },
                "tasks": {
                    "source_type": "TEXT DEFAULT 'manual'", "source_uid": "INTEGER",
                    "source_task_id": "INTEGER", "outline_level": "INTEGER DEFAULT 1",
                    "is_summary": "INTEGER DEFAULT 0", "is_milestone": "INTEGER DEFAULT 0",
                    "critical": "INTEGER DEFAULT 0", "total_slack": "REAL DEFAULT 0",
                    "resource_names": "TEXT DEFAULT ''", "baseline_start": "TEXT DEFAULT ''",
                    "baseline_finish": "TEXT DEFAULT ''", "actual_override": "INTEGER DEFAULT NULL",
                    "actual_update_date": "TEXT DEFAULT ''", "actual_finish_date": "TEXT DEFAULT ''",
                },
                "document_attachments": {
                    "mime_type": "TEXT DEFAULT ''", "file_content": "BLOB",
                    "drive_file_id": "TEXT DEFAULT ''", "drive_web_url": "TEXT DEFAULT ''", "storage_backend": "TEXT DEFAULT 'sqlite'",
                },
                "drawing_attachments": {
                    "mime_type": "TEXT DEFAULT ''", "file_content": "BLOB",
                    "drive_file_id": "TEXT DEFAULT ''", "drive_web_url": "TEXT DEFAULT ''", "storage_backend": "TEXT DEFAULT 'sqlite'",
                },
                "documents": {"note": "TEXT DEFAULT ''"},
                "drawings": {
                    "file_updated_at": "TEXT DEFAULT ''",
                    "due_date": "TEXT DEFAULT ''",
                    "priority": "TEXT DEFAULT ''",
                    "description": "TEXT DEFAULT ''",
                },
                "approval_workflows": {
                    "revision_no": "INTEGER DEFAULT 0",
                    "return_stage": "TEXT DEFAULT ''",
                },
            }
            for table, cols in additions.items():
                existing = self._columns(c, table)
                for name, decl in cols.items():
                    if name not in existing:
                        c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
            c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_tasks_mpp_uid ON tasks(project_id, source_type, source_uid) WHERE source_uid IS NOT NULL")

    # ---------- Projects ----------
    def projects(self):
        with self.connect() as c:
            return c.execute("SELECT * FROM projects ORDER BY id DESC").fetchall()

    def project(self, project_id: int):
        with self.connect() as c:
            return c.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()

    def add_project(self, code, name, start_date="", end_date="", manager="", note="") -> int:
        with self.connect() as c:
            cur = c.execute(
                "INSERT INTO projects(code,name,start_date,end_date,manager,note) VALUES(?,?,?,?,?,?)",
                (code.strip(), name.strip(), start_date, end_date, manager.strip(), note.strip()),
            )
            return int(cur.lastrowid)

    def update_project(self, project_id: int, code, name, start_date="", end_date="", manager="", note=""):
        with self.connect() as c:
            c.execute(
                "UPDATE projects SET code=?,name=?,start_date=?,end_date=?,manager=?,note=? WHERE id=?",
                (code.strip(), name.strip(), start_date, end_date, manager.strip(), note.strip(), project_id),
            )

    def delete_project(self, project_id: int):
        with self.connect() as c:
            c.execute("DELETE FROM projects WHERE id=?", (project_id,))

    # ---------- Tasks ----------
    def tasks(self, project_id: int, keyword="", status="Tất cả"):
        sql = "SELECT * FROM tasks WHERE project_id=?"
        params: list = [project_id]
        if keyword:
            k = f"%{keyword}%"
            sql += " AND (name LIKE ? OR wbs LIKE ? OR responsible LIKE ? OR resource_names LIKE ?)"
            params += [k, k, k, k]
        if status and status != "Tất cả":
            sql += " AND status=?"
            params.append(status)
        sql += " ORDER BY CASE WHEN source_type='mpp' THEN 0 ELSE 1 END, source_task_id, start_date, wbs, id"
        with self.connect() as c:
            return c.execute(sql, params).fetchall()

    def task(self, task_id: int):
        with self.connect() as c:
            return c.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()

    def add_task(self, project_id: int, data: dict) -> int:
        planned = int(data.get("planned_progress", 0) or 0)
        actual = int(data.get("actual_progress", 0) or 0)
        status = calc_progress_status(data["start_date"], data["end_date"], planned, actual)
        actual = max(0, min(100, int(data.get("actual_progress", 0) or 0)))
        update_date = date.today().isoformat() if actual > 0 else ""
        finish_date = date.today().isoformat() if actual >= 100 else ""
        cols = [
            "project_id", "wbs", "name", "responsible", "start_date", "end_date", "duration",
            "planned_progress", "actual_progress", "actual_update_date", "actual_finish_date", "status", "predecessor", "note", "source_type",
            "source_uid", "source_task_id", "outline_level", "is_summary", "is_milestone", "critical",
            "total_slack", "resource_names", "baseline_start", "baseline_finish",
        ]
        defaults = dict(source_type="manual", source_uid=None, source_task_id=None, outline_level=1,
                        is_summary=0, is_milestone=0, critical=0, total_slack=0, resource_names="",
                        baseline_start="", baseline_finish="", actual_update_date=update_date, actual_finish_date=finish_date)
        payload = {**defaults, **data, "project_id": project_id, "status": status}
        with self.connect() as c:
            cur = c.execute(
                f"INSERT INTO tasks({','.join(cols)}) VALUES({','.join('?' for _ in cols)})",
                [payload.get(k) for k in cols],
            )
            return int(cur.lastrowid)

    def delete_task(self, task_id: int):
        with self.connect() as c:
            c.execute("DELETE FROM tasks WHERE id=?", (task_id,))

    def set_actual_override(self, task_id: int, actual: int, status_date: date | None = None):
        actual = max(0, min(100, int(actual)))
        status_date = status_date or date.today()
        t = self.task(task_id)
        if not t:
            return "Chưa xác định", 0
        finish_date = t["actual_finish_date"] or ""
        if actual >= 100 and not finish_date:
            finish_date = status_date.isoformat()
        elif actual < 100:
            finish_date = ""
        status = calc_progress_status(t["start_date"], t["end_date"], t["planned_progress"], actual, status_date)
        with self.connect() as c:
            c.execute(
                "UPDATE tasks SET actual_progress=?,actual_override=?,actual_update_date=?,actual_finish_date=?,status=? WHERE id=?",
                (actual, actual, status_date.isoformat(), finish_date, status, task_id)
            )
        delay = calculate_delay_days(t["end_date"], actual, finish_date, status_date)
        return status, delay

    def recalc_planned(self, project_id: int, status_date: date):
        rows = self.tasks(project_id)
        with self.connect() as c:
            for t in rows:
                planned = planned_progress(t["start_date"], t["end_date"], status_date)
                status = calc_progress_status(t["start_date"], t["end_date"], planned, t["actual_progress"], status_date)
                c.execute("UPDATE tasks SET planned_progress=?,status=? WHERE id=?", (planned, status, t["id"]))

    def sync_mpp_tasks(self, project_id: int, tasks: list[dict], source_name: str, project_info: dict | None = None):
        project_info = project_info or {}
        seen: list[int] = []
        with self.connect() as c:
            for d in tasks:
                uid = int(d.get("source_uid") or 0)
                if uid <= 0:
                    continue
                seen.append(uid)
                old = c.execute(
                    "SELECT * FROM tasks WHERE project_id=? AND source_type='mpp' AND source_uid=?",
                    (project_id, uid),
                ).fetchone()
                actual_mpp = max(0, min(100, int(d.get("actual_progress", 0) or 0)))
                actual = int(old["actual_override"]) if old and old["actual_override"] is not None else actual_mpp
                status = calc_progress_status(d["start_date"], d["end_date"], d.get("planned_progress", 0), actual)
                values = (
                    d.get("wbs", ""), d.get("name", ""), d.get("responsible", ""),
                    d["start_date"], d["end_date"], d.get("duration", 1), d.get("planned_progress", 0),
                    actual, status, d.get("predecessor", ""), d.get("note", ""),
                    d.get("task_id"), d.get("outline_level", 1), d.get("is_summary", 0),
                    d.get("is_milestone", 0), d.get("critical", 0), d.get("total_slack", 0),
                    d.get("resource_names", ""), d.get("baseline_start", ""), d.get("baseline_finish", ""),
                )
                if old:
                    c.execute("""
                        UPDATE tasks SET wbs=?,name=?,responsible=?,start_date=?,end_date=?,duration=?,
                        planned_progress=?,actual_progress=?,status=?,predecessor=?,note=?,source_task_id=?,
                        outline_level=?,is_summary=?,is_milestone=?,critical=?,total_slack=?,resource_names=?,
                        baseline_start=?,baseline_finish=? WHERE id=?
                    """, values + (old["id"],))
                else:
                    c.execute("""
                        INSERT INTO tasks(project_id,wbs,name,responsible,start_date,end_date,duration,planned_progress,
                        actual_progress,status,predecessor,note,source_type,source_uid,source_task_id,outline_level,
                        is_summary,is_milestone,critical,total_slack,resource_names,baseline_start,baseline_finish)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """, (project_id,) + values[:11] + ("mpp", uid) + values[11:])
            # Remove MPP tasks no longer present in the uploaded schedule.
            if seen:
                marks = ",".join("?" for _ in seen)
                c.execute(
                    f"DELETE FROM tasks WHERE project_id=? AND source_type='mpp' AND source_uid NOT IN ({marks})",
                    [project_id] + seen,
                )
            start_date = project_info.get("start_date", "")
            end_date = project_info.get("end_date", "")
            manager = project_info.get("manager", "")
            c.execute("""
                UPDATE projects SET source_mpp_path=?,last_sync=?,
                    start_date=CASE WHEN ?<>'' THEN ? ELSE start_date END,
                    end_date=CASE WHEN ?<>'' THEN ? ELSE end_date END,
                    manager=CASE WHEN ?<>'' THEN ? ELSE manager END
                WHERE id=?
            """, (source_name, _now(), start_date, start_date, end_date, end_date, manager, manager, project_id))

    # ---------- Documents ----------
    def documents(self, project_id: int, doc_type: str):
        with self.connect() as c:
            return c.execute(
                """SELECT d.*, (SELECT COUNT(*) FROM document_attachments a WHERE a.document_id=d.id) attachment_count
                   FROM documents d WHERE project_id=? AND doc_type=? ORDER BY issue_date DESC,id DESC""",
                (project_id, doc_type),
            ).fetchall()

    def document(self, doc_id: int):
        with self.connect() as c:
            return c.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()

    def save_document(self, project_id: int, doc_type: str, data: dict, doc_id: int | None = None) -> int:
        fields = ["code","subject","discipline","contractor","issuer","assignee","issue_date","due_date","closed_date",
                  "status","priority","related_wbs","description","response","note","cost_impact","time_impact_days"]
        vals = [data.get(f, "") for f in fields]
        with self.connect() as c:
            if doc_id:
                c.execute(
                    f"UPDATE documents SET {','.join(f'{f}=?' for f in fields)},updated_at=? WHERE id=?",
                    vals + [_now(), doc_id],
                )
                # V6.14: hard guarantee for revision workflow. If this record is
                # waiting for Contractor correction, a successful Save is also the
                # resubmit action. Do it in the SAME SQLite transaction so the UI
                # can never save the revision while leaving current_stage=CONTRACTOR.
                wf = c.execute(
                    "SELECT * FROM approval_workflows WHERE project_id=? AND record_kind='document' AND subtype=? AND record_id=?",
                    (project_id, doc_type, doc_id),
                ).fetchone()
                if wf and str(wf["current_stage"] or "") == "CONTRACTOR":
                    self._resubmit_approval_workflow_in_connection(
                        c, int(wf["id"]), str(wf["submitted_by"] or ""), ""
                    )
                return doc_id
            cur = c.execute(
                f"INSERT INTO documents(project_id,doc_type,{','.join(fields)},created_at,updated_at) VALUES(?,?,{','.join('?' for _ in fields)},?,?)",
                [project_id, doc_type] + vals + [_now(), _now()],
            )
            return int(cur.lastrowid)

    def delete_document(self, doc_id: int):
        with self.connect() as c:
            c.execute("DELETE FROM documents WHERE id=?", (doc_id,))

    def add_document_attachments(self, doc_id: int, files: Iterable[tuple[str, str, bytes]]):
        with self.connect() as c:
            for name, mime, content in files:
                c.execute(
                    "INSERT INTO document_attachments(document_id,file_name,mime_type,file_content,created_at) VALUES(?,?,?,?,?)",
                    (doc_id, name, mime or "application/octet-stream", sqlite3.Binary(content), _now()),
                )

    def add_document_drive_attachment(self, doc_id: int, name: str, mime: str, drive_file_id: str, drive_web_url: str):
        with self.connect() as c:
            c.execute(
                "INSERT INTO document_attachments(document_id,file_name,mime_type,drive_file_id,drive_web_url,storage_backend,created_at) VALUES(?,?,?,?,?,'gdrive',?)",
                (doc_id, name, mime or "application/octet-stream", drive_file_id, drive_web_url, _now()),
            )

    def document_attachments(self, doc_id: int):
        with self.connect() as c:
            return c.execute("SELECT * FROM document_attachments WHERE document_id=? ORDER BY id DESC", (doc_id,)).fetchall()

    def delete_document_attachment(self, attachment_id: int):
        with self.connect() as c:
            c.execute("DELETE FROM document_attachments WHERE id=?", (attachment_id,))

    # ---------- Drawings ----------
    def drawings(self, project_id: int, drawing_type: str):
        with self.connect() as c:
            return c.execute(
                """SELECT d.*, (SELECT COUNT(*) FROM drawing_attachments a WHERE a.drawing_id=d.id) attachment_count
                   FROM drawings d WHERE project_id=? AND drawing_type=? ORDER BY received_date DESC,id DESC""",
                (project_id, drawing_type),
            ).fetchall()

    def drawing(self, drawing_id: int):
        with self.connect() as c:
            return c.execute("SELECT * FROM drawings WHERE id=?", (drawing_id,)).fetchone()

    def save_drawing(self, project_id: int, drawing_type: str, data: dict, drawing_id: int | None = None) -> int:
        fields = ["drawing_no","title","discipline","revision","issuer","receiver","received_date","issue_date","due_date",
                  "priority","description","status","related_wbs","reference_no","note"]
        vals = [data.get(f, "") for f in fields]
        with self.connect() as c:
            if drawing_id:
                c.execute(
                    f"UPDATE drawings SET {','.join(f'{f}=?' for f in fields)},updated_at=? WHERE id=?",
                    vals + [_now(), drawing_id],
                )
                # V6.14: same hard guarantee for Shopdrawing / As-built and any
                # drawing type that has an online approval workflow. Upload alone
                # does not resubmit; the transition happens only when Save succeeds.
                wf = c.execute(
                    "SELECT * FROM approval_workflows WHERE project_id=? AND record_kind='drawing' AND subtype=? AND record_id=?",
                    (project_id, drawing_type, drawing_id),
                ).fetchone()
                if wf and str(wf["current_stage"] or "") == "CONTRACTOR":
                    self._resubmit_approval_workflow_in_connection(
                        c, int(wf["id"]), str(wf["submitted_by"] or ""), ""
                    )
                return drawing_id
            cur = c.execute(
                f"INSERT INTO drawings(project_id,drawing_type,{','.join(fields)},created_at,updated_at) VALUES(?,?,{','.join('?' for _ in fields)},?,?)",
                [project_id, drawing_type] + vals + [_now(), _now()],
            )
            return int(cur.lastrowid)

    def delete_drawing(self, drawing_id: int):
        with self.connect() as c:
            c.execute("DELETE FROM drawings WHERE id=?", (drawing_id,))

    def add_drawing_attachments(self, drawing_id: int, files: Iterable[tuple[str, str, bytes]]):
        files = list(files)
        if not files:
            return
        with self.connect() as c:
            for name, mime, content in files:
                c.execute(
                    "INSERT INTO drawing_attachments(drawing_id,file_name,mime_type,file_content,created_at) VALUES(?,?,?,?,?)",
                    (drawing_id, name, mime or "application/octet-stream", sqlite3.Binary(content), _now()),
                )
            stamp = _now()
            c.execute("UPDATE drawings SET file_updated_at=?,updated_at=? WHERE id=?", (stamp, stamp, drawing_id))

    def add_drawing_drive_attachment(self, drawing_id: int, name: str, mime: str, drive_file_id: str, drive_web_url: str):
        with self.connect() as c:
            c.execute(
                "INSERT INTO drawing_attachments(drawing_id,file_name,mime_type,drive_file_id,drive_web_url,storage_backend,created_at) VALUES(?,?,?,?,?,'gdrive',?)",
                (drawing_id, name, mime or "application/octet-stream", drive_file_id, drive_web_url, _now()),
            )
            stamp = _now()
            c.execute("UPDATE drawings SET file_updated_at=?,updated_at=? WHERE id=?", (stamp, stamp, drawing_id))

    def drawing_attachments(self, drawing_id: int):
        with self.connect() as c:
            return c.execute("SELECT * FROM drawing_attachments WHERE drawing_id=? ORDER BY id DESC", (drawing_id,)).fetchall()

    def delete_drawing_attachment(self, attachment_id: int, drawing_id: int):
        with self.connect() as c:
            c.execute("DELETE FROM drawing_attachments WHERE id=?", (attachment_id,))
            c.execute("UPDATE drawings SET file_updated_at=?,updated_at=? WHERE id=?", (_now(), _now(), drawing_id))

    # ---------- Online approval workflow ----------
    APPROVAL_STAGES = [
        ("CONTRACTOR", 0, "Nhà thầu"),
        ("SITE_MANAGEMENT", 1, "Ban điều hành"),
        ("CONSULTANT", 2, "Tư vấn giám sát"),
        ("PROJECT_MANAGEMENT", 3, "Ban quản lý dự án"),
    ]

    def approval_workflow(self, project_id: int, record_kind: str, subtype: str, record_id: int):
        with self.connect() as c:
            return c.execute(
                "SELECT * FROM approval_workflows WHERE project_id=? AND record_kind=? AND subtype=? AND record_id=?",
                (project_id, record_kind, subtype, record_id),
            ).fetchone()

    def approval_steps(self, workflow_id: int):
        with self.connect() as c:
            return c.execute(
                "SELECT * FROM approval_steps WHERE workflow_id=? ORDER BY stage_order",
                (workflow_id,),
            ).fetchall()

    def approval_history(self, workflow_id: int):
        with self.connect() as c:
            return c.execute(
                "SELECT * FROM approval_history WHERE workflow_id=? ORDER BY id DESC",
                (workflow_id,),
            ).fetchall()

    def _approval_log(self, c, workflow_id: int, revision_no: int, stage_code: str, stage_label: str,
                      action: str, status: str, comment: str = "", actor_email: str = "", actor_name: str = ""):
        c.execute(
            """INSERT INTO approval_history(
                   workflow_id,revision_no,stage_code,stage_label,action,status,comment,actor_email,actor_name,created_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (workflow_id, revision_no, stage_code, stage_label, action, status, comment, actor_email, actor_name, _now()),
        )

    def start_approval_workflow(self, project_id: int, record_kind: str, subtype: str, record_id: int, record_code: str,
                                submitted_by: str, approvers: dict, submitted_name: str = "") -> int:
        """Khởi tạo quy trình lần đầu. Không xóa lịch sử của workflow đã tồn tại."""
        now = _now()
        with self.connect() as c:
            old = c.execute(
                "SELECT * FROM approval_workflows WHERE project_id=? AND record_kind=? AND subtype=? AND record_id=?",
                (project_id, record_kind, subtype, record_id),
            ).fetchone()
            if old:
                current = str(old["current_stage"] or "")
                if current == "CONTRACTOR":
                    return self._resubmit_approval_workflow_in_connection(c, int(old["id"]), submitted_by, submitted_name)
                if current == "DONE":
                    raise ValueError("Hồ sơ đã phê duyệt hoàn tất; không thể trình lại quy trình đang đóng.")
                raise ValueError("Hồ sơ đang trong quy trình phê duyệt; không thể khởi tạo lại.")

            cur = c.execute(
                """INSERT INTO approval_workflows(
                       project_id,record_kind,subtype,record_id,record_code,overall_status,current_stage,submitted_by,
                       submitted_at,final_approved_at,updated_at,revision_no,return_stage
                   ) VALUES(?,?,?,?,?,'Đang duyệt - Ban điều hành','SITE_MANAGEMENT',?,?, '', ?,0,'')""",
                (project_id, record_kind, subtype, record_id, record_code, submitted_by, now, now),
            )
            wid = int(cur.lastrowid)
            for code, order, label in self.APPROVAL_STAGES:
                info = approvers.get(code) or {}
                status = "Đã trình" if code == "CONTRACTOR" else ("Đang chờ duyệt" if code == "SITE_MANAGEMENT" else "Chờ")
                c.execute(
                    """INSERT INTO approval_steps(
                           workflow_id,stage_code,stage_order,stage_label,approver_email,approver_name,status,acted_by,acted_at
                       ) VALUES(?,?,?,?,?,?,?,?,?)""",
                    (wid, code, order, label, str(info.get("email") or ""), str(info.get("name") or ""), status,
                     submitted_by if code == "CONTRACTOR" else "", now if code == "CONTRACTOR" else ""),
                )
            table = "documents" if record_kind == "document" else "drawings"
            c.execute(f"UPDATE {table} SET status='Đang phê duyệt',updated_at=? WHERE id=?", (now, record_id))
            self._approval_log(c, wid, 0, "CONTRACTOR", "Nhà thầu", "SUBMIT", "Đã trình",
                               actor_email=submitted_by, actor_name=submitted_name)
            return wid

    def _resubmit_approval_workflow_in_connection(self, c, workflow_id: int, submitted_by: str, submitted_name: str = "") -> int:
        wf = c.execute("SELECT * FROM approval_workflows WHERE id=?", (workflow_id,)).fetchone()
        if not wf:
            raise ValueError("Không tìm thấy quy trình duyệt.")
        if str(wf["current_stage"] or "") != "CONTRACTOR":
            raise ValueError("Hồ sơ chưa ở trạng thái chờ Nhà thầu chỉnh sửa.")
        target_stage = str(wf["return_stage"] or "SITE_MANAGEMENT")
        target = c.execute(
            "SELECT * FROM approval_steps WHERE workflow_id=? AND stage_code=?",
            (workflow_id, target_stage),
        ).fetchone()
        if not target:
            raise ValueError("Không tìm thấy cấp duyệt cần trình lại.")
        revision_no = int(wf["revision_no"] or 0) + 1
        now = _now()

        # Nhà thầu đã cập nhật hồ sơ; chỉ mở lại đúng cấp đã trả hồ sơ.
        c.execute(
            "UPDATE approval_steps SET status='Đã trình lại',acted_by=?,acted_at=? WHERE workflow_id=? AND stage_code='CONTRACTOR'",
            (submitted_by, now, workflow_id),
        )
        c.execute(
            "UPDATE approval_steps SET status='Đang chờ duyệt',comment='',acted_by='',acted_at='' WHERE id=?",
            (target["id"],),
        )
        c.execute(
            "UPDATE approval_steps SET status='Chờ' WHERE workflow_id=? AND stage_order>?",
            (workflow_id, target["stage_order"]),
        )
        overall = "Trình lại - Đang duyệt - " + str(target["stage_label"])
        c.execute(
            """UPDATE approval_workflows
               SET overall_status=?,current_stage=?,submitted_by=?,submitted_at=?,updated_at=?,revision_no=?,return_stage=''
               WHERE id=?""",
            (overall, target_stage, submitted_by, now, now, revision_no, workflow_id),
        )
        table = "documents" if wf["record_kind"] == "document" else "drawings"
        c.execute(f"UPDATE {table} SET status=?,updated_at=? WHERE id=?", (overall, now, wf["record_id"]))
        self._approval_log(c, workflow_id, revision_no, "CONTRACTOR", "Nhà thầu", "RESUBMIT", overall,
                           actor_email=submitted_by, actor_name=submitted_name)
        return workflow_id

    def resubmit_approval_workflow(self, workflow_id: int, submitted_by: str, submitted_name: str = "") -> dict:
        with self.connect() as c:
            wid = self._resubmit_approval_workflow_in_connection(c, workflow_id, submitted_by, submitted_name)
            wf = c.execute("SELECT * FROM approval_workflows WHERE id=?", (wid,)).fetchone()
            step = c.execute(
                "SELECT * FROM approval_steps WHERE workflow_id=? AND stage_code=?",
                (wid, wf["current_stage"]),
            ).fetchone()
            return {
                "workflow_id": wid,
                "next_email": step["approver_email"] if step else "",
                "next_name": step["approver_name"] if step else "",
                "status": wf["overall_status"],
                "current_stage": wf["current_stage"],
                "revision_no": int(wf["revision_no"] or 0),
            }

    def repair_revision_resubmit_if_saved(
        self,
        project_id: int,
        record_kind: str,
        subtype: str,
        record_id: int,
        submitted_by: str = "",
        submitted_name: str = "",
    ) -> dict:
        """V6.13 fail-safe: tự trình lại khi Nhà thầu đã Lưu hồ sơ sau lần bị trả.

        REQUEST_REVISION cập nhật ``workflow.updated_at`` và bản ghi hồ sơ cùng thời điểm.
        Khi Nhà thầu thực sự bấm Lưu, ``record.updated_at`` sẽ mới hơn trong khi
        workflow vẫn có thể còn ở CONTRACTOR nếu UI/session bị gián đoạn. Khi đó
        phương thức này phục hồi đúng ``return_stage`` bằng cùng logic resubmit chuẩn.
        Chỉ việc upload file lên Drive không thay đổi SQLite ``updated_at``, nên không
        tự trình lại trước khi Nhà thầu bấm Lưu.
        """
        kind = str(record_kind or "").strip().lower()
        if kind not in {"document", "drawing"}:
            return {"repaired": False, "reason": "unsupported_kind"}
        table = "documents" if kind == "document" else "drawings"
        with self.connect() as c:
            wf = c.execute(
                "SELECT * FROM approval_workflows WHERE project_id=? AND record_kind=? AND subtype=? AND record_id=?",
                (project_id, kind, subtype, record_id),
            ).fetchone()
            if not wf:
                return {"repaired": False, "reason": "no_workflow"}
            if str(wf["current_stage"] or "") != "CONTRACTOR":
                return {
                    "repaired": False,
                    "reason": "not_waiting_contractor",
                    "current_stage": str(wf["current_stage"] or ""),
                }
            row = c.execute(f"SELECT updated_at FROM {table} WHERE id=?", (record_id,)).fetchone()
            if not row:
                return {"repaired": False, "reason": "record_missing"}
            record_updated = str(row["updated_at"] or "")
            workflow_updated = str(wf["updated_at"] or "")
            if not record_updated or record_updated <= workflow_updated:
                return {
                    "repaired": False,
                    "reason": "record_not_saved_after_return",
                    "record_updated_at": record_updated,
                    "workflow_updated_at": workflow_updated,
                }

            actor_email = str(submitted_by or wf["submitted_by"] or "").strip().lower()
            actor_name = str(submitted_name or "").strip()
            wid = self._resubmit_approval_workflow_in_connection(
                c, int(wf["id"]), actor_email, actor_name
            )
            repaired_wf = c.execute("SELECT * FROM approval_workflows WHERE id=?", (wid,)).fetchone()
            step = c.execute(
                "SELECT * FROM approval_steps WHERE workflow_id=? AND stage_code=?",
                (wid, repaired_wf["current_stage"]),
            ).fetchone()
            return {
                "repaired": True,
                "workflow_id": wid,
                "status": str(repaired_wf["overall_status"] or ""),
                "current_stage": str(repaired_wf["current_stage"] or ""),
                "revision_no": int(repaired_wf["revision_no"] or 0),
                "next_email": str(step["approver_email"] or "") if step else "",
                "next_name": str(step["approver_name"] or "") if step else "",
            }

    def approval_action(self, workflow_id: int, stage_code: str, actor_email: str, action: str, comment: str, actor_name: str = "", actor_role: str = ""):
        now = _now()
        action = action.upper().strip()
        with self.connect() as c:
            wf = c.execute("SELECT * FROM approval_workflows WHERE id=?", (workflow_id,)).fetchone()
            if not wf:
                raise ValueError("Không tìm thấy quy trình duyệt.")
            if str(wf["current_stage"] or "") != stage_code:
                raise ValueError("Bước duyệt này không còn là bước đang chờ xử lý.")
            step = c.execute(
                "SELECT * FROM approval_steps WHERE workflow_id=? AND stage_code=?",
                (workflow_id, stage_code),
            ).fetchone()
            if not step:
                raise ValueError("Không tìm thấy bước duyệt.")
            assigned_email = str(step["approver_email"] or "").lower()
            actor_email_norm = str(actor_email or "").lower()
            actor_role_norm = str(actor_role or "").strip().upper()
            # V6.8: định tuyến theo vai trò. Nếu workflow cũ/chưa đọc được danh bạ
            # nên approver_email đang trống, người đăng nhập đúng vai trò của bước
            # được quyền nhận (claim) và xử lý. Nếu đã gán email cụ thể thì vẫn
            # chỉ người đó được xử lý, trừ trường hợp dữ liệu legacy trống.
            expected_role = str(stage_code or "").strip().upper()
            if actor_role_norm:
                if actor_role_norm != expected_role:
                    raise PermissionError("Tài khoản hiện tại không đúng vai trò của bước phê duyệt này.")
            elif assigned_email != actor_email_norm:
                # Tương thích lời gọi legacy/test chưa truyền actor_role.
                raise PermissionError("Bạn không phải người được chỉ định duyệt bước này.")
            # Vai trò là nguồn phân quyền chính. Người hiện tại xử lý bước sẽ được
            # ghi nhận vào approver_email/name, kể cả workflow legacy đã gán một
            # email khác cùng vai trò.
            if assigned_email != actor_email_norm or not str(step["approver_name"] or "").strip():
                c.execute(
                    "UPDATE approval_steps SET approver_email=?,approver_name=? WHERE id=?",
                    (actor_email_norm, actor_name, step["id"]),
                )
            if str(step["status"] or "") != "Đang chờ duyệt":
                raise ValueError("Bước này đã được xử lý hoặc chưa đến lượt duyệt.")

            revision_no = int(wf["revision_no"] or 0)
            if action == "APPROVE":
                c.execute(
                    "UPDATE approval_steps SET status='Đã duyệt',comment=?,acted_by=?,acted_at=? WHERE id=?",
                    (comment, actor_email, now, step["id"]),
                )
                self._approval_log(c, workflow_id, revision_no, stage_code, str(step["stage_label"]),
                                   "APPROVE", "Đã duyệt", comment, actor_email, actor_name)
                next_step = c.execute(
                    "SELECT * FROM approval_steps WHERE workflow_id=? AND stage_order>? ORDER BY stage_order LIMIT 1",
                    (workflow_id, step["stage_order"]),
                ).fetchone()
                if next_step:
                    c.execute("UPDATE approval_steps SET status='Đang chờ duyệt' WHERE id=?", (next_step["id"],))
                    overall = "Đang duyệt - " + str(next_step["stage_label"])
                    current = str(next_step["stage_code"])
                    c.execute(
                        "UPDATE approval_workflows SET overall_status=?,current_stage=?,updated_at=?,return_stage='' WHERE id=?",
                        (overall, current, now, workflow_id),
                    )
                    table = "documents" if wf["record_kind"] == "document" else "drawings"
                    c.execute(f"UPDATE {table} SET status=?,updated_at=? WHERE id=?", (overall, now, wf["record_id"]))
                    return {
                        "completed": False,
                        "next_email": next_step["approver_email"],
                        "next_name": next_step["approver_name"],
                        "status": overall,
                        "current_stage": current,
                    }

                c.execute(
                    """UPDATE approval_workflows
                       SET overall_status='Đã phê duyệt',current_stage='DONE',final_approved_at=?,updated_at=?,return_stage=''
                       WHERE id=?""",
                    (now, now, workflow_id),
                )
                table = "documents" if wf["record_kind"] == "document" else "drawings"
                c.execute(f"UPDATE {table} SET status='Đã phê duyệt',updated_at=? WHERE id=?", (now, wf["record_id"]))
                self._approval_log(c, workflow_id, revision_no, "DONE", "Hoàn tất", "COMPLETE", "Đã phê duyệt",
                                   actor_email=actor_email, actor_name=actor_name)
                return {
                    "completed": True,
                    "next_email": wf["submitted_by"],
                    "next_name": "Nhà thầu",
                    "status": "Đã phê duyệt",
                    "current_stage": "DONE",
                }

            if action in {"REJECT", "REQUEST_REVISION"}:
                if not str(comment or "").strip():
                    raise ValueError("Cần nhập ý kiến khi yêu cầu chỉnh sửa.")
                c.execute(
                    "UPDATE approval_steps SET status='Yêu cầu chỉnh sửa',comment=?,acted_by=?,acted_at=? WHERE id=?",
                    (comment, actor_email, now, step["id"]),
                )
                overall = "Chờ Nhà thầu chỉnh sửa - " + str(step["stage_label"])
                c.execute(
                    """UPDATE approval_workflows
                       SET overall_status=?,current_stage='CONTRACTOR',return_stage=?,updated_at=? WHERE id=?""",
                    (overall, stage_code, now, workflow_id),
                )
                table = "documents" if wf["record_kind"] == "document" else "drawings"
                c.execute(f"UPDATE {table} SET status=?,updated_at=? WHERE id=?", (overall, now, wf["record_id"]))
                self._approval_log(c, workflow_id, revision_no, stage_code, str(step["stage_label"]),
                                   "REQUEST_REVISION", overall, comment, actor_email, actor_name)
                return {
                    "completed": False,
                    "next_email": wf["submitted_by"],
                    "next_name": "Nhà thầu",
                    "status": overall,
                    "current_stage": "CONTRACTOR",
                    "return_stage": stage_code,
                }

            raise ValueError("Hành động duyệt không hợp lệ.")

    # ---------- Cost management ----------
    def cost_budgets(self, project_id: int):
        with self.connect() as c:
            return c.execute("SELECT * FROM cost_budgets WHERE project_id=? ORDER BY id DESC", (project_id,)).fetchall()

    def cost_budget(self, row_id: int):
        with self.connect() as c:
            return c.execute("SELECT * FROM cost_budgets WHERE id=?", (row_id,)).fetchone()

    def save_cost_budget(self, project_id: int, data: dict, row_id: int | None = None) -> int:
        fields = ["task_ref","boq_item","quantity","unit","unit_price","budget_total","contract_type","contractor","note"]
        vals = [data.get(f, "") for f in fields]
        with self.connect() as c:
            if row_id:
                c.execute(f"UPDATE cost_budgets SET {','.join(f'{f}=?' for f in fields)},updated_at=? WHERE id=?", vals + [_now(), row_id])
                return row_id
            cur = c.execute(f"INSERT INTO cost_budgets(project_id,{','.join(fields)},created_at,updated_at) VALUES(?,{','.join('?' for _ in fields)},?,?)", [project_id] + vals + [_now(), _now()])
            return int(cur.lastrowid)

    def delete_cost_budget(self, row_id: int):
        with self.connect() as c: c.execute("DELETE FROM cost_budgets WHERE id=?", (row_id,))

    def payments(self, project_id: int):
        with self.connect() as c:
            return c.execute("SELECT * FROM payment_tracking WHERE project_id=? ORDER BY payment_date DESC,id DESC", (project_id,)).fetchall()

    def payment(self, row_id: int):
        with self.connect() as c: return c.execute("SELECT * FROM payment_tracking WHERE id=?", (row_id,)).fetchone()

    def save_payment(self, project_id: int, data: dict, row_id: int | None = None) -> int:
        fields = ["payment_code","task_ref","installment","certified_cumulative","paid_amount","advance_amount","advance_recovery","planned_disbursement_pct","payment_status","payment_date","note"]
        vals = [data.get(f, "") for f in fields]
        with self.connect() as c:
            if row_id:
                c.execute(f"UPDATE payment_tracking SET {','.join(f'{f}=?' for f in fields)},updated_at=? WHERE id=?", vals + [_now(), row_id]); return row_id
            cur = c.execute(f"INSERT INTO payment_tracking(project_id,{','.join(fields)},created_at,updated_at) VALUES(?,{','.join('?' for _ in fields)},?,?)", [project_id] + vals + [_now(), _now()]); return int(cur.lastrowid)

    def delete_payment(self, row_id: int):
        with self.connect() as c: c.execute("DELETE FROM payment_tracking WHERE id=?", (row_id,))

    def cost_variations(self, project_id: int):
        with self.connect() as c: return c.execute("SELECT * FROM cost_variations WHERE project_id=? ORDER BY vo_date DESC,id DESC", (project_id,)).fetchall()

    def cost_variation(self, row_id: int):
        with self.connect() as c: return c.execute("SELECT * FROM cost_variations WHERE id=?", (row_id,)).fetchone()

    def save_cost_variation(self, project_id: int, data: dict, row_id: int | None = None) -> int:
        fields = ["vo_code","task_ref","description","proposed_amount","approved_amount","funding_source","status","vo_date","note"]
        vals = [data.get(f, "") for f in fields]
        with self.connect() as c:
            if row_id:
                c.execute(f"UPDATE cost_variations SET {','.join(f'{f}=?' for f in fields)},updated_at=? WHERE id=?", vals + [_now(), row_id]); return row_id
            cur = c.execute(f"INSERT INTO cost_variations(project_id,{','.join(fields)},created_at,updated_at) VALUES(?,{','.join('?' for _ in fields)},?,?)", [project_id] + vals + [_now(), _now()]); return int(cur.lastrowid)

    def delete_cost_variation(self, row_id: int):
        with self.connect() as c: c.execute("DELETE FROM cost_variations WHERE id=?", (row_id,))

    # ---------- Material & equipment management ----------
    def materials(self, project_id: int):
        with self.connect() as c: return c.execute("SELECT * FROM material_master WHERE project_id=? ORDER BY material_code", (project_id,)).fetchall()

    def material(self, row_id: int):
        with self.connect() as c: return c.execute("SELECT * FROM material_master WHERE id=?", (row_id,)).fetchone()

    def save_material(self, project_id: int, data: dict, row_id: int | None = None) -> int:
        fields = ["material_code","material_name","spec_brand","legal_ref","supply_type","task_ref","note"]
        vals = [data.get(f, "") for f in fields]
        with self.connect() as c:
            if row_id:
                c.execute(f"UPDATE material_master SET {','.join(f'{f}=?' for f in fields)},updated_at=? WHERE id=?", vals + [_now(), row_id]); return row_id
            cur = c.execute(f"INSERT INTO material_master(project_id,{','.join(fields)},created_at,updated_at) VALUES(?,{','.join('?' for _ in fields)},?,?)", [project_id] + vals + [_now(), _now()]); return int(cur.lastrowid)

    def delete_material(self, row_id: int):
        with self.connect() as c: c.execute("DELETE FROM material_master WHERE id=?", (row_id,))

    def procurements(self, project_id: int):
        with self.connect() as c: return c.execute("SELECT * FROM procurement_schedule WHERE project_id=? ORDER BY planned_delivery_date,id DESC", (project_id,)).fetchall()

    def procurement(self, row_id: int):
        with self.connect() as c: return c.execute("SELECT * FROM procurement_schedule WHERE id=?", (row_id,)).fetchone()

    def save_procurement(self, project_id: int, data: dict, row_id: int | None = None) -> int:
        fields = ["material_code","task_ref","supplier","sample_approval_date","order_date","planned_delivery_date","actual_delivery_date","status","note"]
        vals = [data.get(f, "") for f in fields]
        with self.connect() as c:
            if row_id:
                c.execute(f"UPDATE procurement_schedule SET {','.join(f'{f}=?' for f in fields)},updated_at=? WHERE id=?", vals + [_now(), row_id]); return row_id
            cur = c.execute(f"INSERT INTO procurement_schedule(project_id,{','.join(fields)},created_at,updated_at) VALUES(?,{','.join('?' for _ in fields)},?,?)", [project_id] + vals + [_now(), _now()]); return int(cur.lastrowid)

    def delete_procurement(self, row_id: int):
        with self.connect() as c: c.execute("DELETE FROM procurement_schedule WHERE id=?", (row_id,))

    def inventory_rows(self, project_id: int):
        with self.connect() as c: return c.execute("SELECT * FROM inventory_inspection WHERE project_id=? ORDER BY transaction_date DESC,id DESC", (project_id,)).fetchall()

    def inventory_row(self, row_id: int):
        with self.connect() as c: return c.execute("SELECT * FROM inventory_inspection WHERE id=?", (row_id,)).fetchone()

    def save_inventory_row(self, project_id: int, data: dict, row_id: int | None = None) -> int:
        fields = ["slip_code","transaction_date","material_code","quantity_in","quantity_out","task_ref","inspection_code","material_status","note"]
        vals = [data.get(f, "") for f in fields]
        with self.connect() as c:
            if row_id:
                c.execute(f"UPDATE inventory_inspection SET {','.join(f'{f}=?' for f in fields)},updated_at=? WHERE id=?", vals + [_now(), row_id]); return row_id
            cur = c.execute(f"INSERT INTO inventory_inspection(project_id,{','.join(fields)},created_at,updated_at) VALUES(?,{','.join('?' for _ in fields)},?,?)", [project_id] + vals + [_now(), _now()]); return int(cur.lastrowid)

    def delete_inventory_row(self, row_id: int):
        with self.connect() as c: c.execute("DELETE FROM inventory_inspection WHERE id=?", (row_id,))

    # ---------- Backup ----------
    def backup_bytes(self) -> bytes:
        # Checkpoint then read the single-file database.
        with self.connect() as c:
            c.execute("PRAGMA wal_checkpoint(FULL)")
        return self.path.read_bytes() if self.path.exists() else b""

    def restore_bytes(self, data: bytes):
        if not data.startswith(b"SQLite format 3\x00"):
            raise ValueError("File không phải SQLite database hợp lệ.")
        tmp = self.path.with_suffix(".restore.tmp")
        tmp.write_bytes(data)
        conn = sqlite3.connect(tmp)
        try:
            check = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if check != "ok":
                raise ValueError(f"Database lỗi integrity_check: {check}")
        finally:
            conn.close()
        os.replace(tmp, self.path)
        self.create_tables()
        self.migrate()
