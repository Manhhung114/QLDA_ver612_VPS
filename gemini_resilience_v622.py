from __future__ import annotations

import os
import random
import threading
import time
from typing import Any

import ai_service as _ai

PATCH_VERSION = "V6.22 GEMINI RESILIENCE V2"

_LOCK = threading.Lock()
_MODEL_COOLDOWN_UNTIL: dict[str, float] = {}
_INSTALLED = False


def _env_int(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(str(os.environ.get(name, default)).strip())
    except Exception:
        value = default
    return max(low, min(high, value))


def _env_float(name: str, default: float, low: float, high: float) -> float:
    try:
        value = float(str(os.environ.get(name, default)).strip())
    except Exception:
        value = default
    return max(low, min(high, value))


def _status_code(exc: Exception) -> int | None:
    raw = getattr(exc, "status_code", None)
    if raw is None:
        raw = getattr(exc, "code", None)
    try:
        return int(raw) if raw is not None else None
    except Exception:
        return None


def _cooldown_remaining(model: str) -> float:
    now = time.monotonic()
    with _LOCK:
        until = float(_MODEL_COOLDOWN_UNTIL.get(str(model), 0.0) or 0.0)
        if until <= now:
            _MODEL_COOLDOWN_UNTIL.pop(str(model), None)
            return 0.0
        return until - now


def _mark_cooldown(model: str, seconds: float) -> None:
    if seconds <= 0:
        return
    with _LOCK:
        _MODEL_COOLDOWN_UNTIL[str(model)] = max(
            float(_MODEL_COOLDOWN_UNTIL.get(str(model), 0.0) or 0.0),
            time.monotonic() + float(seconds),
        )


def _preferred_model_candidates() -> list[str]:
    """Ưu tiên model production và phân tán qua nhiều dòng Gemini.

    Thứ tự cũ đặt 3.7/3.6/3.5 liên tiếp. Với giới hạn 4 fallback, các model
    stable 2.5 thường không bao giờ được thử khi cụm model mới cùng quá tải.
    Danh sách này cố ý xen kẽ Flash / Flash-Lite và stable 2.5.
    """
    return [
        "gemini-3.6-flash",
        "gemini-2.5-flash",
        "gemini-3.1-flash-lite",
        "gemini-2.5-flash-lite",
        "gemini-3.7-flash",
        "gemini-3.5-flash",
        "gemini-3.5-flash-lite",
        "gemini-flash-lite-latest",
    ]


def _fallback_model_sequence(self, client, *, refresh: bool = False) -> list[str]:
    configured = self._normalize_model_name(
        (self.settings.model or os.environ.get("GEMINI_MODEL", "auto")).strip() or "auto"
    )
    try:
        available = self._available_generate_models(client, refresh=refresh)
    except Exception:
        available = []

    result: list[str] = []

    def add(candidate: str) -> None:
        candidate = self._normalize_model_name(candidate)
        if not candidate or candidate in result:
            return
        if available and candidate not in available:
            return
        result.append(candidate)

    # Nếu người dùng khóa model cụ thể, thử model đó trước nhưng vẫn có fallback.
    if configured.lower() not in {"", "auto", "default"}:
        add(configured)

    for candidate in _preferred_model_candidates():
        add(candidate)

    # Cuối cùng mới dùng các model generateContent còn lại do chính API key liệt kê.
    for candidate in available:
        low = str(candidate).lower()
        if "gemini" not in low:
            continue
        if any(x in low for x in ("image", "live", "tts", "embed", "embedding", "transcribe")):
            continue
        add(candidate)

    if not result:
        add(configured if configured.lower() not in {"", "auto", "default"} else "gemini-2.5-flash")
    if not result:
        result = ["gemini-2.5-flash", "gemini-2.5-flash-lite"]

    # Circuit breaker: model vừa 5xx/429 được đẩy xuống cuối trong các request sau.
    active = [m for m in result if _cooldown_remaining(m) <= 0]
    cooling = [m for m in result if m not in active]
    return active + cooling if active else result


def _generate_content_with_fallback(self, client, *, contents, config):
    """Gemini routing chịu lỗi 503/rate-limit cho VPS production.

    - Phân tán fallback qua model stable / Flash-Lite thay vì chỉ các model mới.
    - Retry ngắn theo exponential backoff + jitter.
    - Model trả 5xx/429 được cooldown để request kế tiếp không đập lại ngay.
    - Sau lỗi model đầu tiên, refresh danh sách model từ API một lần.
    """
    retry_attempts = _env_int("GEMINI_RETRY_ATTEMPTS", 2, 1, 4)
    base_delay = _env_float("GEMINI_RETRY_BASE_SECONDS", 0.65, 0.15, 5.0)
    max_models = _env_int("GEMINI_MAX_FALLBACK_MODELS", 5, 2, 8)
    cooldown_seconds = _env_float("GEMINI_MODEL_COOLDOWN_SECONDS", 90.0, 5.0, 600.0)
    rate_cooldown = _env_float("GEMINI_RATE_COOLDOWN_SECONDS", 30.0, 5.0, 300.0)

    models = list(_fallback_model_sequence(self, client))[:max_models]
    last_exc: Exception | None = None
    attempted: list[str] = []

    model_index = 0
    while model_index < len(models) and model_index < max_models:
        model = models[model_index]
        model_index += 1
        self._resolved_model = model
        if model not in attempted:
            attempted.append(model)

        for attempt in range(retry_attempts):
            try:
                return client.models.generate_content(model=model, contents=contents, config=config)
            except Exception as exc:
                last_exc = exc
                info = _ai.classify_gemini_error(exc)
                status = _status_code(exc)

                if info.code == "model_not_found":
                    _mark_cooldown(model, 300.0)
                    break

                if info.code not in {"server_error", "rate_limit", "timeout", "network"}:
                    raise

                if info.code == "server_error" or status in {500, 502, 503, 504}:
                    _mark_cooldown(model, cooldown_seconds)
                elif info.code == "rate_limit" or status == 429:
                    _mark_cooldown(model, rate_cooldown)

                if attempt < retry_attempts - 1:
                    delay = base_delay * (2 ** attempt) + random.uniform(0.0, base_delay * 0.30)
                    time.sleep(delay)
                    continue
                break

        # Refresh routing once. New model names are appended, not allowed to
        # displace the stable models already selected above.
        if model_index == 1 and last_exc is not None:
            try:
                refreshed = _fallback_model_sequence(self, client, refresh=True)
                for candidate in refreshed:
                    if candidate not in models:
                        models.append(candidate)
                models = models[:max_models]
            except Exception:
                pass

    if last_exc is not None:
        info = _ai.classify_gemini_error(last_exc)
        if info.code == "server_error":
            tried = ", ".join(attempted) or "các model khả dụng"
            raise _ai.AIServiceError(
                "🛠️ Gemini API đang quá tải tạm thời.\n\n"
                f"Ứng dụng đã tự retry và thử {len(attempted)} model dự phòng: {tried}.\n\n"
                "Cách xử lý: thử gửi lại sau 30–60 giây. Model vừa lỗi đã được đưa vào cooldown để request kế tiếp tự ưu tiên model khác.\n\n"
                f"Mã HTTP: {_status_code(last_exc) or 503}",
                code="server_error",
                title="Gemini tạm thời quá tải",
                retryable=True,
                action="Thử lại sau 30–60 giây; ứng dụng sẽ tự chuyển model dự phòng.",
            ) from last_exc
        raise last_exc

    raise _ai.AIServiceError(
        "Gemini không có model generateContent khả dụng cho API key hiện tại.",
        code="model_not_found",
        title="Không có Gemini model khả dụng",
    )


def install_gemini_resilience() -> bool:
    global _INSTALLED
    if _INSTALLED:
        return False
    cls = _ai.GeminiProjectAssistant
    cls._preferred_model_candidates = staticmethod(_preferred_model_candidates)
    cls._fallback_model_sequence = _fallback_model_sequence
    cls._generate_content_with_fallback = _generate_content_with_fallback
    cls._qlda_gemini_resilience_installed = True
    _INSTALLED = True
    return True


install_gemini_resilience()
