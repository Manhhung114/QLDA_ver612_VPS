from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from typing import Any, Protocol

from .models import ActionMode, ExecutionPlan, ExecutionResult, PlanStep, RiskLevel
from .services import ToolRegistry


class Planner(Protocol):
    def plan(self, project_id: int, objective: str, context: dict[str, Any] | None = None) -> ExecutionPlan: ...


class HeuristicPlanner:
    """Deterministic fallback planner used when an LLM planner is unavailable.

    It never invents direct database writes; every step must map to a registered
    service tool. Advanced draft tools are only planned automatically when their
    required structured payload exists in ``context`` so a vague user request can
    never fabricate quantities, rates, task IDs or image evidence.
    """

    def plan(self, project_id: int, objective: str, context: dict[str, Any] | None = None) -> ExecutionPlan:
        text = str(objective or "").lower()
        context = dict(context or {})
        steps: list[PlanStep] = []

        def add(tool: str, reason: str, arguments: dict[str, Any] | None = None) -> None:
            step_id = f"S{len(steps)+1}"
            deps = (steps[-1].step_id,) if steps else ()
            steps.append(PlanStep(step_id, tool, dict(arguments or {}), reason, deps))

        if any(key in text for key in ("đồng bộ", "google", "sản lượng", "production")):
            add("sync_google_data", "Cần dữ liệu nguồn mới nhất trước khi phân tích.")
            add("check_data_integrity", "Đối soát dữ liệu trước khi AI kết luận.")

        add("get_project_status", "Thu thập trạng thái dự án hiện tại.")

        # V9.2 Contract Audit: deterministic ledger from real dates/evidence.
        if any(key in text for key in ("hợp đồng", "nghĩa vụ", "bảo lãnh", "bảo hiểm", "gia hạn", "contract")):
            add("audit_contract_obligations", "Kiểm tra các nghĩa vụ và mốc có due-date thực tế trong workspace nhà thầu.")

        # V9.3 QS reconciliation does the arithmetic in code, not in the LLM.
        if any(key in text for key in ("đối soát ipc", "ipc boq", "boq ipc", "khối lượng ipc", "đơn giá ipc", "reconcile")):
            arguments: dict[str, Any] = {}
            selected_claim = str(context.get("claim_id") or "").strip()
            if selected_claim:
                arguments["claim_id"] = selected_claim
            add("reconcile_ipc_boq", "Đối soát IPC với BOQ trước khi đưa ra kết luận QS.", arguments)

        # V9.5: schedule prediction is deterministic velocity extrapolation and
        # verified due-date cash needs; payment-overdue is not part of Health.
        if any(key in text for key in ("dự báo", "forecast", "predict", "milestone", "nguy cơ trễ", "rủi ro tiến độ")):
            add(
                "forecast_project_risk",
                "Tạo cảnh báo sớm tiến độ từ velocity có kiểm chứng.",
                {"horizon_days": int(context.get("forecast_horizon_days") or 60)},
            )

        # V9.4 only creates a DRAFT when the caller supplied actual change facts.
        change_payload = context.get("change_payload")
        if isinstance(change_payload, dict) and any(key in text for key in ("vo", "phát sinh", "thay đổi", "variation", "change")):
            add("draft_vo_from_change", "Lập bản nháp VO từ thay đổi đã định lượng; không phê duyệt tự động.", dict(change_payload))

        # V9.6 never infers a task/image from prose. It needs a structured site
        # observation or a scoped attachment ID and produces a proposal only.
        site_payload = context.get("site_observation")
        if isinstance(site_payload, dict) and any(key in text for key in ("ảnh", "camera", "drone", "vision", "hiện trường", "site")):
            add("analyze_site_progress", "Phân tích bằng chứng ảnh/quan sát và chỉ đề xuất phần trăm hoàn thành.", dict(site_payload))

        # V9.1 routing is useful only when an actual finding payload exists.
        route_payload = context.get("finding_payload")
        if isinstance(route_payload, dict) and any(key in text for key in ("phân công", "assign", "routing", "giao việc", "người phụ trách")):
            add("route_work_task", "Xác định discipline, SLA và người phụ trách từ finding đã xác minh.", dict(route_payload))

        if any(key in text for key in ("báo cáo", "report", "đánh giá", "tình hình", "rủi ro")):
            add("generate_report", "Tổng hợp kết quả thành báo cáo có kiểm chứng.")

        if any(key in text for key in ("nhắc", "giao việc", "task", "xử lý")):
            assignee_email = str(context.get("default_assignee_email") or "").strip()
            if assignee_email:
                due_at = str(context.get("default_due_at") or "").strip()
                if not due_at:
                    due_at = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
                add(
                    "create_work_task",
                    "Tạo công việc xử lý cho vấn đề đã xác minh.",
                    {
                        "title": f"AI: {str(objective or '').strip()[:160]}",
                        "description": "Công việc do AI Orchestrator đề xuất từ mục tiêu đã xác minh. Người phụ trách cần kiểm tra nội dung trước khi hoàn tất.",
                        "assignee_email": assignee_email,
                        "assignee_name": str(context.get("default_assignee_name") or assignee_email),
                        "due_at": due_at,
                        "priority": str(context.get("default_priority") or "Bình thường"),
                    },
                )

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
        data_valid: bool = True,
    ) -> list[ExecutionResult]:
        """Execute a validated plan through the service boundary.

        `data_valid=False` is a hard V7.7 safety boundary: only read-only tools may
        run. Drafts and writes are blocked until the source reconciliation passes.
        """
        approvals = set(approvals or set())
        results: list[ExecutionResult] = []
        completed: set[str] = set()

        for step in plan.steps:
            if any(dep not in completed for dep in step.depends_on):
                results.append(ExecutionResult(step.step_id, step.tool_name, "BLOCKED", error="Dependency chưa hoàn tất."))
                continue
            tool = self.tools.get(step.tool_name)
            if not data_valid and tool.spec.mode != ActionMode.READ_ONLY:
                results.append(
                    ExecutionResult(
                        step.step_id,
                        step.tool_name,
                        "BLOCKED_DATA_INTEGRITY",
                        error="AI_DATA_VALID=False: tác vụ có thể thay đổi trạng thái bị khóa cho đến khi dữ liệu được đối soát.",
                    )
                )
                continue
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
