from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any, Sequence

from qlda.application.ai.ports import ToolChoice, ToolDefinition
from qlda.infrastructure.ai.telemetry import content_hash, record_ai_event


class NativeToolCallingAdapter:
    """Provider-native function/tool calling with no database execution authority."""

    def __init__(self, provider: str | None = None) -> None:
        self.provider = str(provider or os.environ.get("QLDA_AUTONOMY_AI_PROVIDER", "openai") or "openai").strip().lower()

    def choose_tools(
        self,
        workspace_project_id: int,
        objective: str,
        tools: Sequence[ToolDefinition],
        *,
        context: dict[str, Any] | None = None,
        max_steps: int = 24,
    ) -> list[ToolChoice]:
        tenant = int(workspace_project_id)
        if tenant <= 0:
            raise ValueError("workspace_project_id phải > 0")
        allowed = {tool.name: tool for tool in tools}
        if not allowed:
            return []
        request_id = "AIR-" + uuid.uuid4().hex[:16].upper()
        started = time.perf_counter()
        try:
            if self.provider in {"openai", "gpt"}:
                choices, model = self._openai(objective, tools, context=context, max_steps=max_steps)
            elif self.provider in {"gemini", "google"}:
                choices, model = self._gemini(objective, tools, context=context, max_steps=max_steps)
            else:
                raise RuntimeError(f"AI Planner provider không hỗ trợ: {self.provider}")
            safe = [choice for choice in choices if choice.tool_name in allowed][: max(1, int(max_steps))]
            record_ai_event({
                "workspace_project_id": tenant,
                "request_id": request_id,
                "event_type": "PLANNER_NATIVE_TOOL_CALL",
                "provider": self.provider,
                "model": model,
                "input": objective,
                "input_hash": content_hash({"objective": objective, "context": context or {}}),
                "context": context or {},
                "tool_name": ",".join(x.tool_name for x in safe),
                "tool_arguments": {str(i + 1): x.arguments for i, x in enumerate(safe)},
                "decision_reason": " | ".join(x.reason for x in safe)[:4000],
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "success": True,
            })
            return safe
        except Exception as exc:
            record_ai_event({
                "workspace_project_id": tenant,
                "request_id": request_id,
                "event_type": "PLANNER_NATIVE_TOOL_CALL",
                "provider": self.provider,
                "input": objective,
                "context": context or {},
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "success": False,
                "error_code": exc.__class__.__name__,
            })
            raise

    @staticmethod
    def _instruction(objective: str, context: dict[str, Any] | None) -> str:
        context_text = json.dumps(context or {}, ensure_ascii=False, default=str)[:12000]
        return (
            "Bạn là AI Planner của QLDA. Chỉ chọn các function được cung cấp. "
            "Không SQL, không shell, không API ngoài. Không tự phê duyệt IPC/VO/hồ sơ/NCR. "
            "Ưu tiên tool đọc/kiểm tra khi thiếu dữ liệu. Có thể gọi nhiều function theo thứ tự cần thiết.\n\n"
            f"MỤC TIÊU: {objective}\nBỐI CẢNH: {context_text}"
        )

    def _openai(
        self,
        objective: str,
        tools: Sequence[ToolDefinition],
        *,
        context: dict[str, Any] | None,
        max_steps: int,
    ) -> tuple[list[ToolChoice], str]:
        from openai import OpenAI

        key = str(os.environ.get("OPENAI_API_KEY") or "").strip()
        if not key:
            raise RuntimeError("OPENAI_API_KEY trống")
        model = str(os.environ.get("QLDA_AUTONOMY_OPENAI_MODEL") or os.environ.get("OPENAI_MODEL") or "gpt-5-mini").strip()
        tool_payload = [
            {
                "type": "function",
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters_schema or {"type": "object", "properties": {}},
            }
            for tool in tools
        ]
        response = OpenAI(api_key=key).responses.create(
            model=model,
            input=self._instruction(objective, context),
            tools=tool_payload,
            tool_choice="required",
        )
        choices: list[ToolChoice] = []
        for item in getattr(response, "output", []) or []:
            if str(getattr(item, "type", "")) != "function_call":
                continue
            name = str(getattr(item, "name", "") or "")
            raw_args = getattr(item, "arguments", "{}") or "{}"
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
            except Exception:
                args = {}
            choices.append(ToolChoice(name, args if isinstance(args, dict) else {}, reason="OpenAI native function call"))
            if len(choices) >= max_steps:
                break
        if not choices:
            raise RuntimeError("OpenAI không trả function call")
        return choices, model

    def _gemini(
        self,
        objective: str,
        tools: Sequence[ToolDefinition],
        *,
        context: dict[str, Any] | None,
        max_steps: int,
    ) -> tuple[list[ToolChoice], str]:
        from google import genai
        from google.genai import types

        key = str(os.environ.get("GEMINI_API_KEY") or "").strip()
        if not key:
            raise RuntimeError("GEMINI_API_KEY trống")
        model = str(os.environ.get("QLDA_AUTONOMY_GEMINI_MODEL") or os.environ.get("GEMINI_MODEL") or "gemini-2.5-flash").strip()
        if model.lower() in {"auto", "default", ""}:
            model = "gemini-2.5-flash"
        declarations = []
        for tool in tools:
            declarations.append(
                types.FunctionDeclaration(
                    name=tool.name,
                    description=tool.description,
                    parameters_json_schema=tool.parameters_schema or {"type": "object", "properties": {}},
                )
            )
        response = genai.Client(api_key=key).models.generate_content(
            model=model,
            contents=self._instruction(objective, context),
            config=types.GenerateContentConfig(tools=[types.Tool(function_declarations=declarations)]),
        )
        calls = getattr(response, "function_calls", None) or []
        choices: list[ToolChoice] = []
        for call in calls[:max_steps]:
            args = getattr(call, "args", None) or {}
            try:
                args = dict(args)
            except Exception:
                args = {}
            choices.append(
                ToolChoice(
                    str(getattr(call, "name", "") or ""),
                    args,
                    reason="Gemini native function call",
                )
            )
        if not choices:
            raise RuntimeError("Gemini không trả function call")
        return choices, model


__all__ = ["NativeToolCallingAdapter"]
