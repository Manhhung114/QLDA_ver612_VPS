from __future__ import annotations

import base64
import json
from datetime import date
from typing import Any, Sequence

from qlda.infrastructure.ai.provider_settings import get_provider_settings


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


def _friendly_error(exc: BaseException) -> AIProviderError:
    text = str(exc or "").strip()
    lower = text.lower()
    code = exc.__class__.__name__ or "ai_error"
    retryable = any(token in lower for token in ("timeout", "temporar", "429", "rate limit", "503", "overloaded"))
    action = ""
    if any(token in lower for token in ("401", "unauthorized", "invalid api key", "api_key")):
        code = "invalid_api_key"
        action = "Kiểm tra API key trong Cài đặt hệ thống."
    elif any(token in lower for token in ("429", "rate limit", "quota")):
        code = "rate_limit"
        action = "Thử lại sau hoặc kiểm tra quota của nhà cung cấp AI."
    elif any(token in lower for token in ("timeout", "timed out")):
        code = "timeout"
        action = "Thử lại yêu cầu."
    return AIProviderError(text or "Nhà cung cấp AI trả về lỗi không xác định.", code=code, retryable=retryable, action=action)


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
                    response = client.models.generate_content(
                        model=model or "gemini-2.5-flash",
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
                    response = client.models.generate_content(
                        model=str(settings.get("model") or "gemini-2.5-flash"),
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
