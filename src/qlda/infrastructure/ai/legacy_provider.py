from __future__ import annotations

import base64
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any


class AIProviderError(RuntimeError):
    def __init__(self, message: str, *, code: str = "ai_error", retryable: bool = False, action: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable
        self.action = action


class LegacyAIProvider:
    """Single infrastructure compatibility adapter for the packaged AI engine.

    No application/autonomy/native adapter should import the legacy provider engine
    or Admin AI settings store directly. This module is the shrinking strangler
    boundary until the provider/context patches are migrated slice by slice.
    """

    @staticmethod
    def db_label() -> Path:
        return Path(
            os.environ.get(
                "QLDA_AI_DB_LABEL",
                os.environ.get("QLDA_WORKER_DB_LABEL", "/opt/qlda/shared/qlda-ai.db"),
            )
        )

    @staticmethod
    def _modules():
        from qlda.runtime_core import ai_service as engine
        from qlda.runtime_core import contractor_access_control as access
        from qlda.runtime_core.bootstrap import initialize_ai_runtime

        initialize_ai_runtime()
        return engine, access

    @classmethod
    def assistant(cls, provider: str):
        engine, _access = cls._modules()
        value = str(provider or "openai").strip().lower()
        if value in {"openai", "gpt"}:
            return engine.OpenAIProjectAssistant(cls.db_label())
        if value in {"gemini", "google"}:
            return engine.GeminiProjectAssistant(cls.db_label())
        raise ValueError("provider phải là openai hoặc gemini.")

    @classmethod
    @contextmanager
    def scope(cls, workspace_scope: int | None):
        _engine, access = cls._modules()
        access.set_ai_workspace_scope(workspace_scope)
        try:
            yield
        finally:
            access.set_ai_workspace_scope(None)

    @classmethod
    def run(
        cls,
        provider: str,
        method: str,
        workspace_scope: int | None,
        *args: Any,
        **kwargs: Any,
    ):
        engine, _access = cls._modules()
        assistant = cls.assistant(provider)
        try:
            with cls.scope(workspace_scope):
                return getattr(assistant, method)(*args, **kwargs)
        except engine.AIServiceError as exc:
            raise AIProviderError(
                str(exc),
                code=str(getattr(exc, "code", "ai_error") or "ai_error"),
                retryable=bool(getattr(exc, "retryable", False)),
                action=str(getattr(exc, "action", "") or ""),
            ) from exc

    @classmethod
    def vision_text(
        cls,
        workspace_scope: int,
        *,
        data: bytes,
        mime_type: str,
        prompt: str,
    ) -> str:
        """Run one image prompt using Admin-managed provider settings.

        This keeps Site Vision outside direct ``runtime_core`` imports while the
        legacy provider engine is still being strangled.
        """
        from qlda.runtime_core.settings_store import get_ai_runtime_settings

        engine, _access = cls._modules()
        settings = dict(get_ai_runtime_settings() or {})
        api_key = str(settings.get("api_key") or "").strip()
        if not api_key:
            raise AIProviderError("Chưa cấu hình API key cho AI Vision.", code="missing_api_key")
        provider = str(settings.get("provider") or "openai").strip().lower()
        model = str(settings.get("model") or "").strip()

        try:
            with cls.scope(int(workspace_scope)):
                if provider in {"gemini", "google"}:
                    from google.genai import types

                    assistant = engine.GeminiProjectAssistant(
                        Path("."),
                        engine.GeminiSettings(api_key=api_key, model=model or "auto", use_web=False),
                    )
                    client = assistant._client()
                    try:
                        part = types.Part.from_bytes(data=bytes(data), mime_type=str(mime_type))
                        response = assistant._generate_content_with_fallback(
                            client,
                            contents=[part, str(prompt)],
                            config=None,
                        )
                        return str(getattr(response, "text", "") or "")
                    finally:
                        try:
                            client.close()
                        except Exception:
                            pass

                assistant = engine.OpenAIProjectAssistant(
                    Path("."),
                    engine.AISettings(api_key=api_key, model=model or "gpt-5-mini", use_web=False),
                )
                client = assistant._client()
                try:
                    encoded = base64.b64encode(bytes(data)).decode("ascii")
                    response = client.responses.create(
                        model=assistant.model,
                        store=False,
                        input=[
                            {
                                "role": "user",
                                "content": [
                                    {"type": "input_image", "image_url": f"data:{mime_type};base64,{encoded}"},
                                    {"type": "input_text", "text": str(prompt)},
                                ],
                            }
                        ],
                    )
                    return str(getattr(response, "output_text", "") or "")
                finally:
                    try:
                        client.close()
                    except Exception:
                        pass
        except engine.AIServiceError as exc:
            raise AIProviderError(
                str(exc),
                code=str(getattr(exc, "code", "ai_error") or "ai_error"),
                retryable=bool(getattr(exc, "retryable", False)),
                action=str(getattr(exc, "action", "") or ""),
            ) from exc


__all__ = ["LegacyAIProvider", "AIProviderError"]
