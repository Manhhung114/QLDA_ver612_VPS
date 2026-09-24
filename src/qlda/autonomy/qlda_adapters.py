from __future__ import annotations

from datetime import date, datetime
from typing import Any, Callable

from qlda.infrastructure.contractor_data_hub import ContractorDataHubRepository
from qlda.runtime_core.work_tasks_v1 import create_work_task

from .supervisor import ProjectSupervisor


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


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


class QLDAAutomationAdapters:
    """Concrete V7.8 adapters over existing QLDA business services.

    AI tenant boundary: every operational tool runs inside exactly one contractor
    workspace. Project-wide aggregation is not an implicit fallback. A caller must
    deliberately use a separate Project Control path for cross-contractor analysis.
    """

    def __init__(
        self,
        db,
        *,
        google_sync: Callable[..., Any] | None = None,
        notify: Callable[..., Any] | None = None,
        approve_ipc: Callable[..., Any] | None = None,
        approve_vo: Callable[..., Any] | None = None,
    ) -> None:
        self.db = db
        self.google_sync = google_sync
        self.notify = notify
        self.approve_ipc_callback = approve_ipc
        self.approve_vo_callback = approve_vo
        self.supervisor = ProjectSupervisor()

    def _resolve_scope(self, project_id: int) -> dict[str, Any]:
        """Resolve one AI tenant from a contractor workspace project id.

        Standalone projects without contractor rows remain supported. If a master
        project owns contractor workspaces but is not itself one of them, implicit
        aggregate AI is rejected so callers cannot accidentally mix contractors.
        """
        pid = int(project_id)
        default = {
            "master_project_id": pid,
            "workspace_project_id": pid,
            "contractor_id": 0,
            "contractor_code": "",
            "contractor_name": "",
            "standalone": True,
        }
        try:
            with self.db.connect() as connection:
                row = connection.execute(
                    """SELECT id,master_project_id,workspace_project_id,contractor_code,contractor_name,status
                    FROM project_contractors WHERE workspace_project_id=? LIMIT 1""",
                    (pid,),
                ).fetchone()
                item = _rowdict(row)
                if item:
                    return {
                        "master_project_id": int(item.get("master_project_id") or pid),
                        "workspace_project_id": int(item.get("workspace_project_id") or pid),
                        "contractor_id": int(item.get("id") or 0),
                        "contractor_code": str(item.get("contractor_code") or ""),
                        "contractor_name": str(item.get("contractor_name") or ""),
                        "standalone": False,
                    }
                children = connection.execute(
                    """SELECT workspace_project_id FROM project_contractors
                    WHERE master_project_id=? AND status='Đang hoạt động' LIMIT 2""",
                    (pid,),
                ).fetchall()
        except Exception:
            return default

        if children:
            raise ValueError(
                "AI nhà thầu không được chạy ở phạm vi dự án tổng. "
                "Hãy chọn một Nhà thầu đang làm việc để xác định workspace_project_id."
            )
        return default

    def handlers(self) -> dict[str, Callable[..., Any]]:
        return {
            "get_project_status": self.get_project_status,
            "sync_google_data": self.sync_google_data,
            "check_data_integrity": self.check_data_integrity,
            "create_work_task": self.create_work_task,
            "draft_rfi": self.draft_rfi,
            "draft_ncr": self.draft_ncr,
            "update_schedule_progress": self.update_schedule_progress,
            "approve_document": self.approve_document,
            "approve_ipc": self.approve_ipc,
            "approve_vo": self.approve_vo,
            "close_ncr": self.close_ncr,
            "generate_report": self.generate_report,
            "send_notification": self.send_notification,
        }

    def _payment_status(self, workspace_id: int) -> dict[str, Any]:
        """Return payment facts without inventing an overdue amount.

        ``certified_cumulative`` is cumulative by schema, so summing it across IPC
        records double-counts earlier periods.  For health/risk evaluation, overdue
        payment is taken only from existing IPC rows that have a real due date and
        are still unpaid, using the same finance service as the UI.
        """
        certified_cumulative = 0.0
        paid_total = 0.0
        try:
            with self.db.connect() as connection:
                row = connection.execute(
                    """SELECT COALESCE(MAX(certified_cumulative),0) AS certified_cumulative,
                    COALESCE(SUM(paid_amount),0) AS paid_total
                    FROM payment_tracking WHERE project_id=?""",
                    (int(workspace_id),),
                ).fetchone()
            summary = _rowdict(row)
            certified_cumulative = float(summary.get("certified_cumulative") or 0)
            paid_total = float(summary.get("paid_total") or 0)
        except Exception:
            pass

        unpaid_rows: list[dict[str, Any]] = []
        try:
            from qlda.runtime_core.finance_management_ui import load_unpaid_ipcs

            unpaid_rows = [dict(x) for x in load_unpaid_ipcs(self.db, int(workspace_id))]
        except Exception:
            unpaid_rows = []

        unpaid_balance = sum(max(0.0, float(x.get("outstanding") or 0)) for x in unpaid_rows)
        overdue_rows = [
            x for x in unpaid_rows
            if int(x.get("overdue_days") or 0) > 0 and x.get("due_date") is not None
        ]
        overdue_amount = sum(max(0.0, float(x.get("outstanding") or 0)) for x in overdue_rows)
        dated_count = sum(1 for x in unpaid_rows if x.get("due_date") is not None)

        return {
            # Compatibility fields retained for reports that already consume them.
            "certified": certified_cumulative,
            "paid": paid_total,
            "outstanding": unpaid_balance,
            # Only this field is allowed to drive PAYMENT_OVERDUE findings.
            "overdue": overdue_amount,
            "overdue_count": len(overdue_rows),
            "due_dated_unpaid_count": dated_count,
            "unpaid_ipc_count": len(unpaid_rows),
            "overdue_verified": bool(overdue_rows),
            "calculation": "verified_ipc_due_dates",
        }

    def get_project_status(self, *, project_id: int, actor: str = "", **_: Any) -> dict[str, Any]:
        scope = self._resolve_scope(int(project_id))
        workspace_id = int(scope["workspace_project_id"])
        project = _rowdict(self.db.project(workspace_id))
        if not project:
            raise ValueError(f"Không tìm thấy workspace dự án {workspace_id}.")
        tasks = [_rowdict(x) for x in self.db.tasks(workspace_id)]
        detail_tasks = [x for x in tasks if not int(x.get("is_summary") or 0)] or tasks
        planned = [float(x.get("planned_progress") or 0) for x in detail_tasks]
        actual = [float(x.get("actual_progress") or 0) for x in detail_tasks]
        delayed = [x for x in detail_tasks if str(x.get("status") or "") == "Chậm tiến độ"]
        critical_delayed = [x for x in delayed if int(x.get("critical") or 0)]

        with self.db.connect() as connection:
            doc_rows = connection.execute(
                """SELECT doc_type,status,COUNT(*) AS n FROM documents
                WHERE project_id=? GROUP BY doc_type,status""",
                (workspace_id,),
            ).fetchall()
        documents = [_rowdict(x) for x in doc_rows]

        days_remaining = None
        end_text = str(project.get("end_date") or "")
        if end_text:
            try:
                days_remaining = (datetime.strptime(end_text[:10], "%Y-%m-%d").date() - date.today()).days
            except Exception:
                pass

        planned_avg = _mean(planned)
        actual_avg = _mean(actual)
        return {
            "scope": scope,
            "project": project,
            "schedule": {
                "task_count": len(detail_tasks),
                "planned_progress": round(planned_avg, 2),
                "actual_progress": round(actual_avg, 2),
                "delay_percent": round(max(0.0, planned_avg - actual_avg), 2),
                "delayed_tasks": len(delayed),
                "critical_delayed_tasks": len(critical_delayed),
            },
            "documents": documents,
            "payment": self._payment_status(workspace_id),
            "contract_days_remaining": days_remaining,
            "actor": actor,
        }

    def check_data_integrity(self, *, project_id: int, actor: str = "", **_: Any) -> dict[str, Any]:
        scope = self._resolve_scope(int(project_id))
        master_id = int(scope["master_project_id"])
        workspace_id = int(scope["workspace_project_id"])
        repo = ContractorDataHubRepository(self.db)
        sources = [
            row for row in repo.list_project_sources(master_id)
            if int(row.get("workspace_project_id") or 0) == workspace_id
        ]
        snapshots = [
            row for row in repo.snapshots(master_id, limit=1000)
            if int(row.get("workspace_project_id") or 0) == workspace_id
        ]
        latest_by_source: dict[str, dict[str, Any]] = {}
        for snapshot in snapshots:
            sid = str(snapshot.get("source_id") or "")
            if sid and sid not in latest_by_source:
                latest_by_source[sid] = snapshot

        details: list[dict[str, Any]] = []
        valid = True
        for source in sources:
            sid = str(source.get("source_id") or "")
            if not int(source.get("enabled") or 0):
                continue
            snapshot = latest_by_source.get(sid)
            with self.db.connect() as connection:
                count_row = connection.execute(
                    """SELECT COUNT(*) AS n FROM contractor_data_records
                    WHERE source_id=? AND workspace_project_id=?""",
                    (sid, workspace_id),
                ).fetchone()
            current = int(_rowdict(count_row).get("n") or 0)
            expected = int(snapshot.get("item_count") or 0) if snapshot else None
            source_ok = snapshot is not None and current == expected and not str(source.get("last_error") or "")
            valid = valid and source_ok
            details.append({
                "source_id": sid,
                "name": source.get("name") or "",
                "workspace_project_id": workspace_id,
                "expected_records": expected,
                "stored_records": current,
                "last_sync": source.get("last_sync") or "",
                "last_error": source.get("last_error") or "",
                "valid": source_ok,
            })
        score = 100.0 if valid else round(100.0 * sum(1 for x in details if x["valid"]) / max(1, len(details)), 1)
        return {
            "valid": valid,
            "score": score,
            "sources": details,
            "scope": scope,
            "actor": actor,
        }

    def sync_google_data(self, *, project_id: int, actor: str = "", **arguments: Any) -> Any:
        if not self.google_sync:
            raise RuntimeError("Google sync adapter chưa được cấp OAuth/background sync context.")
        scope = self._resolve_scope(int(project_id))
        master_id = int(scope["master_project_id"])
        workspace_id = int(scope["workspace_project_id"])
        requested = arguments.pop("workspace_ids", None)
        if requested:
            requested_ids = {int(x) for x in requested if int(x) > 0}
            if requested_ids != {workspace_id}:
                raise PermissionError("AI không được đồng bộ chéo sang workspace nhà thầu khác.")
        return self.google_sync(
            project_id=master_id,
            actor=actor,
            workspace_ids=[workspace_id],
            **arguments,
        )

    def create_work_task(self, *, project_id: int, actor: str = "", **arguments: Any) -> dict[str, Any]:
        scope = self._resolve_scope(int(project_id))
        workspace_id = int(scope["workspace_project_id"])
        master_id = int(scope["master_project_id"])
        requested_workspace = int(arguments.pop("workspace_project_id", workspace_id) or workspace_id)
        if requested_workspace != workspace_id:
            raise PermissionError("AI không được tạo công việc sang workspace nhà thầu khác.")
        identity = arguments.pop("identity", None) or {"email": actor, "name": actor, "role": "update"}
        return create_work_task(
            self.db,
            master_project_id=master_id,
            workspace_project_id=workspace_id,
            title=str(arguments.pop("title", "")),
            description=str(arguments.pop("description", "")),
            assignee_email=str(arguments.pop("assignee_email", "")),
            assignee_name=str(arguments.pop("assignee_name", "")),
            priority=str(arguments.pop("priority", "Bình thường")),
            due_at=arguments.pop("due_at", ""),
            actor=identity,
            source_module=str(arguments.pop("source_module", "AI_AUTOMATION")),
            source_type=str(arguments.pop("source_type", "AI")),
            source_id=str(arguments.pop("source_id", "")),
            source_code=str(arguments.pop("source_code", "")),
            source_title=str(arguments.pop("source_title", "")),
        )

    @staticmethod
    def _draft_document(doc_type: str, *, project_id: int, actor: str, **arguments: Any) -> dict[str, Any]:
        return {
            "draft": True,
            "project_id": int(project_id),
            "doc_type": doc_type,
            "code": str(arguments.get("code") or ""),
            "subject": str(arguments.get("subject") or ""),
            "discipline": str(arguments.get("discipline") or ""),
            "contractor": str(arguments.get("contractor") or ""),
            "issuer": str(arguments.get("issuer") or actor),
            "assignee": str(arguments.get("assignee") or ""),
            "due_date": str(arguments.get("due_date") or ""),
            "priority": str(arguments.get("priority") or "Trung bình"),
            "description": str(arguments.get("description") or ""),
            "related_wbs": str(arguments.get("related_wbs") or ""),
            "note": "Bản nháp do AI chuẩn bị; chưa phát hành/chưa thay đổi workflow.",
        }

    def draft_rfi(self, *, project_id: int, actor: str = "", **arguments: Any) -> dict[str, Any]:
        scope = self._resolve_scope(int(project_id))
        return self._draft_document("RFI", project_id=int(scope["workspace_project_id"]), actor=actor, **arguments)

    def draft_ncr(self, *, project_id: int, actor: str = "", **arguments: Any) -> dict[str, Any]:
        scope = self._resolve_scope(int(project_id))
        return self._draft_document("NCR", project_id=int(scope["workspace_project_id"]), actor=actor, **arguments)

    def update_schedule_progress(self, *, project_id: int, actor: str = "", task_id: int, actual_progress: int, **_: Any) -> dict[str, Any]:
        scope = self._resolve_scope(int(project_id))
        workspace_id = int(scope["workspace_project_id"])
        task = _rowdict(self.db.task(int(task_id)))
        if not task or int(task.get("project_id") or 0) != workspace_id:
            raise ValueError("Task không thuộc workspace nhà thầu hiện tại.")
        status, delay_days = self.db.set_actual_override(int(task_id), int(actual_progress))
        return {"task_id": int(task_id), "workspace_project_id": workspace_id, "actual_progress": int(actual_progress), "status": status, "delay_days": delay_days, "actor": actor}

    def approve_document(self, *, project_id: int, actor: str = "", workflow_id: int, stage_code: str, comment: str = "", actor_name: str = "", actor_role: str = "admin", **_: Any) -> Any:
        self._resolve_scope(int(project_id))
        return self.db.approval_action(
            int(workflow_id),
            str(stage_code),
            str(actor),
            "APPROVE",
            str(comment),
            actor_name=str(actor_name or actor),
            actor_role=str(actor_role),
        )

    def approve_ipc(self, *, project_id: int, actor: str = "", **arguments: Any) -> Any:
        if not self.approve_ipc_callback:
            raise RuntimeError("IPC approval adapter chưa được nối với workflow IPC hiện hữu.")
        scope = self._resolve_scope(int(project_id))
        return self.approve_ipc_callback(project_id=int(scope["workspace_project_id"]), actor=actor, **arguments)

    def approve_vo(self, *, project_id: int, actor: str = "", **arguments: Any) -> Any:
        if not self.approve_vo_callback:
            raise RuntimeError("VO approval adapter chưa được nối với workflow VO hiện hữu.")
        scope = self._resolve_scope(int(project_id))
        return self.approve_vo_callback(project_id=int(scope["workspace_project_id"]), actor=actor, **arguments)

    def close_ncr(self, *, project_id: int, actor: str = "", document_id: int, response: str = "", **_: Any) -> dict[str, Any]:
        scope = self._resolve_scope(int(project_id))
        workspace_id = int(scope["workspace_project_id"])
        row = _rowdict(self.db.document(int(document_id)))
        if not row or int(row.get("project_id") or 0) != workspace_id or str(row.get("doc_type") or "").upper() != "NCR":
            raise ValueError("Không tìm thấy NCR hợp lệ trong workspace nhà thầu hiện tại.")
        data = dict(row)
        data["status"] = "Đóng"
        data["closed_date"] = date.today().isoformat()
        if response:
            data["response"] = str(response)
        self.db.save_document(workspace_id, "NCR", data, doc_id=int(document_id))
        return {"document_id": int(document_id), "workspace_project_id": workspace_id, "status": "Đóng", "actor": actor}

    def generate_report(self, *, project_id: int, actor: str = "", **arguments: Any) -> dict[str, Any]:
        status = self.get_project_status(project_id=project_id, actor=actor)
        integrity = self.check_data_integrity(project_id=project_id, actor=actor)
        payment = status.get("payment") or {}
        indicators = {
            "data_integrity_score": integrity["score"],
            "schedule_delay_percent": status["schedule"]["delay_percent"],
            "contract_days_remaining": status.get("contract_days_remaining"),
            "payment_overdue_value": float(payment.get("overdue") or 0),
            "ncr_overdue": int(arguments.get("ncr_overdue", 0) or 0),
            "rfi_overdue": int(arguments.get("rfi_overdue", 0) or 0),
            "inspection_rejected": int(arguments.get("inspection_rejected", 0) or 0),
            "production_daily_delta": float(arguments.get("production_daily_delta", 0) or 0),
            "production_expected_to_move": bool(arguments.get("production_expected_to_move", False)),
        }
        health = self.supervisor.evaluate(int(status["scope"]["workspace_project_id"]), indicators)
        return {
            "scope": status["scope"],
            "project_status": status,
            "data_integrity": integrity,
            "health_score": health.score,
            "findings": [
                {
                    "code": x.code,
                    "severity": x.severity.value,
                    "title": x.title,
                    "detail": x.detail,
                    "recommended_action": x.recommended_action,
                }
                for x in health.findings
            ],
        }

    def send_notification(self, *, project_id: int, actor: str = "", **arguments: Any) -> Any:
        if not self.notify:
            raise RuntimeError("Notification adapter chưa được nối; không giả lập trạng thái đã gửi.")
        scope = self._resolve_scope(int(project_id))
        return self.notify(project_id=int(scope["workspace_project_id"]), actor=actor, **arguments)