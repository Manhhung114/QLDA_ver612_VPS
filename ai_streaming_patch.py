from __future__ import annotations

import os
import random
import re
import time

from ai_service import (
    AIServiceError,
    GeminiProjectAssistant,
    OpenAIProjectAssistant,
    SYSTEM_INSTRUCTIONS,
    classify_gemini_error,
    gemini_error_to_service_error,
    openai_error_to_service_error,
)


# V6.21 WebOpt AI streaming
# Giữ nguyên cách trả lời đầy đủ như trước: dùng toàn bộ snapshot dự án và
# tối đa 8 message lịch sử gần nhất. Chỉ thay đổi cách hiển thị sang streaming.

def _project_input_items(self, project_id, question, history=None, status_date=None):
    if not (question or "").strip():
        raise AIServiceError("Câu hỏi đang trống.")
    snapshot = self.context.build(project_id, question, status_date)
    user_prompt = (
        f"Dữ liệu dự án hiện tại:\n\n{snapshot}\n\n"
        f"CÂU HỎI CỦA NGƯỜI DÙNG:\n{question.strip()}\n\n"
        "Hãy trả lời bám sát snapshot. Nếu cần số liệu, tính từ các dòng được cung cấp "
        "và giữ mã tham chiếu trong kết luận."
    )
    items = [{"role": "developer", "content": SYSTEM_INSTRUCTIONS}]
    for m in list(history or [])[-8:]:
        role = m.get("role")
        content = (m.get("content") or "").strip()
        if role in {"user", "assistant"} and content:
            items.append({"role": role, "content": content})
    items.append({"role": "user", "content": user_prompt})
    return items


def _openai_stream(self, input_items, use_web=None):
    client = self._client()
    kwargs = dict(model=self.model, input=input_items, store=False)
    if self.settings.use_web if use_web is None else use_web:
        kwargs["tools"] = [{"type": "web_search"}]
    try:
        stream_method = getattr(client.responses, "stream", None)
        if callable(stream_method):
            emitted = False
            with stream_method(**kwargs) as stream:
                for event in stream:
                    if getattr(event, "type", "") == "response.output_text.delta":
                        delta = getattr(event, "delta", "") or ""
                        if delta:
                            emitted = True
                            yield str(delta)
                if not emitted:
                    try:
                        final_response = stream.get_final_response()
                        text = getattr(final_response, "output_text", "") or ""
                        if text:
                            yield text.strip()
                            return
                    except Exception:
                        pass
            if emitted:
                return

        # SDK cũ: vẫn giữ giao diện chạy dần thay vì đổ cả khối một lần.
        text = self._respond(input_items, use_web=use_web)
        for part in re.findall(r"\S+\s*", text):
            yield part
    except AIServiceError:
        raise
    except Exception as exc:
        raise openai_error_to_service_error(exc) from exc


def _gemini_stream(self, input_items, use_web=None):
    client = self._client()
    emitted_any = False
    try:
        from google.genai import types

        system_parts = []
        contents = []
        for item in input_items:
            role = str(item.get("role") or "user")
            text = self._content_text(item.get("content"))
            if not text:
                continue
            if role in {"developer", "system"}:
                system_parts.append(text)
                continue
            gemini_role = "model" if role == "assistant" else "user"
            contents.append(
                types.Content(role=gemini_role, parts=[types.Part.from_text(text=text)])
            )
        if not contents:
            contents = [
                types.Content(role="user", parts=[types.Part.from_text(text="OK")])
            ]

        tools = None
        if self.settings.use_web if use_web is None else use_web:
            tools = [types.Tool(google_search=types.GoogleSearch())]
        cfg = types.GenerateContentConfig(
            system_instruction="\n\n".join(system_parts) or SYSTEM_INSTRUCTIONS,
            tools=tools,
        )

        try:
            retry_attempts = max(
                1, min(5, int(os.environ.get("GEMINI_RETRY_ATTEMPTS", "3")))
            )
        except Exception:
            retry_attempts = 3
        try:
            base_delay = max(
                0.25,
                min(5.0, float(os.environ.get("GEMINI_RETRY_BASE_SECONDS", "0.8"))),
            )
        except Exception:
            base_delay = 0.8
        try:
            max_models = max(
                1, min(6, int(os.environ.get("GEMINI_MAX_FALLBACK_MODELS", "4")))
            )
        except Exception:
            max_models = 4

        models = self._fallback_model_sequence(client)[:max_models]
        last_exc = None
        for model_index, model in enumerate(models):
            self._resolved_model = model
            for attempt in range(retry_attempts):
                emitted_this_attempt = False
                try:
                    stream = client.models.generate_content_stream(
                        model=model, contents=contents, config=cfg
                    )
                    for chunk in stream:
                        text = getattr(chunk, "text", "") or ""
                        if text:
                            emitted_this_attempt = True
                            emitted_any = True
                            yield str(text)
                    return
                except Exception as exc:
                    last_exc = exc
                    # Không retry từ đầu sau khi đã phát một phần, tránh lặp chữ.
                    if emitted_this_attempt or emitted_any:
                        raise
                    info = classify_gemini_error(exc)
                    if info.code == "model_not_found":
                        break
                    if not self._is_transient_gemini_error(exc):
                        raise
                    if attempt < retry_attempts - 1:
                        delay = base_delay * (2**attempt) + random.uniform(
                            0, base_delay * 0.35
                        )
                        time.sleep(delay)
                        continue
                    break

            if model_index == 0 and last_exc is not None:
                try:
                    refreshed = self._fallback_model_sequence(client, refresh=True)
                    for candidate in refreshed:
                        if candidate not in models:
                            models.append(candidate)
                    models = models[:max_models]
                except Exception:
                    pass

        if last_exc is not None:
            raise last_exc
        raise AIServiceError(
            "Gemini không có model generateContent khả dụng cho API key hiện tại.",
            code="model_not_found",
        )
    except AIServiceError:
        raise
    except Exception as exc:
        raise gemini_error_to_service_error(exc) from exc
    finally:
        try:
            client.close()
        except Exception:
            pass


def _ask_project_stream(self, project_id, question, history=None, status_date=None, use_web=None):
    items = _project_input_items(self, project_id, question, history, status_date)
    if isinstance(self, GeminiProjectAssistant):
        yield from _gemini_stream(self, items, use_web=use_web)
    else:
        yield from _openai_stream(self, items, use_web=use_web)


def install_ai_streaming() -> None:
    """Add true streaming chat to both AI providers without changing existing answer logic."""
    if getattr(OpenAIProjectAssistant, "_v621_streaming_installed", False):
        return
    OpenAIProjectAssistant.ask_project_stream = _ask_project_stream
    GeminiProjectAssistant.ask_project_stream = _ask_project_stream
    OpenAIProjectAssistant._v621_streaming_installed = True
    GeminiProjectAssistant._v621_streaming_installed = True
