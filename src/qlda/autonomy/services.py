from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from typing import Any, Callable

from .models import ActionMode, RiskLevel, ToolSpec

ToolHandler = Callable[..., Any]
AuditSink = Callable[[dict[str, Any]], None]


@dataclass(slots=True)
class RegisteredTool:
    spec: ToolSpec
    handler: ToolHandler


class ToolRegistry:
    """V7.8+ application tool boundary used by humans, workers and AI.

    AI never writes the database directly. Every action crosses this registry,
    which centralizes RBAC, approval, idempotency and audit metadata.
    """

    def __init__(self, audit_sink: AuditSink | None = None) -> None:
        self._tools: dict[str, RegisteredTool] = {}
        self._audit_sink = audit_sink or (lambda row: None)
        self._idempotency: dict[str, Any] = {}

    def register(self, spec: ToolSpec, handler: ToolHandler) -> None:
        if not spec.name or spec.name in self._tools:
            raise ValueError(f"Tool đã tồn tại hoặc không hợp lệ: {spec.name}")
        self._tools[spec.name] = RegisteredTool(spec=spec, handler=handler)

    def list_specs(self) -> tuple[ToolSpec, ...]:
        return tuple(item.spec for item in self._tools.values())

    def get(self, name: str) -> RegisteredTool:
        try:
            return self._tools[str(name)]
        except KeyError as exc:
            raise KeyError(f"Không có AI tool: {name}") from exc

    @staticmethod
    def _idem_key(name: str, project_id: int, arguments: dict[str, Any]) -> str:
        raw = json.dumps([name, int(project_id), arguments], ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def execute(
        self,
        name: str,
        *,
        project_id: int,
        actor: str,
        role: str,
        arguments: dict[str, Any] | None = None,
        approved: bool = False,
        dry_run: bool = False,
    ) -> Any:
        tool = self.get(name)
        args = dict(arguments or {})
        normalized_role = str(role or "read").lower()
        if normalized_role not in {x.lower() for x in tool.spec.allowed_roles}:
            raise PermissionError(f"Role {role} không được dùng tool {name}.")
        if tool.spec.mode == ActionMode.APPROVAL_REQUIRED and not approved:
            raise PermissionError(f"Tool {name} yêu cầu phê duyệt trước khi thực thi.")
        if tool.spec.risk in {RiskLevel.HIGH, RiskLevel.CRITICAL} and not approved:
            raise PermissionError(f"Tool {name} có rủi ro {tool.spec.risk.value}; cần phê duyệt.")

        key = self._idem_key(name, project_id, args)
        if tool.spec.idempotent and key in self._idempotency:
            return self._idempotency[key]

        audit = {
            "project_id": int(project_id),
            "tool": name,
            "actor": actor,
            "role": normalized_role,
            "arguments": args,
            "risk": tool.spec.risk.value,
            "mode": tool.spec.mode.value,
            "approved": bool(approved),
            "dry_run": bool(dry_run),
        }
        if dry_run:
            audit["status"] = "DRY_RUN"
            self._audit_sink(audit)
            return {"status": "DRY_RUN", "tool": name, "arguments": args}

        try:
            result = tool.handler(project_id=int(project_id), actor=actor, **args)
        except Exception as exc:
            audit.update({"status": "FAILED", "error": str(exc)})
            self._audit_sink(audit)
            raise
        audit.update({"status": "SUCCESS"})
        self._audit_sink(audit)
        if tool.spec.idempotent:
            self._idempotency[key] = result
        return result


def _object_schema(
    properties: dict[str, Any] | None = None,
    *,
    required: tuple[str, ...] = (),
    additional: bool = False,
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": dict(properties or {}),
        "additionalProperties": bool(additional),
    }
    if required:
        schema["required"] = list(required)
    return schema


_STR = {"type": "string"}
_INT = {"type": "integer"}
_NUM = {"type": "number"}
_BOOL = {"type": "boolean"}


def _schema_for_tool(name: str) -> dict[str, Any]:
    """Provider-neutral JSON schemas derived from actual handler contracts."""
    schemas: dict[str, dict[str, Any]] = {
        "get_project_status": _object_schema(),
        "check_data_integrity": _object_schema(),
        "sync_google_data": _object_schema(
            {"workspace_ids": {"type": "array", "items": _INT}}, additional=False
        ),
        "create_work_task": _object_schema(
            {
                "title": _STR,
                "description": _STR,
                "assignee_email": _STR,
                "assignee_name": _STR,
                "priority": _STR,
                "due_at": _STR,
                "source_module": _STR,
                "source_type": _STR,
                "source_id": _STR,
                "source_code": _STR,
                "source_title": _STR,
            },
            required=("title",),
        ),
        "draft_rfi": _object_schema(
            {
                "code": _STR,
                "subject": _STR,
                "discipline": _STR,
                "contractor": _STR,
                "issuer": _STR,
                "assignee": _STR,
                "due_date": _STR,
                "priority": _STR,
                "description": _STR,
                "related_wbs": _STR,
            },
            required=("subject",),
        ),
        "draft_ncr": _object_schema(
            {
                "code": _STR,
                "subject": _STR,
                "discipline": _STR,
                "contractor": _STR,
                "issuer": _STR,
                "assignee": _STR,
                "due_date": _STR,
                "priority": _STR,
                "description": _STR,
                "related_wbs": _STR,
            },
            required=("subject",),
        ),
        "update_schedule_progress": _object_schema(
            {"task_id": _INT, "actual_progress": {"type": "integer", "minimum": 0, "maximum": 100}},
            required=("task_id", "actual_progress"),
        ),
        "approve_document": _object_schema(
            {
                "workflow_id": _INT,
                "stage_code": _STR,
                "comment": _STR,
                "actor_name": _STR,
                "actor_role": _STR,
            },
            required=("workflow_id", "stage_code"),
        ),
        # IPC/VO approval callbacks differ by deployed workflow. They remain
        # approval-gated and expose no guessed required fields to the model.
        "approve_ipc": _object_schema(additional=True),
        "approve_vo": _object_schema(additional=True),
        "close_ncr": _object_schema(
            {"document_id": _INT, "response": _STR}, required=("document_id",)
        ),
        "generate_report": _object_schema(
            {
                "ncr_overdue": _INT,
                "rfi_overdue": _INT,
                "inspection_rejected": _INT,
                "production_daily_delta": _NUM,
                "production_expected_to_move": _BOOL,
            }
        ),
        "send_notification": _object_schema(additional=True),
        "route_work_task": _object_schema(
            {
                "finding_code": _STR,
                "title": _STR,
                "detail": _STR,
                "severity": _STR,
                "source_ref": _STR,
                "discipline": _STR,
                "create_task": _BOOL,
            },
            required=("title",),
        ),
        "audit_contract_obligations": _object_schema(),
        "reconcile_ipc_boq": _object_schema(),
        "draft_vo_from_change": _object_schema(
            {
                "source_type": _STR,
                "source_document_id": _INT,
                "item_name": _STR,
                "unit": _STR,
                "old_qty": _NUM,
                "new_qty": _NUM,
                "unit_price": {"type": "number", "minimum": 0},
            },
            required=("item_name", "unit", "old_qty", "new_qty"),
        ),
        "forecast_project_risk": _object_schema(
            {"horizon_days": {"type": "integer", "minimum": 7, "maximum": 180}}
        ),
        "analyze_site_progress": _object_schema(
            {
                "task_id": _INT,
                "object_label": _STR,
                "planned_quantity": {"type": "number", "exclusiveMinimum": 0},
                "observed_quantity": {"type": ["number", "null"], "minimum": 0},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                "evidence_ref": _STR,
                "notes": _STR,
                "attachment_id": _INT,
            },
            required=("planned_quantity",),
        ),
        "run_advanced_supervision": _object_schema(),
    }
    return schemas.get(name, _object_schema(additional=True))


def _with_schema(spec: ToolSpec) -> ToolSpec:
    return replace(spec, parameters_schema=_schema_for_tool(spec.name))


def default_tool_specs() -> tuple[ToolSpec, ...]:
    """Canonical V7.8→V9.6 tool contract with native function schemas."""
    base = (
        ToolSpec("get_project_status", "Đọc trạng thái dự án", RiskLevel.LOW, ActionMode.READ_ONLY),
        ToolSpec("sync_google_data", "Đồng bộ nguồn Google read-only", RiskLevel.LOW, ActionMode.AUTO, ("admin",)),
        ToolSpec("check_data_integrity", "Kiểm tra đối soát dữ liệu nguồn", RiskLevel.LOW, ActionMode.AUTO),
        ToolSpec("create_work_task", "Tạo công việc nhắc/xử lý", RiskLevel.LOW, ActionMode.AUTO, ("admin", "update")),
        ToolSpec("draft_rfi", "Tạo bản nháp RFI", RiskLevel.LOW, ActionMode.DRAFT, ("admin", "update")),
        ToolSpec("draft_ncr", "Tạo bản nháp NCR", RiskLevel.MEDIUM, ActionMode.DRAFT, ("admin", "update")),
        ToolSpec("update_schedule_progress", "Cập nhật tiến độ", RiskLevel.MEDIUM, ActionMode.APPROVAL_REQUIRED, ("admin", "update")),
        ToolSpec("approve_document", "Phê duyệt hồ sơ", RiskLevel.HIGH, ActionMode.APPROVAL_REQUIRED, ("admin",)),
        ToolSpec("approve_ipc", "Phê duyệt IPC", RiskLevel.CRITICAL, ActionMode.APPROVAL_REQUIRED, ("admin",)),
        ToolSpec("approve_vo", "Phê duyệt VO", RiskLevel.CRITICAL, ActionMode.APPROVAL_REQUIRED, ("admin",)),
        ToolSpec("close_ncr", "Đóng NCR", RiskLevel.HIGH, ActionMode.APPROVAL_REQUIRED, ("admin",)),
        ToolSpec("generate_report", "Lập báo cáo dự án", RiskLevel.LOW, ActionMode.AUTO),
        ToolSpec("send_notification", "Gửi thông báo theo workflow", RiskLevel.LOW, ActionMode.AUTO, ("admin", "update")),
    )
    from .advanced_automation import advanced_tool_specs

    return tuple(_with_schema(spec) for spec in (base + advanced_tool_specs()))
