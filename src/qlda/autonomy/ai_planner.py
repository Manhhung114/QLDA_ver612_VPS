from __future__ import annotations

import hashlib
import json
from typing import Any, Callable, Iterable, Sequence

from qlda.application.ai import AITelemetryPort, ToolChoice, ToolDefinition
from qlda.application.ai.tool_schemas import tool_parameters_schema

from .models import ExecutionPlan, PlanStep, ToolSpec
from .orchestrator import HeuristicPlanner

CompletionFn = Callable[[int, str], str]
ToolCallingFn = Callable[[int, str, Sequence[ToolDefinition], dict[str, Any] | None], Sequence[ToolChoice]]


class StructuredAIPlanner:
    """LLM planner constrained to registered ToolRegistry actions.

    Provider-native tool/function calling is the primary path. A strict JSON text
    completion is retained only as an opt-in compatibility fallback; the old
    regex-based extraction is gone. RBAC, integrity and approval remain downstream.
    Every finalized plan can also be written to the provider-neutral AI audit sink.
    """

    def __init__(
        self,
        complete: CompletionFn | None,
        tools: Iterable[ToolSpec],
        *,
        fallback: HeuristicPlanner | None = None,
        tool_caller: ToolCallingFn | None = None,
        telemetry: AITelemetryPort | None = None,
    ) -> None:
        self.complete = complete
        self.specs = {spec.name: spec for spec in tools}
        self.fallback = fallback or HeuristicPlanner()
        self.tool_caller = tool_caller
        self.telemetry = telemetry

    def _definitions(self) -> tuple[ToolDefinition, ...]:
        return tuple(
            ToolDefinition(
                name=spec.name,
                description=(
                    f"{spec.description}; risk={spec.risk.value}; mode={spec.mode.value}; "
                    f"roles={','.join(spec.allowed_roles)}"
                ),
                parameters_schema=tool_parameters_schema(spec.name, spec.parameters_schema),
            )
            for spec in self.specs.values()
        )

    def _prompt(self, objective: str, context: dict[str, Any] | None) -> str:
        tool_lines = [
            f"- {spec.name}: {spec.description}; risk={spec.risk.value}; mode={spec.mode.value}"
            for spec in self.specs.values()
        ]
        context_text = json.dumps(context or {}, ensure_ascii=False, default=str)[:12000]
        return (
            "Bạn là AI Planner của QLDA. Chỉ chọn tool đã đăng ký; không SQL, shell, API ngoài; "
            "không tự phê duyệt IPC/VO/hồ sơ/NCR. Trả JSON thuần, không markdown, dạng "
            "{\"steps\":[{\"tool_name\":\"...\",\"arguments\":{},\"reason\":\"...\",\"depends_on\":[]}]} .\n"
            "TOOLS:\n" + "\n".join(tool_lines) + "\n"
            f"MỤC TIÊU: {objective}\nBỐI CẢNH: {context_text}"
        )

    def _steps_from_choices(self, choices: Sequence[ToolChoice]) -> list[PlanStep]:
        steps: list[PlanStep] = []
        used_ids: set[str] = set()
        for index, choice in enumerate(choices[:24], start=1):
            tool_name = str(choice.tool_name or "").strip()
            if tool_name not in self.specs:
                continue
            step_id = f"S{index}"
            explicit = tuple(str(x).upper() for x in choice.depends_on if str(x).upper() in used_ids)
            # Native providers normally return independent function calls. Chain
            # them conservatively unless the model supplied a valid dependency so
            # a later write cannot race ahead of an earlier integrity/read step.
            deps = explicit or ((steps[-1].step_id,) if steps else ())
            used_ids.add(step_id)
            steps.append(
                PlanStep(
                    step_id=step_id,
                    tool_name=tool_name,
                    arguments=dict(choice.arguments or {}),
                    reason=str(choice.reason or "AI native tool calling"),
                    depends_on=deps,
                )
            )
        return steps

    def _strict_text_choices(self, project_id: int, objective: str, context: dict[str, Any] | None) -> list[ToolChoice]:
        if self.complete is None:
            return []
        raw = self.complete(int(project_id), self._prompt(str(objective), context))
        payload = json.loads(str(raw or "").strip())
        items = payload.get("steps") if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            return []
        choices: list[ToolChoice] = []
        for item in items[:24]:
            if not isinstance(item, dict):
                continue
            deps = item.get("depends_on") or []
            if isinstance(deps, str):
                deps = [deps]
            choices.append(
                ToolChoice(
                    tool_name=str(item.get("tool_name") or item.get("tool") or ""),
                    arguments=dict(item.get("arguments") or {}) if isinstance(item.get("arguments") or {}, dict) else {},
                    reason=str(item.get("reason") or "AI strict JSON fallback"),
                    depends_on=tuple(str(x) for x in deps),
                )
            )
        return choices

    @staticmethod
    def _plan_id(project_id: int, objective: str, steps: Sequence[PlanStep]) -> str:
        raw_id = f"{int(project_id)}|{objective}|" + "|".join(f"{x.step_id}:{x.tool_name}" for x in steps)
        return "PLAN-" + hashlib.sha256(raw_id.encode("utf-8")).hexdigest()[:16].upper()

    def _record_plan(self, plan: ExecutionPlan, context: dict[str, Any] | None) -> None:
        if self.telemetry is None:
            return
        for step in plan.steps:
            try:
                self.telemetry.record(
                    {
                        "workspace_project_id": plan.project_id,
                        "plan_id": plan.plan_id,
                        "event_type": "PLANNER_DECISION",
                        "context": {
                            "objective": plan.objective,
                            "created_by": plan.created_by,
                            "context_keys": sorted(str(k) for k in (context or {}).keys()),
                        },
                        "tool_name": step.tool_name,
                        "tool_arguments": step.arguments,
                        "decision_reason": step.reason,
                        "success": True,
                    }
                )
            except Exception:
                pass

    def plan(self, project_id: int, objective: str, context: dict[str, Any] | None = None) -> ExecutionPlan:
        try:
            choices: Sequence[ToolChoice] = ()
            created_by = "AI Native Tool Planner"
            if self.tool_caller is not None:
                choices = self.tool_caller(int(project_id), str(objective), self._definitions(), context)
            if not choices:
                choices = self._strict_text_choices(int(project_id), str(objective), context)
                created_by = "AI Strict JSON Planner"
            steps = self._steps_from_choices(choices)
            if not steps:
                raise ValueError("AI planner không chọn được tool hợp lệ.")
            plan = ExecutionPlan(
                project_id=int(project_id),
                objective=str(objective),
                steps=tuple(steps),
                created_by=created_by,
                plan_id=self._plan_id(int(project_id), str(objective), steps),
            )
            self._record_plan(plan, context)
            return plan
        except Exception as exc:
            plan = self.fallback.plan(int(project_id), str(objective), context)
            if self.telemetry is not None:
                try:
                    self.telemetry.record(
                        {
                            "workspace_project_id": int(project_id),
                            "plan_id": plan.plan_id,
                            "event_type": "PLANNER_FALLBACK",
                            "context": {"objective": str(objective)},
                            "fallback_used": True,
                            "success": False,
                            "error_code": exc.__class__.__name__,
                            "decision_reason": str(exc)[:1000],
                        }
                    )
                except Exception:
                    pass
            return plan


__all__ = ["StructuredAIPlanner"]
