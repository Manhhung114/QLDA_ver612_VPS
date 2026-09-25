from __future__ import annotations

import base64
import json
import random
import time
from datetime import date
from typing import Any, Sequence

from qlda.infrastructure.ai.provider_settings import (
    DEFAULT_GEMINI_MODEL,
    get_provider_settings,
    normalize_gemini_model,
)


_GEMINI_RETRY_DELAYS = (1.0, 2.0, 4.0)
_GEMINI_PREFERRED_MODELS = (
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
)


class AIProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        code: str = "ai_error",
        retryable: bool = False,
        action: str = "",
    ) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.action = action


def _model_id(value: Any) -> str:
    raw = str(value or "").strip()
    lowered = raw.lower()
    if lowered.startswith("models/"):
        return raw.split("/", 1)[1].strip()
    marker = "/models/"
    if marker in lowered:
        return raw[lowered.index(marker) + len(marker) :].strip()
    return raw


def _is_gemini_model_error(exc: BaseException) -> bool:
    text = str(exc or "").lower()
    not_found = any(token in text for token in ("404", "not_found", "not found", "no longer available"))
    bad_format = "unexpected model name format" in text
    invalid_model = "model" in text and any(token in text for token in ("invalid_argument", "invalid argument"))
    return bool((not_found and "model" in text) or bad_format or invalid_model)


def _is_transient_provider_error(exc: BaseException) -> bool:
    text = str(exc or "").lower()
    return any(
        token in text
        for token in (
            "408",
            "429",
            "500",
            "502",
            "503",
            "504",
            "unavailable",
            "overloaded",
            "high demand",
            "temporar",
            "timeout",
            "timed out",
            "rate limit",
            "resource_exhausted",
        )
    )


def _friendly_error(exc: BaseException) -> AIProviderError:
    text = str(exc or "").strip()
    lower = text.lower()
    code = exc.__class__.__name__ or "ai_error"
    retryable = _is_transient_provider_error(exc)
    action = ""
    message = text or "Nhà cung cấp AI trả về lỗi không xác định."
    if any(token in lower for token in ("401", "unauthorized", "invalid api key", "api_key")):
        code = "invalid_api_key"
        action = "Kiểm tra API key trong Cài đặt hệ thống."
    elif any(token in lower for token in ("429", "rate limit", "quota", "resource_exhausted")):
        code = "rate_limit"
        action = "Thử lại sau hoặc kiểm tra quota của nhà cung cấp AI."
    elif any(token in lower for token in ("503", "unavailable", "overloaded", "high demand")):
        code = "service_unavailable"
        action = "Gemini đang quá tải tạm thời. Hệ thống đã tự thử lại; hãy gửi lại yêu cầu sau ít phút nếu lỗi còn tiếp diễn."
    elif any(token in lower for token in ("timeout", "timed out", "504")):
        code = "timeout"
        action = "Nhà cung cấp AI phản hồi chậm. Hãy thử lại yêu cầu."
    elif _is_gemini_model_error(exc):
        code = "model_unavailable"
        retryable = False
        message = "Gemini không tìm thấy model tạo nội dung tương thích với API key hiện tại."
        action = "Đặt Gemini model = auto hoặc kiểm tra danh sách model khả dụng trong Cài đặt hệ thống."
    return AIProviderError(message, code=code, retryable=retryable, action=action)


def _gemini_model_candidates(client: Any, configured_model: str) -> list[str]:
    """Return generateContent models available to this exact API key.

    A commercial on-premise installation can use a customer-owned Google key,
    and model availability can differ between keys/accounts. Prefer the saved
    model when it is advertised by the Models API, otherwise select a compatible
    Flash model from the live catalog. If catalog discovery itself is not
    available, preserve the configured model so older SDKs continue to work.
    """
    configured = _model_id(normalize_gemini_model(configured_model)) or DEFAULT_GEMINI_MODEL
    discovered: list[str] = []
    catalog_ok = False
    try:
        for item in client.models.list():
            actions = list(getattr(item, "supported_actions", None) or [])
            if actions and "generateContent" not in actions:
                continue
            raw = (
                getattr(item, "base_model_id", "")
                or getattr(item, "baseModelId", "")
                or getattr(item, "name", "")
            )
            candidate = _model_id(raw)
            lowered = candidate.lower()
            if not lowered.startswith("gemini-"):
                continue
            if any(token in lowered for token in ("embedding", "tts", "live", "image")):
                continue
            if candidate not in discovered:
                discovered.append(candidate)
        catalog_ok = True
    except Exception:
        catalog_ok = False

    if not catalog_ok:
        return [configured]

    ordered: list[str] = []

    def add(value: str) -> None:
        value = _model_id(value)
        if value and value in discovered and value not in ordered:
            ordered.append(value)

    add(configured)
    for preferred in _GEMINI_PREFERRED_MODELS:
        add(preferred)
    for candidate in discovered:
        if "flash" in candidate.lower():
            add(candidate)
    for candidate in discovered:
        add(candidate)

    return ordered[:6] or [configured]


def _gemini_generate(client, *, model: str, contents: Any, config: Any):
    """Call Gemini with live model discovery, fallback and bounded retry."""
    candidates = _gemini_model_candidates(client, model)
    last_exc: BaseException | None = None

    for model_index, selected in enumerate(candidates):
        attempt = 0
        while True:
            try:
                return client.models.generate_content(
                    model=selected,
                    contents=contents,
                    config=config,
                )
            except Exception as exc:
                last_exc = exc
                if _is_gemini_model_error(exc):
                    break
                if _is_transient_provider_error(exc):
                    # Keep the existing bounded backoff on the primary model.
                    # A discovered fallback is tried immediately after retries
                    # are exhausted so the UI does not wait on every candidate.
                    if model_index == 0 and attempt < len(_GEMINI_RETRY_DELAYS):
                        delay = _GEMINI_RETRY_DELAYS[attempt] + random.uniform(0.0, 0.35)
                        attempt += 1
                        time.sleep(delay)
                        continue
                    break
                raise

    if last_exc is not None:
        raise last_exc
    raise RuntimeError("Gemini không trả về model generateContent khả dụng.")


class NativeProviderGateway:
    """Native OpenAI/Gemini gateway with no ``runtime_core`` dependency.

    All prompts entering this gateway are already scoped to one workspace by the
    application/context layer. The gateway owns provider configuration and the
    final remote API call only; retrieval, provenance, telemetry and tools stay
    in their dedicated native boundaries.
    """

    @staticmethod
    def _settings(provider: str | None = None) -> dict[str, Any]:
        settings = get_provider_settings(provider)
        if not str(settings.get("api_key") or "").strip():
            raise AIProviderError(
                "Chưa cấu hình API key trong Cài đặt hệ thống.",
                code="missing_api_key",
                action="Cấu hình API key cho OpenAI hoặc Gemini.",
            )
        return settings

    @classmethod
    def generate(
        cls,
        prompt: str,
        *,
        provider: str | None = None,
        system: str = "",
        use_web: bool | None = None,
    ) -> str:
        settings = cls._settings(provider)
        selected = str(settings.get("provider") or "openai").lower()
        model = str(settings.get("model") or "").strip()
        try:
            if selected == "gemini":
                from google import genai
                from google.genai import types

                client = genai.Client(api_key=str(settings["api_key"]))
                try:
                    config = types.GenerateContentConfig(
                        system_instruction=system or "Trả lời chính xác theo dữ liệu được cung cấp. Không bịa số liệu."
                    )
                    response = _gemini_generate(
                        client,
                        model=model or DEFAULT_GEMINI_MODEL,
                        contents=str(prompt or ""),
                        config=config,
                    )
                    text = str(getattr(response, "text", "") or "").strip()
                finally:
                    try:
                        client.close()
                    except Exception:
                        pass
            else:
                from openai import OpenAI

                client = OpenAI(api_key=str(settings["api_key"]))
                response = client.responses.create(
                    model=model or "gpt-5-mini",
                    store=False,
                    input=[
                        {
                            "role": "developer",
                            "content": system or "Trả lời chính xác theo dữ liệu được cung cấp. Không bịa số liệu.",
                        },
                        {"role": "user", "content": str(prompt or "")},
                    ],
                )
                text = str(getattr(response, "output_text", "") or "").strip()
            return text or "AI không trả về nội dung."
        except AIProviderError:
            raise
        except Exception as exc:
            raise _friendly_error(exc) from exc

    @classmethod
    def run(
        cls,
        provider: str,
        method: str,
        workspace_scope: int | None,
        *args: Any,
        **kwargs: Any,
    ) -> str:
        name = str(method or "").strip()
        status_date = kwargs.get("status_date")
        date_text = status_date.isoformat() if isinstance(status_date, date) else str(status_date or "")
        context = str(kwargs.get("context") or "").strip()

        if name == "test_connection":
            return cls.generate(
                "Trả lời ngắn gọn: Kết nối AI thành công.",
                provider=provider,
                system="Đây là phép kiểm tra kết nối. Không thêm nội dung ngoài yêu cầu.",
            )

        project_id = int(args[0]) if args else int(workspace_scope or 0)
        workspace = int(workspace_scope or project_id or 0)
        if workspace <= 0:
            raise AIProviderError("AI cần workspace_project_id hợp lệ.", code="invalid_workspace")

        if name == "ask_project":
            question = str(args[1] if len(args) > 1 else "").strip()
            history: Sequence[dict[str, Any]] = kwargs.get("history") or []
            history_text = json.dumps(list(history)[-12:], ensure_ascii=False, default=str) if history else "[]"
            prompt = (
                f"WORKSPACE={workspace}; PROJECT={project_id}; STATUS_DATE={date_text or 'không chỉ định'}\n"
                f"LỊCH SỬ GẦN ĐÂY={history_text}\n\n{question}"
            )
            return cls.generate(
                prompt,
                provider=provider,
                use_web=kwargs.get("use_web"),
                system=(
                    "Bạn là trợ lý quản lý dự án xây dựng. Chỉ kết luận từ dữ liệu/ngữ cảnh được cung cấp; "
                    "giữ nguyên nhãn [NGUỒN ...] khi viện dẫn; nêu rõ khi thiếu dữ liệu."
                ),
            )

        if name == "analyze_schedule_risk":
            prompt = context or (
                f"Phân tích rủi ro tiến độ cho PROJECT={project_id}, WORKSPACE={workspace}, "
                f"STATUS_DATE={date_text or 'hiện tại'}. Nêu rủi ro, nguyên nhân, mức độ và hành động ưu tiên."
            )
            return cls.generate(
                prompt,
                provider=provider,
                system="Phân tích tiến độ dự án xây dựng từ nguồn được cung cấp. Không tự tạo số liệu hay ngày tháng.",
            )

        if name == "draft_report":
            period = str(kwargs.get("period") or "tuần").strip()
            prompt = context or (
                f"Soạn báo cáo {period} cho PROJECT={project_id}, WORKSPACE={workspace}, "
                f"STATUS_DATE={date_text or 'hiện tại'}. Bao gồm tiến độ, chất lượng, hồ sơ, rủi ro và hành động."
            )
            return cls.generate(
                prompt,
                provider=provider,
                system="Soạn báo cáo QLDA chuyên nghiệp từ đúng dữ liệu được cung cấp; không bịa số liệu.",
            )

        if name == "legal_qa":
            question = str(args[1] if len(args) > 1 else "").strip()
            return cls.generate(
                f"PROJECT={project_id}; WORKSPACE={workspace}; STATUS_DATE={date_text or 'không chỉ định'}\n\n{question}",
                provider=provider,
                use_web=kwargs.get("use_web"),
                system=(
                    "Bạn hỗ trợ tra cứu pháp lý xây dựng. Phân biệt dữ liệu dự án với quy định pháp luật; "
                    "không bịa điều khoản, tiêu chuẩn hoặc nguồn. Khi nguồn chưa đủ phải nói rõ."
                ),
            )

        raise AIProviderError(f"Phương thức AI không được hỗ trợ: {name}", code="unsupported_method")

    @classmethod
    def data_hub_answer(cls, workspace_scope: int, prompt: str) -> str:
        if int(workspace_scope or 0) <= 0:
            raise AIProviderError("Data Hub cần workspace hợp lệ.", code="invalid_workspace")
        return cls.generate(
            str(prompt or ""),
            system=(
                "Chỉ phân tích dữ liệu QLDA được cung cấp cho đúng workspace. "
                "Không bịa số liệu; giữ provenance/citation có trong prompt."
            ),
        )

    @classmethod
    def vision_text(
        cls,
        workspace_scope: int,
        *,
        data: bytes,
        mime_type: str,
        prompt: str,
    ) -> str:
        if int(workspace_scope or 0) <= 0:
            raise AIProviderError("AI Vision cần workspace hợp lệ.", code="invalid_workspace")
        settings = cls._settings(None)
        provider = str(settings.get("provider") or "openai").lower()
        try:
            if provider == "gemini":
                from google import genai
                from google.genai import types

                client = genai.Client(api_key=str(settings["api_key"]))
                try:
                    part = types.Part.from_bytes(data=bytes(data), mime_type=str(mime_type or "image/jpeg"))
                    response = _gemini_generate(
                        client,
                        model=str(settings.get("model") or DEFAULT_GEMINI_MODEL),
                        contents=[part, str(prompt or "")],
                        config=types.GenerateContentConfig(
                            system_instruction="Phân tích đúng hình ảnh được cung cấp. Không suy đoán chi tiết không nhìn thấy."
                        ),
                    )
                    return str(getattr(response, "text", "") or "").strip()
                finally:
                    try:
                        client.close()
                    except Exception:
                        pass

            from openai import OpenAI

            encoded = base64.b64encode(bytes(data)).decode("ascii")
            client = OpenAI(api_key=str(settings["api_key"]))
            response = client.responses.create(
                model=str(settings.get("model") or "gpt-5-mini"),
                store=False,
                input=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_image", "image_url": f"data:{mime_type};base64,{encoded}"},
                            {"type": "input_text", "text": str(prompt or "")},
                        ],
                    }
                ],
            )
            return str(getattr(response, "output_text", "") or "").strip()
        except AIProviderError:
            raise
        except Exception as exc:
            raise _friendly_error(exc) from exc


__all__ = ["NativeProviderGateway", "AIProviderError"]
