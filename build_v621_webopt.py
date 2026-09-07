from __future__ import annotations

import base64
import gzip
from pathlib import Path


APP_VERSION_LABEL = "QLDA Xây dựng V6.22 PostgreSQL Cloud"
REQUIRED_MARKERS = (
    APP_VERSION_LABEL,
    "from streamlit_secrets_v622 import apply_streamlit_secrets_to_env",
    "from postgres_backend_v622 import install_postgres_backend",
    "from v621_webopt_runtime import install_runtime",
    "approval_workflows_for_records",
    "@st.fragment\ndef _render_online_approval",
    "@st.fragment\ndef _render_inline_drive_attachments",
    "Hiển thị biểu đồ Gantt",
    "def _paged_df",
    "def _render_excel_export",
    "class _LazyPlotlyExpress",
    "Tạo Excel tiến độ",
    "st.write_stream(",
    "ask_project_stream(",
    "install_ai_streaming()",
    "def _ai_typewriter_stream",
)

HIDDEN_UI_NOTE_MARKERS = (
    "Sau khi tải file lớn xong, quay lại app",
    "Sau khi tải xong, quay lại app và bấm Làm mới file / File DB",
    "File lớn hơn giới hạn trên: dùng 'Mở trình tải file ở tab riêng'",
    "Workflow engine: **V6.21 WebOpt** • Nhà thầu trình",
    "Workflow engine: **V6.22 PostgreSQL Cloud** • Nhà thầu trình",
    "Nếu một cấp yêu cầu chỉnh sửa: hồ sơ quay về Nhà thầu",
    "V6.9 không dùng nút Trình lại riêng",
    "Approval UI / Workflow engine: V6.21",
    "Approval UI / Workflow engine: V6.22",
    "Nhập đúng Mã hồ sơ và Nội dung trình duyệt trước khi đính kèm file",
    "Nhập đúng Mã bản vẽ và Nội dung/Tên bản vẽ trước khi đính kèm file",
)

FORBIDDEN_UI_NOTE_MARKERS = HIDDEN_UI_NOTE_MARKERS


def _inject_runtime_bootstrap(source: str) -> str:
    """Load Streamlit secrets, then PostgreSQL, then WebOpt runtime.

    Legacy modules read ``os.environ`` directly. On Streamlit Community Cloud we
    bridge root ``st.secrets`` into the environment before importing them so the
    existing Gemini, Drive and database behaviour is preserved.
    """
    marker = "# V6.22 BOOTSTRAP START"
    if marker in source:
        return source

    block = (
        "# V6.22 BOOTSTRAP START\n"
        "from streamlit_secrets_v622 import apply_streamlit_secrets_to_env\n"
        "apply_streamlit_secrets_to_env()\n"
        "from postgres_backend_v622 import install_postgres_backend\n"
        "install_postgres_backend()\n"
        "from v621_webopt_runtime import install_runtime\n"
        "install_runtime()\n"
        "# V6.22 BOOTSTRAP END\n"
    )
    future_anchor = "from __future__ import annotations\n"
    if future_anchor in source:
        return source.replace(future_anchor, future_anchor + "\n" + block, 1)
    return block + "\n" + source


def _validate_bootstrap_order(source: str) -> None:
    """Fail build if secrets/runtime installers can execute before import."""
    sec_import = source.find("from streamlit_secrets_v622 import apply_streamlit_secrets_to_env")
    sec_call = source.find("apply_streamlit_secrets_to_env()")
    pg_import = source.find("from postgres_backend_v622 import install_postgres_backend")
    pg_call = source.find("install_postgres_backend()")
    rt_import = source.find("from v621_webopt_runtime import install_runtime")
    rt_call = source.find("install_runtime()")
    positions = (sec_import, sec_call, pg_import, pg_call, rt_import, rt_call)
    if min(positions) < 0:
        raise RuntimeError("V6.22 bootstrap incomplete")
    if not (sec_import < sec_call < pg_import < pg_call < rt_import < rt_call):
        raise RuntimeError(
            "V6.22 bootstrap order invalid: secrets -> PostgreSQL -> WebOpt required"
        )


def _finalize_source(source: str) -> str:
    # V6.22 keeps the V6.21 WebOpt UI/workflow while switching the durable DB
    # backend to PostgreSQL when DATABASE_URL is configured.
    source = source.replace("QLDA Xây dựng V6.21 WebOpt", APP_VERSION_LABEL)
    source = source.replace("QLDA Xây dựng V6.0", APP_VERSION_LABEL)
    source = source.replace("Workflow engine: **V6.21**", "Workflow engine: **V6.22 PostgreSQL Cloud**")
    source = source.replace("Approval UI / Workflow engine: V6.21", "Approval UI / Workflow engine: V6.22 PostgreSQL Cloud")

    source = _inject_runtime_bootstrap(source)
    _validate_bootstrap_order(source)

    typewriter_helper = r'''def _ai_typewriter_stream(stream):
    """Chia chunk AI lớn thành cụm nhỏ để luôn hiển thị dần trên Streamlit."""
    import re as _re
    import time as _time
    try:
        _words = max(1, min(6, int(os.environ.get("AI_STREAM_WORDS_PER_CHUNK", "2"))))
    except Exception:
        _words = 2
    try:
        _delay = max(0.0, min(0.08, float(os.environ.get("AI_STREAM_DELAY_MS", "12")) / 1000.0))
    except Exception:
        _delay = 0.012
    for _chunk in stream:
        _text = str(_chunk or "")
        if not _text:
            continue
        _parts = _re.findall(r"\S+\s*|\s+", _text)
        _buf, _count = [], 0
        for _part in _parts:
            _buf.append(_part)
            if _part.strip():
                _count += 1
            if _count >= _words:
                yield "".join(_buf)
                _buf, _count = [], 0
                if _delay:
                    _time.sleep(_delay)
        if _buf:
            yield "".join(_buf)


'''
    if "def render_ai_assistant(pid: int):\n" not in source:
        raise RuntimeError("V6.22 AI typewriter anchor missing")
    source = source.replace(
        "def render_ai_assistant(pid: int):\n",
        typewriter_helper + "def render_ai_assistant(pid: int):\n",
        1,
    )

    ai_import = "from ai_service import (AIServiceError, AISettings, OpenAIProjectAssistant, GeminiSettings, GeminiProjectAssistant, ProjectContextBuilder)\n"
    if ai_import not in source:
        raise RuntimeError("V6.22 AI import anchor missing")
    source = source.replace(
        ai_import,
        ai_import + "from ai_streaming_patch import install_ai_streaming\ninstall_ai_streaming()\n",
        1,
    )

    old_chat = '''            try:\n                with st.chat_message("assistant"):\n                    with st.spinner("AI đang phân tích dữ liệu dự án..."):\n                        answer = ai.ask_project(pid, q, previous, date.today(), use_web=False)\n                    st.markdown(answer)\n                st.session_state[hkey].append({"role": "assistant", "content": answer})\n            except Exception as exc:\n                st.error(str(exc))\n'''
    new_chat = '''            try:\n                with st.chat_message("assistant"):\n                    _ai_status = st.empty()\n                    _ai_status.caption("AI đang phân tích dữ liệu dự án...")\n                    answer = st.write_stream(\n                        _ai_typewriter_stream(ai.ask_project_stream(pid, q, previous, date.today(), use_web=False))\n                    )\n                    _ai_status.empty()\n                answer = str(answer or "").strip()\n                if answer:\n                    st.session_state[hkey].append({"role": "assistant", "content": answer})\n            except Exception as exc:\n                st.error(str(exc))\n'''
    if old_chat not in source:
        raise RuntimeError("V6.22 AI streaming chat anchor missing")
    source = source.replace(old_chat, new_chat, 1)

    source = "\n".join(
        line for line in source.splitlines()
        if not any(marker in line for marker in HIDDEN_UI_NOTE_MARKERS)
    ) + "\n"
    source = source.replace(
        "        if not attach_ready:\n        is_revision_return =",
        "        is_revision_return =",
    )

    forbidden = (
        "from v621_runtime_patch import",
        "compiled_streamlit_app(",
        "bundle_01.b64",
        "v612_source/streamlit_app_bundle",
    )
    leaked = [item for item in forbidden if item in source]
    if leaked:
        raise RuntimeError(f"Historical runtime loader leaked into final V6.22 app: {leaked}")
    leaked_notes = [item for item in FORBIDDEN_UI_NOTE_MARKERS if item in source]
    if leaked_notes:
        raise RuntimeError(f"UI note cleanup incomplete: {leaked_notes}")
    _validate_bootstrap_order(source)
    return source


def build() -> Path:
    root = Path(__file__).resolve().parent
    parts = sorted((root / "v621_webopt_source").glob("part_*.b64"))
    if len(parts) != 9:
        raise RuntimeError(f"Incomplete V6.22 source: expected 9 parts, found {len(parts)}")
    encoded = "".join(part.read_text(encoding="ascii").strip() for part in parts)
    source = gzip.decompress(base64.b64decode(encoded)).decode("utf-8")
    source = _finalize_source(source)
    missing = [marker for marker in REQUIRED_MARKERS if marker not in source]
    if missing:
        raise RuntimeError(f"V6.22 markers missing: {missing}")
    out = root / "dist" / "streamlit_app.py"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(source, encoding="utf-8")
    compile(source, str(out), "exec")
    print(f"V6.22 PostgreSQL Cloud build OK: {len(source)} chars -> {out}")
    return out


if __name__ == "__main__":
    build()
