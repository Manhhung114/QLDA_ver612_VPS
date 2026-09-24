from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Callable, Iterable

from .models import ExecutionPlan, PlanStep, ToolSpec
from .orchestrator import HeuristicPlanner

CompletionFn = Callable[[int, str], str]


def _extract_json(text: str) -> Any:
    value = str(text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", value, flags=re.I | re.S)
    if fenced:
        value = fenced.group(1).strip()
    try:
        return json.loads(value)
    except Exception:
        pass
    # Models sometimes add one sentence around the JSON. Extract the largest
    # object/array-like span without accepting executable code.
    starts = [idx for idx in (value.find("["), value.find("{")) if idx >= 0]
    if not starts:
        raise ValueError("AI planner không trả về JSON.")
    start = min(starts)
    end = max(value.rfind("]"), value.rfind("}"))
    if end <= start:
        raise ValueError("AI planner trả về JSON không hoàn chỉnh.")
    return json.loads(value[start : end + 1])


class StructuredAIPlanner:
    """V8.0 LLM planner that can only select registered ToolRegistry actions.

    Any provider/parser failure falls back to the deterministic planner. Tool risk,
    RBAC, integrity and approval checks remain downstream and cannot be overridden
    by model output.
    """

    def __init__(
        self,
        complete: CompletionFn,
        tools: Iterable[ToolSpec],
        *,
        fallback: HeuristicPlanner | None = None,
    ) -> None:
        self.complete = complete
        self.specs = {spec.name: spec for spec in tools}
        self.fallback = fallback or HeuristicPlanner()

    def _prompt(self, objective: str, context: dict[str, Any] | None) -> str:
        tool_lines = []
        for spec in self.specs.values():
            tool_lines.append(
                f"- {spec.name}: {spec.description}; risk={spec.risk.value}; mode={spec.mode.value}; roles={','.join(spec.allowed_roles)}"
            )
        context_text = json.dumps(context or {}, ensure_ascii=False, default=str)[:12000]
        return (
            "Bạn là AI Planner của hệ thống QLDA xây dựng. Hãy lập kế hoạch thực thi, không trả lời câu hỏi nghiệp vụ.\n"
            "QUY TẮC BẮT BUỘC:\n"
            "1. Chỉ được chọn tool trong danh sách bên dưới.\n"
            "2. Không được đề xuất SQL, ghi DB trực tiếp, shell command hay API ngoài ToolRegistry.\n"
            "3. Không được tự phê duyệt IPC/VO/hồ sơ/NCR hoặc bỏ qua approval gate.\n"
            "4. Nếu thiếu dữ liệu, ưu tiên tool chỉ đọc/kiểm tra trước.\n"
            "5. Trả về JSON thuần dạng {\"steps\":[{\"tool_name\":...,\"arguments\":{},\"reason\":...,\"depends_on\":[]}]} .\n\n"
            "TOOLS:\n" + "\n".join(tool_lines) + "\n\n"
            f"MỤC TIÊU: {objective}\n"
            f"BỐI CẢNH TÓM TẮT: {context_text}"
        )

    def plan(self, project_id: int, objective: str, context: dict[str, Any] | None = None) -> ExecutionPlan:
        try:
            raw = self.complete(int(project_id), self._prompt(str(objective), context))
            payload = _extract_json(raw)
            items = payload.get("steps") if isinstance(payload, dict) else payload
            if not isinstance(items, list) or not items:
                raise ValueError("AI planner không có bước thực thi.")

            steps: list[PlanStep] = []
            used_ids: set[str] = set()
            for index, item in enumerate(items[:24], start=1):
                if not isinstance(item, dict):
                    continue
                tool_name = str(item.get("tool_name") or item.get("tool") or "").strip()
                if tool_name not in self.specs:
                    continue
                step_id = str(item.get("step_id") or f"S{index}").strip().upper()
                if not re.fullmatch(r"S\d+", step_id) or step_id in used_ids:
                    step_id = f"S{index}"
                used_ids.add(step_id)
                args = item.get("arguments") or {}
                if not isinstance(args, dict):
                    args = {}
                deps_raw = item.get("depends_on") or []
                if isinstance(deps_raw, str):
                    deps_raw = [deps_raw]
                deps = tuple(str(x).upper() for x in deps_raw if str(x).upper() in used_ids)
                steps.append(
                    PlanStep(
                        step_id=step_id,
                        tool_name=tool_name,
                        arguments=dict(args),
                        reason=str(item.get("reason") or "AI Planner"),
                        depends_on=deps,
                    )
                )
            if not steps:
                raise ValueError("AI planner không chọn được tool hợp lệ.")

            raw_id = f"{int(project_id)}|{objective}|" + "|".join(f"{x.step_id}:{x.tool_name}" for x in steps)
            plan_id = "PLAN-" + hashlib.sha256(raw_id.encode("utf-8")).hexdigest()[:16].upper()
            return ExecutionPlan(
                project_id=int(project_id),
                objective=str(objective),
                steps=tuple(steps),
                created_by="AI Structured Planner",
                plan_id=plan_id,
            )
        except Exception:
            return self.fallback.plan(int(project_id), str(objective), context)


__all__ = ["StructuredAIPlanner"]
