from __future__ import annotations

"""Provider-neutral PDF vision reader for Quản lý hồ sơ attachments.

Unlike the contract-specific AI pipeline, this reader is intentionally generic:
it may read NCR/RFI/RFA/BBHT/NTCV/NTVL/KDVT/BBHOP and any other document
subtype stored under ``kind=document``. It reuses the application's configured
OpenAI/Gemini provider and never creates a separate AI configuration.
"""

import tempfile
import time
from pathlib import Path
from typing import Any

from qlda.runtime_core.ai_service import (
    AIServiceError,
    AISettings,
    GeminiProjectAssistant,
    GeminiSettings,
    OpenAIProjectAssistant,
    gemini_error_to_service_error,
    openai_error_to_service_error,
)


DOCUMENT_VISION_SYSTEM = """Bạn là bộ đọc tài liệu đa phương thức của hệ thống QLDA xây dựng.
Nhiệm vụ duy nhất ở lượt này là đọc chính xác file hồ sơ được cung cấp, bất kể PDF có lớp text hay chỉ là ảnh scan.
Bạn được phép đọc mọi loại hồ sơ trong Quản lý hồ sơ: NCR, RFI, RFA, biên bản hiện trường, biên bản họp, nghiệm thu công việc, nghiệm thu vật liệu, kiểm định vật tư và các loại hồ sơ tương tự.
Không giới hạn nội dung vào hợp đồng. Không dùng metadata thay cho nội dung file. Không suy đoán chữ hoặc số không nhìn rõ.
Khi được yêu cầu trích xuất, phải đọc cả chữ in, bảng biểu, ghi chú, ngày tháng, số liệu, phân công và kết luận nhìn thấy trên trang.
"""


def _text(value: Any) -> str:
    return str(value or "").strip()


def _wait_gemini_file(client, uploaded):
    current = uploaded
    for _ in range(40):
        state = getattr(current, "state", None)
        state_name = _text(getattr(state, "name", state)).upper()
        if not state_name or state_name in {"ACTIVE", "STATE_UNSPECIFIED", "UNSPECIFIED"}:
            return current
        if "FAIL" in state_name:
            raise AIServiceError(f"Gemini không xử lý được PDF: trạng thái {state_name}.")
        if "PROCESS" not in state_name and "PENDING" not in state_name:
            return current
        time.sleep(1.5)
        current = client.files.get(name=current.name)
    return current


def _scan_openai(settings: dict[str, Any], name: str, data: bytes, prompt: str) -> str:
    assistant = OpenAIProjectAssistant(
        Path("."),
        AISettings(
            api_key=_text(settings.get("api_key")),
            model=_text(settings.get("model")) or "gpt-5-mini",
            use_web=False,
        ),
    )
    client = assistant._client()
    uploaded_id = None
    temp_path = ""
    try:
        suffix = Path(name).suffix or ".pdf"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(bytes(data))
            temp_path = tmp.name
        with open(temp_path, "rb") as handle:
            uploaded = client.files.create(file=handle, purpose="user_data")
        uploaded_id = uploaded.id
        response = client.responses.create(
            model=assistant.model,
            store=False,
            input=[
                {"role": "developer", "content": DOCUMENT_VISION_SYSTEM},
                {
                    "role": "user",
                    "content": [
                        {"type": "input_file", "file_id": uploaded_id},
                        {"type": "input_text", "text": prompt},
                    ],
                },
            ],
        )
        return (getattr(response, "output_text", "") or "").strip()
    except AIServiceError:
        raise
    except Exception as exc:
        raise openai_error_to_service_error(exc) from exc
    finally:
        if uploaded_id:
            try:
                client.files.delete(uploaded_id)
            except Exception:
                pass
        if temp_path:
            try:
                Path(temp_path).unlink(missing_ok=True)
            except Exception:
                pass
        try:
            client.close()
        except Exception:
            pass


def _scan_gemini(settings: dict[str, Any], name: str, data: bytes, prompt: str) -> str:
    assistant = GeminiProjectAssistant(
        Path("."),
        GeminiSettings(
            api_key=_text(settings.get("api_key")),
            model=_text(settings.get("model")) or "auto",
            use_web=False,
        ),
    )
    client = assistant._client()
    uploaded = None
    temp_path = ""
    try:
        from google.genai import types

        suffix = Path(name).suffix or ".pdf"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(bytes(data))
            temp_path = tmp.name
        uploaded = client.files.upload(file=temp_path)
        uploaded = _wait_gemini_file(client, uploaded)
        response = assistant._generate_content_with_fallback(
            client,
            contents=[uploaded, prompt],
            config=types.GenerateContentConfig(system_instruction=DOCUMENT_VISION_SYSTEM),
        )
        return (getattr(response, "text", "") or "").strip()
    except AIServiceError:
        raise
    except Exception as exc:
        raise gemini_error_to_service_error(exc) from exc
    finally:
        if uploaded is not None:
            try:
                client.files.delete(name=uploaded.name)
            except Exception:
                pass
        if temp_path:
            try:
                Path(temp_path).unlink(missing_ok=True)
            except Exception:
                pass
        try:
            client.close()
        except Exception:
            pass


def scan_document_pdf(provider: str, settings: dict[str, Any], name: str, data: bytes, prompt: str) -> str:
    """Read one PDF/PDF-part with the application's configured multimodal AI."""
    provider = _text(provider).lower()
    if provider == "gemini":
        return _scan_gemini(settings, name, data, prompt)
    return _scan_openai(settings, name, data, prompt)


__all__ = ["DOCUMENT_VISION_SYSTEM", "scan_document_pdf"]
