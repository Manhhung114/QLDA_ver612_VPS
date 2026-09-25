from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from typing import Any, Callable

from qlda.application.ai.tool_schemas import tool_parameters_schema

from .models import ActionMode, RiskLevel, ToolSpec

ToolHandler = Callable[..., Any]
AuditSink = Callable[[dict[str, Any]], None]


@dataclass(slots=True)
class RegisteredTool:
    spec: ToolSpec
    handler: ToolHandler


class ToolRegistry:
    """Application tool boundary used by humans, workers and AI.

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


def _with_schema(spec: ToolSpec) -> ToolSpec:
    return replace(
        spec,
        parameters_schema=tool_parameters_schema(spec.name, spec.parameters_schema),
    )


def default_tool_specs() -> tuple[ToolSpec, ...]:
    """Canonical tool contract with provider-neutral JSON parameter schemas."""
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
