from __future__ import annotations

import re
import tempfile
import time
import unicodedata
from pathlib import Path
from typing import Any

import contract_management_v622 as cm
from ai_service import AIServiceError
from settings_store import get_ai_runtime_settings


PATCH_MARKER = "V6.22 CONTRACT AI DEEP SCAN"
SCAN_MAX_FILES = 40
SCAN_MAX_TOTAL_BYTES = 640 * 1024 * 1024
MAX_TEXT_EVIDENCE_CHARS = 120_000
_INSTALLED = False
_ORIGINAL_ASK_CONTRACT_AI = cm.ask_contract_ai

_STOPWORDS = {
    "cua", "cho", "voi", "the", "nao", "nhu", "la", "co", "ve", "va", "hay", "trong",
    "hop", "dong", "phu", "luc", "quy", "dinh", "noi", "dung", "thong", "tin", "duoc",
    "293", "2024", "scg", "sigma",
}

_DOMAIN_EXPANSIONS = {
    "phat": {"phat", "vi pham", "che tai", "boi thuong", "khau tru", "phan tram", "%"},
    "tien do": {"tien do", "cham tien do", "tre", "gia han", "thoi gian thi cong", "hoan thanh"},
    "thanh toan": {"thanh toan", "tam ung", "quyet toan", "hoa don", "bao lanh", "giu lai"},
    "bao hanh": {"bao hanh", "bao tri", "khuyet tat", "thoi han bao hanh"},
    "cham dut": {"cham dut", "huy hop dong", "don phuong", "tam ngung"},
}


def _plain(value: Any) -> str:
    text = unicodedata.normalize("NFD", str(value or "").lower())
    return "".join(ch for ch in text if unicodedata.category(ch) != "Mn")


def _question_terms(question: str) -> list[str]:
    plain = _plain(question)
    words = [x for x in re.findall(r"[a-z0-9%]+", plain) if len(x) >= 3 and x not in _STOPWORDS]
    terms: set[str] = set(words)
    for key, extras in _DOMAIN_EXPANSIONS.items():
        if _plain(key) in plain:
            terms.update(_plain(x) for x in extras)
    return sorted(terms, key=len, reverse=True)


def _original_page_start(name: str) -> int:
    match = re.search(r"__part_\d+_pages_(\d+)-(\d+)_of_\d+\.pdf$", str(name or ""), re.I)
    return int(match.group(1)) if match else 1


def _source_tag(name: str, records: list[dict[str, Any]]) -> str:
    candidate = str(name or "")
    low = candidate.lower()
    for row in records:
        original = cm._text(row.get("current_file_name"))
        stem = Path(original).stem.lower() if original else ""
        if original and (low == original.lower() or (stem and low.startswith(stem + "__part_"))):
            return f"[HĐ:{row.get('id')}|{cm._text(row.get('record_no'))}|{original}]"
    return f"[FILE|{candidate}]"


def _extract_pdf_text_evidence(name: str, data: bytes, question: str) -> str:
    if Path(name).suffix.lower() != ".pdf" or not data:
        return ""
    try:
        from pypdf import PdfReader
        import io

        reader = PdfReader(io.BytesIO(data), strict=False)
    except Exception:
        return ""

    pages: list[tuple[int, str, int]] = []
    terms = _question_terms(question)
    page_start = _original_page_start(name)
    total_text = 0
    for idx, page in enumerate(reader.pages):
        try:
            text = (page.extract_text() or "").strip()
        except Exception:
            text = ""
        if not text:
            continue
        total_text += len(text)
        norm = _plain(text)
        score = sum(norm.count(term) * (3 if " " in term else 1) for term in terms)
        pages.append((page_start + idx, text, score))

    # A tiny OCR/text layer is often page numbers or stamps only; do not mistake it
    # for searchable contract content.
    if total_text < 800 or not pages:
        return ""

    scored = [item for item in pages if item[2] > 0]
    if not scored:
        return ""

    top = sorted(scored, key=lambda item: (item[2], len(item[1])), reverse=True)[:14]
    wanted = {p for p, _text, _score in top}
    # Include the neighboring page because clauses commonly continue across pages.
    wanted.update(p - 1 for p in list(wanted))
    wanted.update(p + 1 for p in list(wanted))

    blocks: list[str] = []
    used = 0
    for page_no, text, _score in pages:
        if page_no not in wanted:
            continue
        block = f"\n--- Trang {page_no} ---\n{text}\n"
        if used + len(block) > MAX_TEXT_EVIDENCE_CHARS:
            remain = MAX_TEXT_EVIDENCE_CHARS - used
            if remain > 500:
                blocks.append(block[:remain])
            break
        blocks.append(block)
        used += len(block)
    return "".join(blocks).strip()


def _scan_instruction(question: str, tag: str, name: str) -> str:
    return f"""Bạn đang đọc TRỰC TIẾP một phần của file hợp đồng xây dựng.
Nguồn: {tag}
Tệp/phần: {name}
Câu hỏi cần tra cứu: {question}

Hãy quét kỹ toàn bộ phần tài liệu được gửi trong lượt này, kể cả trang scan/hình ảnh.
Chỉ trả về bằng chứng liên quan trực tiếp đến câu hỏi: Điều/Mục, nội dung quy định, mức tiền/%/số ngày, điều kiện áp dụng và số trang nếu xác định được.
Nếu có nội dung liên quan, trích dẫn ngắn phần then chốt rồi diễn giải chính xác.
Nếu thực sự không có nội dung liên quan trong phần này, trả lời đúng: KHÔNG TÌM THẤY TRONG PHẦN NÀY.
Không dùng metadata để thay thế nội dung file và không suy đoán."""


def _wait_gemini_file(client, uploaded):
    """Wait only when the Files API reports an explicit processing state."""
    current = uploaded
    for _ in range(30):
        state = getattr(current, "state", None)
        state_name = cm._text(getattr(state, "name", state)).upper()
        if not state_name or state_name in {"ACTIVE", "STATE_UNSPECIFIED", "UNSPECIFIED"}:
            return current
        if "FAIL" in state_name:
            raise AIServiceError(f"Gemini không xử lý được file: trạng thái {state_name}.")
        if "PROCESS" not in state_name and "PENDING" not in state_name:
            return current
        time.sleep(2)
        current = client.files.get(name=current.name)
    return current


def _scan_gemini_file(settings: dict[str, Any], name: str, data: bytes, prompt: str) -> str:
    assistant = cm.GeminiProjectAssistant(
        Path("."),
        cm.GeminiSettings(
            api_key=cm._text(settings.get("api_key")),
            model=cm._text(settings.get("model")) or "auto",
            use_web=False,
        ),
    )
    client = assistant._client()
    uploaded = None
    temp_path = ""
    try:
        from google.genai import types

        suffix = Path(name).suffix or ".bin"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(data)
            temp_path = tmp.name
        uploaded = client.files.upload(file=temp_path)
        uploaded = _wait_gemini_file(client, uploaded)
        response = assistant._generate_content_with_fallback(
            client,
            contents=[uploaded, prompt],
            config=types.GenerateContentConfig(system_instruction=cm.CONTRACT_AI_SYSTEM),
        )
        return (getattr(response, "text", "") or "").strip()
    except AIServiceError:
        raise
    except Exception as exc:
        raise cm.gemini_error_to_service_error(exc) from exc
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


def _scan_provider_file(provider: str, settings: dict[str, Any], name: str, data: bytes, prompt: str) -> str:
    if provider == "gemini":
        return _scan_gemini_file(settings, name, data, prompt)
    return cm._ask_openai(settings, prompt, [(name, data)])


def _final_answer(provider: str, settings: dict[str, Any], prompt: str) -> str:
    if provider == "gemini":
        return cm._ask_gemini(settings, prompt, [])
    return cm._ask_openai(settings, prompt, [])


def ask_contract_ai_deep_scan(
    db,
    gateway,
    session_token: str,
    *,
    workspace_project_id: int,
    question: str,
) -> str:
    question = cm._text(question)
    if not question:
        raise ValueError("Hãy nhập câu hỏi về hợp đồng/phụ lục.")

    records = cm.list_contract_records(db, int(workspace_project_id))
    if not records:
        raise ValueError("Workspace hiện chưa có hợp đồng/phụ lục để AI tra cứu.")
    if gateway is None or not session_token:
        raise RuntimeError("Chưa có phiên lưu trữ để AI đọc file hợp đồng.")

    files, skipped = cm._collect_ai_files(gateway, session_token, records)
    records_with_file = [r for r in records if cm._text(r.get("current_file_id"))]
    if records_with_file and not files:
        reason = "; ".join(skipped) or "không tải được dữ liệu file từ kho VPS"
        raise AIServiceError(
            "AI chưa nhận được nội dung file hợp đồng nên không trả lời bằng metadata thay thế. "
            f"Chi tiết: {reason}"
        )

    settings = get_ai_runtime_settings()
    if not cm._text(settings.get("api_key")):
        raise AIServiceError("Chưa cấu hình API key cho AI trên máy chủ.")
    provider = cm._text(settings.get("provider")).lower()
    if provider not in {"gemini", "openai"}:
        provider = "openai"

    evidence: list[str] = []
    scan_errors: list[str] = []
    for index, (name, data) in enumerate(files, 1):
        tag = _source_tag(name, records)

        # Fast/reliable path for searchable PDFs: extract the actual page text
        # locally and preserve original page numbers in the evidence sent to AI.
        text_evidence = _extract_pdf_text_evidence(name, data, question)
        if text_evidence:
            evidence.append(
                f"NGUỒN {index}: {tag}\nTệp/phần: {name}\n"
                f"NỘI DUNG TRÍCH TRỰC TIẾP TỪ PDF:\n{text_evidence}"
            )
            continue

        # Scanned/image PDFs (or other supported files) are read directly by the
        # configured multimodal provider one part at a time. This avoids silently
        # dropping late pages of a large contract because of a total-byte cap.
        try:
            result = _scan_provider_file(
                provider,
                settings,
                name,
                data,
                _scan_instruction(question, tag, name),
            )
            if result:
                evidence.append(
                    f"NGUỒN {index}: {tag}\nTệp/phần: {name}\nKẾT QUẢ QUÉT TRỰC TIẾP:\n{result}"
                )
            else:
                scan_errors.append(f"{name}: AI không trả về kết quả quét")
        except Exception as exc:
            scan_errors.append(f"{name}: {exc}")

    if files and not evidence:
        detail = "; ".join(scan_errors[:8]) or "không thu được bằng chứng từ file"
        raise AIServiceError(
            "AI đã nhận file nhưng chưa đọc được nội dung hợp đồng. "
            f"Chi tiết kỹ thuật: {detail}"
        )

    metadata = "\n".join(cm._record_metadata_lines(records)) or "Không có metadata."
    evidence_text = "\n\n==========\n\n".join(evidence) or "Không có file nội dung để quét."
    warnings: list[str] = []
    if skipped:
        warnings.append("Một số phần/file không được nạp: " + "; ".join(skipped))
    if scan_errors:
        warnings.append("Một số phần phát sinh lỗi khi quét: " + "; ".join(scan_errors[:8]))
    warning_text = "\n".join(warnings) if warnings else "Không có lỗi quét."

    final_prompt = f"""CÂU HỎI NGƯỜI DÙNG:
{question}

METADATA HỢP ĐỒNG (chỉ để nhận diện hồ sơ):
{metadata}

BẰNG CHỨNG ĐÃ QUÉT TỪ NỘI DUNG FILE:
{evidence_text}

TRẠNG THÁI QUÉT:
{warning_text}

Hãy trả lời câu hỏi bằng tiếng Việt dựa chủ yếu trên BẰNG CHỨNG ĐÃ QUÉT TỪ NỘI DUNG FILE.
- Nêu rõ Điều/Mục, mức phạt/tỷ lệ/số ngày/điều kiện nếu bằng chứng có.
- Gắn nguồn dạng [HĐ:id|số hồ sơ|tên file] và số trang khi có.
- Nếu các phần đã quét đều ghi KHÔNG TÌM THẤY thì nói rõ đã quét nhưng chưa tìm thấy điều khoản, không được nói chung chung rằng file "chưa đọc được".
- Nếu BẰNG CHỨNG có nội dung trích từ PDF thì tuyệt đối không được kết luận rằng file chưa được đọc.
- Không suy đoán ngoài tài liệu."""
    return _final_answer(provider, settings, final_prompt)


def install_contract_ai_deep_scan_v622() -> None:
    global _INSTALLED
    if _INSTALLED or getattr(cm, "_V622_CONTRACT_AI_DEEP_SCAN_INSTALLED", False):
        return

    # The deep scanner processes file parts sequentially, so the former 80 MB
    # aggregate request cap is no longer needed. Per-part safety is still enforced
    # by contract_ai_large_pdf_v622 and the maximum part count remains bounded.
    cm.AI_MAX_FILES = max(int(getattr(cm, "AI_MAX_FILES", 20) or 20), SCAN_MAX_FILES)
    cm.AI_MAX_TOTAL_BYTES = max(
        int(getattr(cm, "AI_MAX_TOTAL_BYTES", 80 * 1024 * 1024) or 0),
        SCAN_MAX_TOTAL_BYTES,
    )
    cm.ask_contract_ai = ask_contract_ai_deep_scan
    cm.PATCH_MARKER = f"{cm.PATCH_MARKER} + {PATCH_MARKER}"
    cm._V622_CONTRACT_AI_DEEP_SCAN_INSTALLED = True
    _INSTALLED = True


__all__ = [
    "PATCH_MARKER",
    "ask_contract_ai_deep_scan",
    "install_contract_ai_deep_scan_v622",
]
