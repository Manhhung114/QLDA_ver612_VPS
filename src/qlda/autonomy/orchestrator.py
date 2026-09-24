from __future__ import annotations

import hashlib
from typing import Any, Protocol

from .models import ActionMode, ExecutionPlan, ExecutionResult, PlanStep, RiskLevel
from .services import ToolRegistry


class Planner(Protocol):
    def plan(self, project_id: int, objective: str, context: dict[str, Any] | None = None) -> ExecutionPlan: ...


class HeuristicPlanner:
    """Deterministic fallback planner used when an LLM planner is unavailable.

    It never invents direct database writes; every step must map to a registered
    service tool. The LLM planner can replace this class while preserving the same
    ExecutionPlan contract.
    """

    def plan(self, project_id: int, objective: str, context: dict[str, Any] | None = None) -> ExecutionPlan:
        text = str(objective or "").lower()
        steps: list[PlanStep] = []

        def add(tool: str, reason: str, arguments: dict[str, Any] | None = None) -> None:
            step_id = f"S{len(steps)+1}"
            deps = (steps[-1].step_id,) if steps else ()
            steps.append(PlanStep(step_id, tool, dict(arguments or {}), reason, deps))

        if any(key in text for key in ("đồng bộ", "google", "sản lượng", "production")):
            add("sync_google_data", "Cần dữ liệu nguồn mới nhất trước khi phân tích.")
            add("check_data_integrity", "Đối soát dữ liệu trước khi AI kết luận.")
        add("get_project_status", "Thu thập trạng thái dự án hiện tại.")
        if any(key in text for key in ("báo cáo", "report", "đánh giá", "tình hình", "rủi ro")):
            add("generate_report", "Tổng hợp kết quả thành báo cáo có kiểm chứng.")
        if any(key in text for key in ("nhắc", "giao việc", "task", "xử lý")):
            add("create_work_task", "Tạo công việc xử lý cho vấn đề đã xác minh.")

        raw = f"{project_id}|{objective}|{'|'.join(x.tool_name for x in steps)}"
        plan_id = "PLAN-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16].upper()
        return ExecutionPlan(int(project_id), str(objective), tuple(steps), plan_id=plan_id)


class ApprovalPolicy:
    """V8.0/V8.2 safety gate for semi-autonomous execution."""

    def requires_approval(self, *, risk: RiskLevel, mode: ActionMode, role: str) -> bool:
        if mode == ActionMode.APPROVAL_REQUIRED:
            return True
        if risk in {RiskLevel.HIGH, RiskLevel.CRITICAL}:
            return True
        # Non-admins cannot silently execute medium-risk write actions.
        if risk == RiskLevel.MEDIUM and str(role).lower() != "admin":
            return True
        return False


class AIOrchestrator:
    def __init__(self, tools: ToolRegistry, planner: Planner | None = None, approval_policy: ApprovalPolicy | None = None) -> None:
        self.tools = tools
        self.planner = planner or HeuristicPlanner()
        self.approval_policy = approval_policy or ApprovalPolicy()

    def create_plan(self, project_id: int, objective: str, context: dict[str, Any] | None = None) -> ExecutionPlan:
        return self.planner.plan(int(project_id), objective, context)

    def execute_plan(
        self,
        plan: ExecutionPlan,
        *,
        actor: str,
        role: str,
        approvals: set[str] | None = None,
        dry_run: bool = False,
    ) -> list[ExecutionResult]:
        approvals = set(approvals or set())
        results: list[ExecutionResult] = []
        completed: set[str] = set()

        for step in plan.steps:
            if any(dep not in completed for dep in step.depends_on):
                results.append(ExecutionResult(step.step_id, step.tool_name, "BLOCKED", error="Dependency chưa hoàn tất."))
                continue
            tool = self.tools.get(step.tool_name)
            needs_approval = self.approval_policy.requires_approval(
                risk=tool.spec.risk,
                mode=tool.spec.mode,
                role=role,
            )
            approved = step.step_id in approvals or step.tool_name in approvals
            if needs_approval and not approved:
                results.append(
                    ExecutionResult(
                        step.step_id,
                        step.tool_name,
                        "PENDING_APPROVAL",
                        approval_required=True,
                    )
                )
                continue
            try:
                output = self.tools.execute(
                    step.tool_name,
                    project_id=plan.project_id,
                    actor=actor,
                    role=role,
                    arguments=step.arguments,
                    approved=approved,
                    dry_run=dry_run,
                )
            except Exception as exc:
                results.append(ExecutionResult(step.step_id, step.tool_name, "FAILED", error=str(exc)))
                continue
            results.append(ExecutionResult(step.step_id, step.tool_name, "SUCCESS", output=output))
            completed.add(step.step_id)
        return results
