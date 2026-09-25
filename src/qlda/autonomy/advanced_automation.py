from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from .models import ActionMode, RiskLevel, ToolSpec


ADVANCED_SCHEMA_VERSION = "V9.6-ADVANCED-AUTOMATION-1"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


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


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("đ", "d")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _float(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value or 0)
        return number if math.isfinite(number) else float(default)
    except Exception:
        return float(default)


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except Exception:
            pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except Exception:
        return None


def _table_exists(connection, table: str) -> bool:
    try:
        connection.execute(f"SELECT 1 FROM {table} LIMIT 1")
        return True
    except Exception:
        return False


def _rows(connection, sql: str, params: tuple[Any, ...] | list[Any] = ()) -> list[dict[str, Any]]:
    try:
        return [_rowdict(row) for row in connection.execute(sql, params).fetchall()]
    except Exception:
        return []


def _one(connection, sql: str, params: tuple[Any, ...] | list[Any] = ()) -> dict[str, Any]:
    try:
        return _rowdict(connection.execute(sql, params).fetchone())
    except Exception:
        return {}


def ensure_advanced_schema(db) -> None:
    with db.connect() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS qlda_ai_task_routes(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                fingerprint TEXT NOT NULL,
                finding_code TEXT DEFAULT '',
                discipline TEXT DEFAULT '',
                assignee_email TEXT DEFAULT '',
                assignee_name TEXT DEFAULT '',
                priority TEXT DEFAULT '',
                sla_hours INTEGER DEFAULT 0,
                title TEXT DEFAULT '',
                detail TEXT DEFAULT '',
                source_ref TEXT DEFAULT '',
                status TEXT DEFAULT 'PROPOSED',
                created_at TEXT DEFAULT '',
                updated_at TEXT DEFAULT '',
                UNIQUE(project_id,fingerprint)
            );
            CREATE INDEX IF NOT EXISTS idx_qlda_ai_task_routes_project
                ON qlda_ai_task_routes(project_id,status,updated_at);

            CREATE TABLE IF NOT EXISTS qlda_contract_obligations(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                obligation_key TEXT NOT NULL,
                source_type TEXT DEFAULT '',
                source_id TEXT DEFAULT '',
                source_ref TEXT DEFAULT '',
                title TEXT NOT NULL,
                responsible TEXT DEFAULT '',
                due_date TEXT DEFAULT '',
                remind_days INTEGER DEFAULT 30,
                status TEXT DEFAULT 'OPEN',
                evidence TEXT DEFAULT '',
                created_at TEXT DEFAULT '',
                updated_at TEXT DEFAULT '',
                UNIQUE(project_id,obligation_key)
            );
            CREATE INDEX IF NOT EXISTS idx_qlda_contract_obligations_project
                ON qlda_contract_obligations(project_id,status,due_date);

            CREATE TABLE IF NOT EXISTS qlda_ipc_boq_reconciliation(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                claim_id TEXT DEFAULT '',
                claim_code TEXT DEFAULT '',
                item_key TEXT DEFAULT '',
                boq_id INTEGER DEFAULT 0,
                claim_row INTEGER DEFAULT 0,
                flag_code TEXT NOT NULL,
                severity TEXT DEFAULT 'medium',
                detail TEXT DEFAULT '',
                claim_qty REAL DEFAULT 0,
                boq_qty REAL DEFAULT 0,
                claim_unit_price REAL DEFAULT 0,
                boq_unit_price REAL DEFAULT 0,
                claim_cumulative_value REAL DEFAULT 0,
                boq_budget_total REAL DEFAULT 0,
                created_at TEXT DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_qlda_ipc_boq_recon_project
                ON qlda_ipc_boq_reconciliation(project_id,claim_id,severity,flag_code);

            CREATE TABLE IF NOT EXISTS qlda_ai_forecasts(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                forecast_type TEXT NOT NULL,
                horizon_days INTEGER DEFAULT 30,
                predicted_finish TEXT DEFAULT '',
                risk_score REAL DEFAULT 0,
                confidence REAL DEFAULT 0,
                payload TEXT DEFAULT '',
                created_at TEXT DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_qlda_ai_forecasts_project
                ON qlda_ai_forecasts(project_id,forecast_type,created_at);

            CREATE TABLE IF NOT EXISTS qlda_vo_drafts(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                draft_code TEXT NOT NULL,
                source_type TEXT DEFAULT '',
                source_ref TEXT DEFAULT '',
                item_name TEXT DEFAULT '',
                unit TEXT DEFAULT '',
                old_qty REAL DEFAULT 0,
                new_qty REAL DEFAULT 0,
                delta_qty REAL DEFAULT 0,
                unit_price REAL DEFAULT 0,
                estimated_amount REAL DEFAULT 0,
                status TEXT DEFAULT 'DRAFT',
                evidence TEXT DEFAULT '',
                created_by TEXT DEFAULT '',
                created_at TEXT DEFAULT '',
                UNIQUE(project_id,draft_code)
            );
            CREATE INDEX IF NOT EXISTS idx_qlda_vo_drafts_project
                ON qlda_vo_drafts(project_id,status,created_at);

            CREATE TABLE IF NOT EXISTS qlda_site_vision_observations(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id INTEGER NOT NULL,
                task_id INTEGER DEFAULT 0,
                evidence_ref TEXT DEFAULT '',
                object_label TEXT DEFAULT '',
                observed_qty REAL DEFAULT 0,
                planned_qty REAL DEFAULT 0,
                proposed_progress REAL DEFAULT 0,
                confidence REAL DEFAULT 0,
                status TEXT DEFAULT 'PROPOSED',
                notes TEXT DEFAULT '',
                created_at TEXT DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_qlda_site_vision_project
                ON qlda_site_vision_observations(project_id,task_id,created_at);
            """
        )


def advanced_tool_specs() -> tuple[ToolSpec, ...]:
    return (
        ToolSpec(
            "route_work_task",
            "V9.1 tự phân luồng finding tới đúng discipline/người phụ trách và chống task trùng",
            RiskLevel.LOW,
            ActionMode.AUTO,
            ("admin", "update"),
        ),
        ToolSpec(
            "audit_contract_obligations",
            "V9.2 lập ledger nghĩa vụ/hạn hợp đồng và cảnh báo mốc đến hạn",
            RiskLevel.LOW,
            ActionMode.AUTO,
        ),
        ToolSpec(
            "reconcile_ipc_boq",
            "V9.3 đối soát IPC với BOQ theo dòng, khối lượng, đơn vị, đơn giá và giá trị lũy kế",
            RiskLevel.LOW,
            ActionMode.AUTO,
        ),
        ToolSpec(
            "draft_vo_from_change",
            "V9.4 tạo bản nháp VO từ thay đổi đã định lượng; tuyệt đối không tự phê duyệt",
            RiskLevel.MEDIUM,
            ActionMode.DRAFT,
            ("admin", "update"),
        ),
        ToolSpec(
            "forecast_project_risk",
            "V9.5 dự báo rủi ro tiến độ bằng velocity có kiểm chứng và nhu cầu tiền theo IPC có due-date thật",
            RiskLevel.LOW,
            ActionMode.AUTO,
        ),
        ToolSpec(
            "analyze_site_progress",
            "V9.6 phân tích quan sát hiện trường/ảnh và chỉ đề xuất phần trăm tiến độ",
            RiskLevel.MEDIUM,
            ActionMode.DRAFT,
            ("admin", "update"),
        ),
        ToolSpec(
            "run_advanced_supervision",
            "Chạy tổng hợp V9.1-V9.6 cho một AI tenant nhà thầu",
            RiskLevel.LOW,
            ActionMode.AUTO,
            ("admin",),
        ),
    )


_DISCIPLINE_RULES = (
    ("PCCC", ("pccc", "phong chay", "bao chay", "sprinkler", "fire")),
    ("ELECTRICAL", ("dien", "electrical", "cable", "cap dien", "tu dien", "lighting")),
    ("ELV", ("dien nhe", "elv", "camera", "cctv", "lan", "pa", "access control")),
    ("PLUMBING", ("cap thoat nuoc", "plumbing", "thoat nuoc", "cap nuoc", "ong nuoc")),
    ("HVAC", ("hvac", "dieu hoa", "thong gio", "ahu", "fcu", "duct")),
    ("STRUCTURE", ("ket cau", "be tong", "cot", "dam", "san", "thep", "rebar")),
    ("ARCHITECTURE", ("kien truc", "hoan thien", "tuong", "tran", "son", "gach")),
    ("QS", ("boq", "ipc", "thanh toan", "khoi luong", "don gia", "vo", "phat sinh")),
    ("QAQC", ("ncr", "nghiem thu", "inspection", "chat luong", "khong dat")),
    ("DESIGN", ("rfi", "rfa", "ban ve", "drawing", "thiet ke")),
)


class AdvancedAutomation:
    def __init__(self, base_adapter) -> None:
        self.base = base_adapter
        self.db = base_adapter.db
        ensure_advanced_schema(self.db)

    def _scope(self, project_id: int) -> tuple[dict[str, Any], int]:
        scope = self.base._resolve_scope(int(project_id))
        return scope, int(scope["workspace_project_id"])

    @staticmethod
    def _discipline(text: str, explicit: str = "") -> str:
        if str(explicit or "").strip():
            return str(explicit).strip().upper()
        plain = _norm(text)
        for label, terms in _DISCIPLINE_RULES:
            if any(term in plain for term in terms):
                return label
        return "GENERAL"

    def _assignee_candidates(self, workspace_id: int, discipline: str, text: str) -> list[dict[str, Any]]:
        tokens = set(_norm(text).split())
        candidates: dict[str, dict[str, Any]] = {}
        with self.db.connect() as connection:
            if _table_exists(connection, "documents"):
                for row in _rows(
                    connection,
                    "SELECT discipline,assignee,issuer,subject,doc_type FROM documents WHERE project_id=?",
                    (workspace_id,),
                ):
                    for field in ("assignee", "issuer"):
                        person = str(row.get(field) or "").strip()
                        if not person:
                            continue
                        key = person.casefold()
                        score = 0
                        if _norm(row.get("discipline")) == _norm(discipline):
                            score += 5
                        row_text = _norm(" ".join(str(row.get(k) or "") for k in ("subject", "doc_type", "discipline")))
                        score += len(tokens & set(row_text.split()))
                        item = candidates.setdefault(
                            key,
                            {
                                "name": person if "@" not in person else person.split("@", 1)[0],
                                "email": person if "@" in person else "",
                                "score": 0,
                            },
                        )
                        item["score"] = max(int(item["score"]), score)
            if _table_exists(connection, "tasks"):
                for row in _rows(
                    connection,
                    "SELECT responsible,resource_names,name,wbs FROM tasks WHERE project_id=?",
                    (workspace_id,),
                ):
                    row_text = _norm(" ".join(str(row.get(k) or "") for k in ("name", "wbs")))
                    overlap = len(tokens & set(row_text.split()))
                    for field in ("responsible", "resource_names"):
                        person = str(row.get(field) or "").strip()
                        if not person:
                            continue
                        for part in [x.strip() for x in re.split(r"[,;]", person) if x.strip()]:
                            key = part.casefold()
                            item = candidates.setdefault(
                                key,
                                {
                                    "name": part if "@" not in part else part.split("@", 1)[0],
                                    "email": part if "@" in part else "",
                                    "score": 0,
                                },
                            )
                            item["score"] = max(int(item["score"]), overlap)
        return sorted(candidates.values(), key=lambda x: (int(x.get("score") or 0), bool(x.get("email"))), reverse=True)

    def route_work_task(
        self,
        *,
        project_id: int,
        actor: str = "",
        finding_code: str = "",
        title: str = "",
        detail: str = "",
        severity: str = "medium",
        discipline: str = "",
        source_ref: str = "",
        assignee_email: str = "",
        assignee_name: str = "",
        create_task: bool = False,
        **_: Any,
    ) -> dict[str, Any]:
        _scope, workspace_id = self._scope(project_id)
        text = " ".join((finding_code, title, detail, source_ref))
        detected = self._discipline(text, discipline)
        severity_norm = str(severity or "medium").strip().lower()
        sla = {"critical": 8, "high": 24, "medium": 48, "low": 72}.get(severity_norm, 48)
        priority = {"critical": "Khẩn cấp", "high": "Cao", "medium": "Bình thường", "low": "Thấp"}.get(severity_norm, "Bình thường")

        selected_email = str(assignee_email or "").strip().lower()
        selected_name = str(assignee_name or "").strip()
        candidates = self._assignee_candidates(workspace_id, detected, text)
        if not selected_email:
            for candidate in candidates:
                if candidate.get("email"):
                    selected_email = str(candidate["email"]).strip().lower()
                    selected_name = selected_name or str(candidate.get("name") or "")
                    break
        if not selected_name and candidates:
            selected_name = str(candidates[0].get("name") or "")

        fingerprint_raw = f"{workspace_id}|{_norm(finding_code)}|{_norm(source_ref)}|{_norm(title)}"
        fingerprint = hashlib.sha256(fingerprint_raw.encode("utf-8")).hexdigest()[:24]
        stamp = _now()
        route_status = "ROUTED" if selected_email else "NEEDS_ASSIGNEE"
        with self.db.connect() as connection:
            existing = _one(
                connection,
                "SELECT * FROM qlda_ai_task_routes WHERE project_id=? AND fingerprint=? LIMIT 1",
                (workspace_id, fingerprint),
            )
            if existing:
                connection.execute(
                    """UPDATE qlda_ai_task_routes SET discipline=?,assignee_email=?,assignee_name=?,priority=?,sla_hours=?,
                    title=?,detail=?,source_ref=?,status=?,updated_at=? WHERE project_id=? AND fingerprint=?""",
                    (
                        detected,
                        selected_email or existing.get("assignee_email") or "",
                        selected_name or existing.get("assignee_name") or "",
                        priority,
                        sla,
                        title,
                        detail,
                        source_ref,
                        route_status if selected_email else str(existing.get("status") or route_status),
                        stamp,
                        workspace_id,
                        fingerprint,
                    ),
                )
            else:
                connection.execute(
                    """INSERT INTO qlda_ai_task_routes(
                    project_id,fingerprint,finding_code,discipline,assignee_email,assignee_name,priority,sla_hours,
                    title,detail,source_ref,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        workspace_id,
                        fingerprint,
                        finding_code,
                        detected,
                        selected_email,
                        selected_name,
                        priority,
                        sla,
                        title,
                        detail,
                        source_ref,
                        route_status,
                        stamp,
                        stamp,
                    ),
                )

        task = None
        if create_task and selected_email:
            task = self.base.create_work_task(
                project_id=workspace_id,
                actor=actor,
                title=title or f"AI xử lý {finding_code}",
                description=detail or f"Finding {finding_code} do AI Supervisor xác minh.",
                assignee_email=selected_email,
                assignee_name=selected_name or selected_email,
                priority=priority,
                due_at=(datetime.now() + timedelta(hours=sla)).strftime("%Y-%m-%d %H:%M:%S"),
                source_module="AI_SUPERVISOR",
                source_type="AI_FINDING",
                source_id=fingerprint,
                source_code=finding_code,
                source_title=title,
            )
        return {
            "project_id": workspace_id,
            "fingerprint": fingerprint,
            "discipline": detected,
            "assignee_email": selected_email,
            "assignee_name": selected_name,
            "priority": priority,
            "sla_hours": sla,
            "status": route_status,
            "candidate_count": len(candidates),
            "task_created": bool(task),
            "task": task,
        }

    @staticmethod
    def _date_tokens(text: str) -> list[date]:
        out: list[date] = []
        for raw in re.findall(r"\b(?:\d{1,2}[/-]\d{1,2}[/-]\d{4}|\d{4}-\d{2}-\d{2})\b", str(text or "")):
            parsed = _parse_date(raw)
            if parsed and parsed not in out:
                out.append(parsed)
        return out

    def _upsert_obligation(self, workspace_id: int, item: dict[str, Any]) -> None:
        key = str(item["obligation_key"])
        stamp = _now()
        with self.db.connect() as connection:
            old = _one(
                connection,
                "SELECT status,created_at FROM qlda_contract_obligations WHERE project_id=? AND obligation_key=? LIMIT 1",
                (workspace_id, key),
            )
            status = str(old.get("status") or item.get("status") or "OPEN")
            if status.upper() not in {"CLOSED", "DONE", "WAIVED"}:
                status = str(item.get("status") or "OPEN")
            connection.execute(
                "DELETE FROM qlda_contract_obligations WHERE project_id=? AND obligation_key=?",
                (workspace_id, key),
            )
            connection.execute(
                """INSERT INTO qlda_contract_obligations(
                project_id,obligation_key,source_type,source_id,source_ref,title,responsible,due_date,
                remind_days,status,evidence,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    workspace_id,
                    key,
                    item.get("source_type", ""),
                    str(item.get("source_id") or ""),
                    item.get("source_ref", ""),
                    item.get("title", ""),
                    item.get("responsible", ""),
                    item.get("due_date", ""),
                    int(item.get("remind_days") or 30),
                    status,
                    item.get("evidence", ""),
                    str(old.get("created_at") or stamp),
                    stamp,
                ),
            )

    def audit_contract_obligations(self, *, project_id: int, actor: str = "", **_: Any) -> dict[str, Any]:
        _scope, workspace_id = self._scope(project_id)
        today = date.today()
        obligations: list[dict[str, Any]] = []
        try:
            from qlda.runtime_core import contract_management as cm

            cm.ensure_schema(self.db)
        except Exception:
            cm = None

        with self.db.connect() as connection:
            if _table_exists(connection, "project_contract_records"):
                records = _rows(
                    connection,
                    """SELECT id,record_type,record_no,title,effective_date,expiry_date,note
                    FROM project_contract_records WHERE workspace_project_id=? ORDER BY id""",
                    (workspace_id,),
                )
                for row in records:
                    expiry = _parse_date(row.get("expiry_date"))
                    if expiry:
                        obligations.append(
                            {
                                "obligation_key": f"CONTRACT_EXPIRY:{row.get('id')}",
                                "source_type": "CONTRACT",
                                "source_id": row.get("id"),
                                "source_ref": str(row.get("record_no") or ""),
                                "title": f"Theo dõi hết hiệu lực {row.get('record_type') or 'Hợp đồng'} {row.get('record_no') or ''}".strip(),
                                "responsible": "BĐH/QS/Contract",
                                "due_date": expiry.isoformat(),
                                "remind_days": 30,
                                "status": "OPEN",
                                "evidence": f"project_contract_records.expiry_date={expiry.isoformat()}",
                            }
                        )
                    note = str(row.get("note") or "")
                    note_plain = _norm(note)
                    if any(term in note_plain for term in ("bao lanh", "bao hiem", "gia han", "phan hoi", "thoi han")):
                        for idx, due in enumerate(self._date_tokens(note), start=1):
                            obligations.append(
                                {
                                    "obligation_key": f"CONTRACT_NOTE:{row.get('id')}:{idx}:{due.isoformat()}",
                                    "source_type": "CONTRACT_NOTE",
                                    "source_id": row.get("id"),
                                    "source_ref": str(row.get("record_no") or ""),
                                    "title": f"Mốc nghĩa vụ ghi trong {row.get('record_no') or 'hợp đồng'}",
                                    "responsible": "BĐH/QS/Contract",
                                    "due_date": due.isoformat(),
                                    "remind_days": 30,
                                    "status": "OPEN",
                                    "evidence": note[:1000],
                                }
                            )

            if _table_exists(connection, "documents"):
                docs = _rows(
                    connection,
                    """SELECT id,doc_type,code,subject,assignee,due_date,status,description,note
                    FROM documents WHERE project_id=? AND due_date<>''""",
                    (workspace_id,),
                )
                terms = ("bao lanh", "bao hiem", "hop dong", "phu luc", "gia han", "cong van", "phan hoi", "nghia vu")
                for row in docs:
                    text = _norm(" ".join(str(row.get(k) or "") for k in ("subject", "description", "note", "doc_type")))
                    due = _parse_date(row.get("due_date"))
                    if not due or not any(term in text for term in terms):
                        continue
                    obligations.append(
                        {
                            "obligation_key": f"DOC_DUE:{row.get('id')}:{due.isoformat()}",
                            "source_type": "DOCUMENT",
                            "source_id": row.get("id"),
                            "source_ref": str(row.get("code") or ""),
                            "title": str(row.get("subject") or row.get("code") or "Mốc hồ sơ hợp đồng"),
                            "responsible": str(row.get("assignee") or "BĐH"),
                            "due_date": due.isoformat(),
                            "remind_days": 14,
                            "status": "CLOSED" if _norm(row.get("status")) in {"dong", "hoan thanh", "da duyet", "closed"} else "OPEN",
                            "evidence": f"documents#{row.get('id')} due_date={due.isoformat()}",
                        }
                    )

            if not obligations and _table_exists(connection, "projects"):
                project = _one(connection, "SELECT id,code,end_date FROM projects WHERE id=?", (workspace_id,))
                due = _parse_date(project.get("end_date"))
                if due:
                    obligations.append(
                        {
                            "obligation_key": f"PROJECT_END:{workspace_id}",
                            "source_type": "PROJECT",
                            "source_id": workspace_id,
                            "source_ref": str(project.get("code") or ""),
                            "title": "Mốc kết thúc dự án/hợp đồng trong workspace",
                            "responsible": "BĐH",
                            "due_date": due.isoformat(),
                            "remind_days": 30,
                            "status": "OPEN",
                            "evidence": f"projects.end_date={due.isoformat()}",
                        }
                    )

        for item in obligations:
            self._upsert_obligation(workspace_id, item)

        with self.db.connect() as connection:
            ledger = _rows(
                connection,
                "SELECT * FROM qlda_contract_obligations WHERE project_id=? ORDER BY due_date,id",
                (workspace_id,),
            )
        open_rows = [x for x in ledger if str(x.get("status") or "").upper() not in {"CLOSED", "DONE", "WAIVED"}]
        overdue = [x for x in open_rows if (_parse_date(x.get("due_date")) or date.max) < today]
        due_30 = [
            x for x in open_rows
            if (lambda d: d is not None and today <= d <= today + timedelta(days=30))(_parse_date(x.get("due_date")))
        ]
        return {
            "project_id": workspace_id,
            "schema": ADVANCED_SCHEMA_VERSION,
            "obligation_count": len(open_rows),
            "overdue_count": len(overdue),
            "due_30d_count": len(due_30),
            "overdue": overdue[:50],
            "due_30d": due_30[:50],
            "ledger": ledger[:200],
            "actor": actor,
        }

    def reconcile_ipc_boq(
        self,
        *,
        project_id: int,
        actor: str = "",
        claim_id: str = "",
        **_: Any,
    ) -> dict[str, Any]:
        _scope, workspace_id = self._scope(project_id)
        try:
            from qlda.runtime_core import ipc_claim

            with self.db.connect() as connection:
                ipc_claim._ensure_tables(connection)
        except Exception:
            pass

        with self.db.connect() as connection:
            if not _table_exists(connection, "payment_claims") or not _table_exists(connection, "payment_claim_items"):
                return {"project_id": workspace_id, "available": False, "reason": "ipc_tables_missing", "flags": []}
            if claim_id:
                claim = _one(
                    connection,
                    "SELECT * FROM payment_claims WHERE project_id=? AND claim_id=? LIMIT 1",
                    (workspace_id, str(claim_id)),
                )
            else:
                claim = _one(
                    connection,
                    "SELECT * FROM payment_claims WHERE project_id=? ORDER BY updated_at DESC,claim_no DESC LIMIT 1",
                    (workspace_id,),
                )
            if not claim:
                return {"project_id": workspace_id, "available": False, "reason": "no_ipc", "flags": []}
            cid = str(claim.get("claim_id") or "")
            items = _rows(
                connection,
                "SELECT * FROM payment_claim_items WHERE project_id=? AND claim_id=? ORDER BY row_no",
                (workspace_id, cid),
            )
            boq = _rows(
                connection,
                "SELECT id,task_ref,boq_item,quantity,unit,unit_price,budget_total,note FROM cost_budgets WHERE project_id=? ORDER BY id",
                (workspace_id,),
            ) if _table_exists(connection, "cost_budgets") else []

        by_name: dict[str, list[dict[str, Any]]] = {}
        for row in boq:
            by_name.setdefault(_norm(row.get("boq_item")), []).append(row)

        flags: list[dict[str, Any]] = []
        seen: dict[str, int] = {}
        for item in items:
            name_key = _norm(item.get("boq_item"))
            unit_key = _norm(item.get("unit"))
            item_key = f"{name_key}|{unit_key}"
            seen[item_key] = seen.get(item_key, 0) + 1
            matches = list(by_name.get(name_key) or [])
            exact_unit = [x for x in matches if _norm(x.get("unit")) == unit_key]
            candidate_pool = exact_unit or matches
            candidate = None
            if candidate_pool:
                claim_qty = _float(item.get("contract_qty"))
                candidate = min(candidate_pool, key=lambda x: abs(_float(x.get("quantity")) - claim_qty))

            claim_qty = _float(item.get("contract_qty"))
            claim_price = _float(item.get("material_unit_price")) + _float(item.get("labor_unit_price"))
            if claim_price <= 0 and claim_qty > 0:
                claim_price = _float(item.get("contract_amount")) / claim_qty
            cumulative = _float(item.get("cumulative_value"))

            def add(code: str, severity: str, detail: str, boq_row: dict[str, Any] | None = None) -> None:
                b = boq_row or {}
                flags.append(
                    {
                        "flag_code": code,
                        "severity": severity,
                        "detail": detail,
                        "item_key": item_key,
                        "boq_id": int(b.get("id") or 0),
                        "claim_row": int(item.get("row_no") or 0),
                        "claim_qty": claim_qty,
                        "boq_qty": _float(b.get("quantity")),
                        "claim_unit_price": claim_price,
                        "boq_unit_price": _float(b.get("unit_price")),
                        "claim_cumulative_value": cumulative,
                        "boq_budget_total": _float(b.get("budget_total")),
                    }
                )

            if not candidate:
                add("UNMATCHED_IPC_ITEM", "high", f"Không tìm thấy BOQ khớp cho '{item.get('boq_item')}' ({item.get('unit')}).")
                continue

            boq_qty = _float(candidate.get("quantity"))
            boq_price = _float(candidate.get("unit_price"))
            boq_total = _float(candidate.get("budget_total")) or boq_qty * boq_price
            if matches and not exact_unit and unit_key and _norm(candidate.get("unit")) != unit_key:
                add(
                    "UNIT_MISMATCH",
                    "high",
                    f"Đơn vị IPC='{item.get('unit')}' khác BOQ='{candidate.get('unit')}' cho '{item.get('boq_item')}'.",
                    candidate,
                )
            qty_tol = max(1e-6, abs(boq_qty) * 0.01)
            if abs(claim_qty - boq_qty) > qty_tol:
                add(
                    "CONTRACT_QTY_MISMATCH",
                    "high" if claim_qty > boq_qty else "medium",
                    f"Khối lượng hợp đồng trong IPC={claim_qty:,.4f} khác BOQ={boq_qty:,.4f}.",
                    candidate,
                )
            if claim_price > 0 and boq_price > 0:
                price_tol = max(1.0, abs(boq_price) * 0.01)
                if abs(claim_price - boq_price) > price_tol:
                    add(
                        "UNIT_PRICE_MISMATCH",
                        "high",
                        f"Đơn giá IPC={claim_price:,.2f} khác BOQ={boq_price:,.2f}.",
                        candidate,
                    )
            if boq_total > 0 and cumulative > boq_total * 1.005:
                add(
                    "CUMULATIVE_EXCEEDS_BOQ",
                    "critical",
                    f"Giá trị lũy kế IPC={cumulative:,.0f} vượt ngân sách BOQ={boq_total:,.0f}.",
                    candidate,
                )
            ratio = _float(item.get("completion_ratio"))
            normalized_ratio = ratio / 100.0 if ratio > 2 else ratio
            if normalized_ratio > 1.005:
                add(
                    "COMPLETION_OVER_100",
                    "critical",
                    f"Tỷ lệ hoàn thành của dòng IPC vượt 100% ({ratio:,.2f}).",
                    candidate,
                )

        for item_key, count in seen.items():
            if item_key.strip("|") and count > 1:
                flags.append(
                    {
                        "flag_code": "DUPLICATE_CLAIM_ITEM",
                        "severity": "low",
                        "detail": f"Một tên công tác/đơn vị xuất hiện {count} lần trong cùng IPC; cần kiểm tra có phải tách khu vực hợp lệ hay trùng dòng.",
                        "item_key": item_key,
                        "boq_id": 0,
                        "claim_row": 0,
                        "claim_qty": 0,
                        "boq_qty": 0,
                        "claim_unit_price": 0,
                        "boq_unit_price": 0,
                        "claim_cumulative_value": 0,
                        "boq_budget_total": 0,
                    }
                )

        claim_code = str(claim.get("claim_code") or claim.get("claim_no") or "")
        with self.db.connect() as connection:
            connection.execute(
                "DELETE FROM qlda_ipc_boq_reconciliation WHERE project_id=? AND claim_id=?",
                (workspace_id, str(claim.get("claim_id") or "")),
            )
            for flag in flags:
                connection.execute(
                    """INSERT INTO qlda_ipc_boq_reconciliation(
                    project_id,claim_id,claim_code,item_key,boq_id,claim_row,flag_code,severity,detail,
                    claim_qty,boq_qty,claim_unit_price,boq_unit_price,claim_cumulative_value,boq_budget_total,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        workspace_id,
                        str(claim.get("claim_id") or ""),
                        claim_code,
                        flag.get("item_key", ""),
                        int(flag.get("boq_id") or 0),
                        int(flag.get("claim_row") or 0),
                        flag.get("flag_code", ""),
                        flag.get("severity", "medium"),
                        flag.get("detail", ""),
                        _float(flag.get("claim_qty")),
                        _float(flag.get("boq_qty")),
                        _float(flag.get("claim_unit_price")),
                        _float(flag.get("boq_unit_price")),
                        _float(flag.get("claim_cumulative_value")),
                        _float(flag.get("boq_budget_total")),
                        _now(),
                    ),
                )

        severity_order = {"critical": 4, "high": 3, "medium": 2, "low": 1}
        flags.sort(key=lambda x: severity_order.get(str(x.get("severity")), 0), reverse=True)
        high_count = sum(1 for x in flags if str(x.get("severity")) in {"high", "critical"})
        return {
            "project_id": workspace_id,
            "available": True,
            "claim_id": str(claim.get("claim_id") or ""),
            "claim_code": claim_code,
            "ipc_item_count": len(items),
            "boq_item_count": len(boq),
            "flag_count": len(flags),
            "high_flag_count": high_count,
            "clean": len(flags) == 0,
            "flags": flags[:500],
            "actor": actor,
        }

    def _match_boq_rate(self, workspace_id: int, item_name: str, unit: str) -> dict[str, Any]:
        with self.db.connect() as connection:
            rows = _rows(
                connection,
                "SELECT id,boq_item,quantity,unit,unit_price,budget_total FROM cost_budgets WHERE project_id=?",
                (workspace_id,),
            ) if _table_exists(connection, "cost_budgets") else []
        name = _norm(item_name)
        unit_norm = _norm(unit)
        exact = [x for x in rows if _norm(x.get("boq_item")) == name and (not unit_norm or _norm(x.get("unit")) == unit_norm)]
        if not exact:
            exact = [x for x in rows if _norm(x.get("boq_item")) == name]
        return dict(exact[0]) if exact else {}

    def draft_vo_from_change(
        self,
        *,
        project_id: int,
        actor: str = "",
        source_type: str = "CHANGE",
        source_ref: str = "",
        source_document_id: int = 0,
        item_name: str = "",
        unit: str = "",
        old_qty: float = 0,
        new_qty: float = 0,
        unit_price: float = 0,
        note: str = "",
        **_: Any,
    ) -> dict[str, Any]:
        _scope, workspace_id = self._scope(project_id)
        evidence: dict[str, Any] = {"note": note}
        document_cost = 0.0
        if int(source_document_id or 0) > 0:
            with self.db.connect() as connection:
                doc = _one(
                    connection,
                    "SELECT * FROM documents WHERE project_id=? AND id=? LIMIT 1",
                    (workspace_id, int(source_document_id)),
                )
            if not doc:
                raise ValueError("Không tìm thấy RFI/RFA/hồ sơ thay đổi trong workspace nhà thầu hiện tại.")
            item_name = item_name or str(doc.get("subject") or doc.get("code") or "Thay đổi thiết kế")
            source_type = source_type or str(doc.get("doc_type") or "DOCUMENT")
            source_ref = source_ref or str(doc.get("code") or doc.get("id") or "")
            document_cost = _float(doc.get("cost_impact"))
            evidence["document"] = {
                "id": doc.get("id"),
                "code": doc.get("code"),
                "doc_type": doc.get("doc_type"),
                "cost_impact": document_cost,
                "time_impact_days": doc.get("time_impact_days"),
            }

        boq = self._match_boq_rate(workspace_id, item_name, unit)
        rate = _float(unit_price)
        rate_source = "INPUT"
        if rate <= 0 and boq:
            rate = _float(boq.get("unit_price"))
            rate_source = "BOQ"
        delta = _float(new_qty) - _float(old_qty)
        estimated = delta * rate if rate > 0 and abs(delta) > 0 else document_cost
        if rate <= 0 and document_cost == 0 and abs(delta) > 0:
            raise ValueError("Không tìm thấy đơn giá BOQ và chưa có đơn giá thay đổi để lập draft VO.")

        raw = f"{workspace_id}|{source_type}|{source_ref}|{_norm(item_name)}|{old_qty}|{new_qty}|{rate}|{estimated}"
        draft_code = "AI-VO-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12].upper()
        evidence.update({"boq_match": boq, "rate_source": rate_source})
        with self.db.connect() as connection:
            connection.execute(
                "DELETE FROM qlda_vo_drafts WHERE project_id=? AND draft_code=?",
                (workspace_id, draft_code),
            )
            connection.execute(
                """INSERT INTO qlda_vo_drafts(
                project_id,draft_code,source_type,source_ref,item_name,unit,old_qty,new_qty,delta_qty,
                unit_price,estimated_amount,status,evidence,created_by,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    workspace_id,
                    draft_code,
                    source_type,
                    source_ref,
                    item_name,
                    unit or str(boq.get("unit") or ""),
                    _float(old_qty),
                    _float(new_qty),
                    delta,
                    rate,
                    estimated,
                    "DRAFT",
                    json.dumps(evidence, ensure_ascii=False, default=str),
                    actor,
                    _now(),
                ),
            )
        return {
            "project_id": workspace_id,
            "draft": True,
            "draft_code": draft_code,
            "source_type": source_type,
            "source_ref": source_ref,
            "item_name": item_name,
            "unit": unit or str(boq.get("unit") or ""),
            "old_qty": _float(old_qty),
            "new_qty": _float(new_qty),
            "delta_qty": delta,
            "unit_price": rate,
            "rate_source": rate_source,
            "estimated_amount": estimated,
            "approval_required": True,
            "note": "Đây chỉ là bản nháp AI. Không ghi vào variation_orders và không thay đổi hợp đồng cho tới khi workflow VO được phê duyệt.",
        }

    def forecast_project_risk(
        self,
        *,
        project_id: int,
        actor: str = "",
        horizon_days: int = 60,
        **_: Any,
    ) -> dict[str, Any]:
        _scope, workspace_id = self._scope(project_id)
        horizon = max(7, min(180, int(horizon_days or 60)))
        today = date.today()
        tasks = [_rowdict(x) for x in self.db.tasks(workspace_id)]
        detail = [x for x in tasks if not int(x.get("is_summary") or 0)] or tasks
        active = [x for x in detail if _float(x.get("actual_progress")) < 100]
        forecasts: list[dict[str, Any]] = []
        usable = 0
        max_delay = 0
        stalled = 0
        for task in active:
            start = _parse_date(task.get("start_date"))
            finish = _parse_date(task.get("end_date"))
            actual = max(0.0, min(100.0, _float(task.get("actual_progress"))))
            planned = max(0.0, min(100.0, _float(task.get("planned_progress"))))
            if not start or not finish:
                continue
            elapsed = max(1, (today - start).days + 1) if today >= start else 0
            observed_rate = actual / elapsed if elapsed > 0 and actual > 0 else 0.0
            predicted = None
            delay_days = 0
            if actual >= 100:
                predicted = _parse_date(task.get("actual_finish_date")) or today
            elif observed_rate > 0:
                usable += 1
                remaining = max(0.0, 100.0 - actual)
                days_left = int(math.ceil(remaining / observed_rate))
                predicted = today + timedelta(days=days_left)
                delay_days = max(0, (predicted - finish).days)
            elif today > finish:
                stalled += 1
                delay_days = (today - finish).days
            max_delay = max(max_delay, delay_days)
            forecasts.append(
                {
                    "task_id": int(task.get("id") or 0),
                    "wbs": str(task.get("wbs") or ""),
                    "name": str(task.get("name") or ""),
                    "critical": bool(int(task.get("critical") or 0)),
                    "planned_progress": planned,
                    "actual_progress": actual,
                    "observed_rate_pct_per_day": round(observed_rate, 4),
                    "baseline_finish": finish.isoformat(),
                    "predicted_finish": predicted.isoformat() if predicted else "",
                    "predicted_delay_days": int(delay_days),
                }
            )

        status = self.base.get_project_status(project_id=workspace_id, actor=actor)
        schedule = dict(status.get("schedule") or {})
        current_delay = _float(schedule.get("delay_percent"))
        critical_delayed = int(schedule.get("critical_delayed_tasks") or 0)
        risk_score = min(100.0, current_delay * 3.0 + max_delay * 1.2 + critical_delayed * 8.0 + stalled * 4.0)
        coverage = usable / max(1, len(active))
        confidence = min(0.75, 0.35 + coverage * 0.40) if active else 0.25
        predicted_finish = ""
        predicted_dates = [_parse_date(x.get("predicted_finish")) for x in forecasts if x.get("predicted_finish")]
        predicted_dates = [x for x in predicted_dates if x is not None]
        if predicted_dates:
            predicted_finish = max(predicted_dates).isoformat()

        cash_30 = 0.0
        cash_60 = 0.0
        cash_rows = 0
        try:
            from qlda.runtime_core.finance_management_ui import load_unpaid_ipcs

            unpaid = [dict(x) for x in load_unpaid_ipcs(self.db, workspace_id)]
            for row in unpaid:
                due = _parse_date(row.get("due_date"))
                if not due:
                    continue
                amount = max(0.0, _float(row.get("outstanding")))
                if today <= due <= today + timedelta(days=30):
                    cash_30 += amount
                if today <= due <= today + timedelta(days=60):
                    cash_60 += amount
                cash_rows += 1
        except Exception:
            pass

        payload = {
            "method": "deterministic_task_velocity_v1",
            "horizon_days": horizon,
            "active_task_count": len(active),
            "velocity_task_count": usable,
            "max_predicted_delay_days": int(max_delay),
            "stalled_task_count": stalled,
            "critical_delayed_tasks": critical_delayed,
            "current_schedule_delay_percent": current_delay,
            "tasks": sorted(forecasts, key=lambda x: (bool(x["critical"]), int(x["predicted_delay_days"])), reverse=True)[:100],
            "verified_cash_due_30d": cash_30,
            "verified_cash_due_60d": cash_60,
            "verified_cash_due_row_count": cash_rows,
            "cash_note": "Các giá trị tiền chỉ gồm IPC đã tồn tại có due_date thật; không dự báo IPC tương lai và không đưa vào Project Health như nợ quá hạn.",
        }
        with self.db.connect() as connection:
            connection.execute(
                """INSERT INTO qlda_ai_forecasts(project_id,forecast_type,horizon_days,predicted_finish,risk_score,confidence,payload,created_at)
                VALUES(?,?,?,?,?,?,?,?)""",
                (
                    workspace_id,
                    "SCHEDULE_RISK",
                    horizon,
                    predicted_finish,
                    risk_score,
                    confidence,
                    json.dumps(payload, ensure_ascii=False, default=str),
                    _now(),
                ),
            )
        return {
            "project_id": workspace_id,
            "forecast_type": "SCHEDULE_RISK",
            "method": payload["method"],
            "horizon_days": horizon,
            "risk_score": round(risk_score, 1),
            "confidence": round(confidence, 3),
            "predicted_finish": predicted_finish,
            "max_predicted_delay_days": int(max_delay),
            "verified_cash_due_30d": cash_30,
            "verified_cash_due_60d": cash_60,
            "tasks": payload["tasks"],
            "warning": "Dự báo tiến độ là ngoại suy velocity có kiểm chứng, không phải cam kết. Confidence bị giới hạn cho tới khi có đủ lịch sử snapshot thực tế.",
        }

    @staticmethod
    def _parse_json_object(text: str) -> dict[str, Any]:
        raw = str(text or "").strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
            raw = re.sub(r"\s*```$", "", raw)
        try:
            data = json.loads(raw)
            return dict(data) if isinstance(data, dict) else {}
        except Exception:
            match = re.search(r"\{.*\}", raw, re.S)
            if not match:
                return {}
            try:
                data = json.loads(match.group(0))
                return dict(data) if isinstance(data, dict) else {}
            except Exception:
                return {}

    def _vision_from_attachment(
        self,
        workspace_id: int,
        attachment_id: int,
        *,
        object_label: str,
        planned_qty: float,
    ) -> dict[str, Any]:
        with self.db.connect() as connection:
            row = _one(
                connection,
                """SELECT a.id,a.file_name,a.mime_type,a.file_content,d.code,d.subject
                FROM document_attachments a JOIN documents d ON d.id=a.document_id
                WHERE a.id=? AND d.project_id=? LIMIT 1""",
                (int(attachment_id), workspace_id),
            )
        if not row:
            raise ValueError("Không tìm thấy ảnh hiện trường trong workspace hiện tại.")
        content = row.get("file_content")
        if isinstance(content, memoryview):
            content = bytes(content)
        if not isinstance(content, (bytes, bytearray)) or not content:
            raise ValueError("Ảnh hiện trường chưa có byte dữ liệu trong DB để AI Vision đọc trực tiếp.")
        data = bytes(content)
        mime = str(row.get("mime_type") or "").strip().lower()
        suffix = Path(str(row.get("file_name") or "image.jpg")).suffix.lower()
        if not mime:
            mime = {".png": "image/png", ".webp": "image/webp", ".jpeg": "image/jpeg", ".jpg": "image/jpeg"}.get(suffix, "image/jpeg")
        if not mime.startswith("image/"):
            raise ValueError("Site Vision hiện chỉ nhận attachment hình ảnh PNG/JPEG/WebP.")

        from qlda.runtime_core.settings_store import get_ai_runtime_settings
        from qlda.runtime_core.ai_service import AISettings, GeminiProjectAssistant, GeminiSettings, OpenAIProjectAssistant

        settings = dict(get_ai_runtime_settings() or {})
        api_key = str(settings.get("api_key") or "").strip()
        if not api_key:
            raise RuntimeError("Chưa cấu hình API key cho AI Vision.")
        provider = str(settings.get("provider") or "openai").strip().lower()
        prompt = f"""Bạn là Site Vision của hệ thống QLDA xây dựng.
Chỉ quan sát đúng ảnh được cung cấp. Mục tiêu: ước lượng số lượng đối tượng '{object_label or 'cấu kiện/công tác'}'.
Số lượng kế hoạch tham chiếu: {planned_qty}.
Không suy đoán nếu đối tượng không nhìn đủ rõ. Trả về DUY NHẤT JSON:
{{"observed_quantity": number, "confidence": number_0_to_1, "notes": "bằng chứng nhìn thấy/nguyên nhân không chắc chắn"}}
Nếu không thể đếm đáng tin cậy, observed_quantity phải là null và confidence thấp.
"""
        if provider == "gemini":
            from google.genai import types

            assistant = GeminiProjectAssistant(Path("."), GeminiSettings(api_key=api_key, model=str(settings.get("model") or "auto"), use_web=False))
            client = assistant._client()
            try:
                part = types.Part.from_bytes(data=data, mime_type=mime)
                response = assistant._generate_content_with_fallback(client, contents=[part, prompt])
                text = str(getattr(response, "text", "") or "")
            finally:
                try:
                    client.close()
                except Exception:
                    pass
        else:
            assistant = OpenAIProjectAssistant(Path("."), AISettings(api_key=api_key, model=str(settings.get("model") or "gpt-5-mini"), use_web=False))
            client = assistant._client()
            try:
                encoded = base64.b64encode(data).decode("ascii")
                response = client.responses.create(
                    model=assistant.model,
                    store=False,
                    input=[
                        {
                            "role": "user",
                            "content": [
                                {"type": "input_image", "image_url": f"data:{mime};base64,{encoded}"},
                                {"type": "input_text", "text": prompt},
                            ],
                        }
                    ],
                )
                text = str(getattr(response, "output_text", "") or "")
            finally:
                try:
                    client.close()
                except Exception:
                    pass
        result = self._parse_json_object(text)
        result["evidence_ref"] = f"document_attachment:{attachment_id}:{row.get('file_name') or ''}"
        result["source_document"] = str(row.get("code") or row.get("subject") or "")
        return result

    def analyze_site_progress(
        self,
        *,
        project_id: int,
        actor: str = "",
        task_id: int = 0,
        object_label: str = "",
        planned_quantity: float = 0,
        observed_quantity: float | None = None,
        confidence: float = 0,
        evidence_ref: str = "",
        notes: str = "",
        attachment_id: int = 0,
        **_: Any,
    ) -> dict[str, Any]:
        _scope, workspace_id = self._scope(project_id)
        task = _rowdict(self.db.task(int(task_id))) if int(task_id or 0) > 0 else {}
        if task and int(task.get("project_id") or 0) != workspace_id:
            raise PermissionError("Task không thuộc AI tenant nhà thầu hiện tại.")
        planned = _float(planned_quantity)
        if planned <= 0:
            raise ValueError("planned_quantity phải lớn hơn 0 để Site Vision đề xuất tiến độ.")
        observed = observed_quantity
        conf = max(0.0, min(1.0, _float(confidence)))
        if observed is None and int(attachment_id or 0) > 0:
            vision = self._vision_from_attachment(
                workspace_id,
                int(attachment_id),
                object_label=object_label or str(task.get("name") or ""),
                planned_qty=planned,
            )
            if vision.get("observed_quantity") is None:
                raise ValueError("AI Vision không đủ tin cậy để định lượng từ ảnh; không tạo đề xuất tiến độ.")
            observed = _float(vision.get("observed_quantity"))
            conf = max(0.0, min(1.0, _float(vision.get("confidence"))))
            evidence_ref = evidence_ref or str(vision.get("evidence_ref") or "")
            notes = notes or str(vision.get("notes") or "")
        if observed is None:
            raise ValueError("Cần observed_quantity hoặc attachment_id ảnh hiện trường.")
        observed_value = max(0.0, _float(observed))
        proposed = max(0.0, min(100.0, observed_value * 100.0 / planned))
        current = _float(task.get("actual_progress")) if task else 0.0
        with self.db.connect() as connection:
            connection.execute(
                """INSERT INTO qlda_site_vision_observations(
                project_id,task_id,evidence_ref,object_label,observed_qty,planned_qty,proposed_progress,confidence,status,notes,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    workspace_id,
                    int(task_id or 0),
                    evidence_ref,
                    object_label or str(task.get("name") or ""),
                    observed_value,
                    planned,
                    proposed,
                    conf,
                    "PROPOSED",
                    notes,
                    _now(),
                ),
            )
        return {
            "project_id": workspace_id,
            "task_id": int(task_id or 0),
            "object_label": object_label or str(task.get("name") or ""),
            "observed_quantity": observed_value,
            "planned_quantity": planned,
            "proposed_progress": round(proposed, 2),
            "current_progress": current,
            "delta_progress": round(proposed - current, 2),
            "confidence": round(conf, 3),
            "evidence_ref": evidence_ref,
            "notes": notes,
            "status": "PROPOSED",
            "approval_required_for_update": True,
            "safety": "Site Vision không tự cập nhật tasks.actual_progress. Muốn ghi tiến độ phải gọi update_schedule_progress qua Approval Gate.",
        }

    def collect_supervisor_data(self, *, project_id: int, actor: str = "AI Supervisor") -> dict[str, Any]:
        _scope, workspace_id = self._scope(project_id)
        contract = self.audit_contract_obligations(project_id=workspace_id, actor=actor)
        reconciliation = self.reconcile_ipc_boq(project_id=workspace_id, actor=actor)
        forecast = self.forecast_project_risk(project_id=workspace_id, actor=actor, horizon_days=60)
        indicators = {
            "contract_obligations_overdue": int(contract.get("overdue_count") or 0),
            "contract_obligations_due_30d": int(contract.get("due_30d_count") or 0),
            "ipc_reconciliation_high_flags": int(reconciliation.get("high_flag_count") or 0),
            "forecast_risk_score": _float(forecast.get("risk_score")),
            "forecast_confidence": _float(forecast.get("confidence")),
            "forecast_max_delay_days": int(forecast.get("max_predicted_delay_days") or 0),
        }
        return {
            "project_id": workspace_id,
            "schema": ADVANCED_SCHEMA_VERSION,
            "indicators": indicators,
            "contract_audit": contract,
            "ipc_boq_reconciliation": reconciliation,
            "forecast": forecast,
        }

    def route_findings(self, project_id: int, findings: list[dict[str, Any]], *, actor: str = "AI Supervisor") -> list[dict[str, Any]]:
        routes: list[dict[str, Any]] = []
        for item in findings:
            try:
                routes.append(
                    self.route_work_task(
                        project_id=project_id,
                        actor=actor,
                        finding_code=str(item.get("code") or ""),
                        title=str(item.get("title") or item.get("code") or "AI finding"),
                        detail=str(item.get("detail") or ""),
                        severity=str(item.get("severity") or "medium"),
                        source_ref=str(item.get("code") or ""),
                        create_task=False,
                    )
                )
            except Exception as exc:
                routes.append({"finding_code": item.get("code"), "status": "ROUTE_FAILED", "error": str(exc)})
        return routes

    def run_advanced_supervision(self, *, project_id: int, actor: str = "AI Supervisor", **_: Any) -> dict[str, Any]:
        return self.collect_supervisor_data(project_id=project_id, actor=actor)

    def handlers(self) -> dict[str, Any]:
        return {
            "route_work_task": self.route_work_task,
            "audit_contract_obligations": self.audit_contract_obligations,
            "reconcile_ipc_boq": self.reconcile_ipc_boq,
            "draft_vo_from_change": self.draft_vo_from_change,
            "forecast_project_risk": self.forecast_project_risk,
            "analyze_site_progress": self.analyze_site_progress,
            "run_advanced_supervision": self.run_advanced_supervision,
        }


def build_advanced_automation(base_adapter) -> AdvancedAutomation:
    return AdvancedAutomation(base_adapter)


__all__ = [
    "ADVANCED_SCHEMA_VERSION",
    "AdvancedAutomation",
    "advanced_tool_specs",
    "build_advanced_automation",
    "ensure_advanced_schema",
]
