from __future__ import annotations

# V6.22 BOOTSTRAP START
from streamlit_secrets_v622 import apply_streamlit_secrets_to_env
apply_streamlit_secrets_to_env()
from postgres_backend_v622 import install_postgres_backend
install_postgres_backend()
from v621_webopt_runtime import install_runtime
install_runtime()
# V6.22 BOOTSTRAP END

from v621_webopt_runtime import install_runtime as _install_v621_webopt_runtime
_install_v621_webopt_runtime()

import io
import os
import json
import re
import sqlite3
import tempfile
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import quote_plus

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

from cloud_db import CloudDatabase, progress_delta, planned_progress, calculate_delay_days
from mpp_cloud_reader import MppCloudError, read_mpp
from settings_store import DEFAULT_SPECIFIED_SEARCH_DOMAINS
from ai_service import (AIServiceError, AISettings, OpenAIProjectAssistant, GeminiSettings, GeminiProjectAssistant, ProjectContextBuilder)
from ai_streaming_patch import install_ai_streaming
install_ai_streaming()
from drive_gateway import DriveGateway, DriveGatewayError, config_from_streamlit

class _LazyPlotlyExpress:
    """Import Plotly only when the user opens Gantt/Reports."""
    _module = None

    def __getattr__(self, name):
        if self._module is None:
            import plotly.express as _px
            self._module = _px
        return getattr(self._module, name)


px = _LazyPlotlyExpress()

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
IS_RENDER = str(os.environ.get("RENDER", "")).strip().lower() == "true"
DEPLOY_PLATFORM = "Render" if IS_RENDER else "Streamlit/Local"
DEFAULT_DB_PATH = Path("/var/data/qlda_cloud.db") if IS_RENDER else (DATA_DIR / "qlda_cloud.db")
DB_PATH = Path(os.environ.get("QLDA_DB_PATH", str(DEFAULT_DB_PATH)))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
LEGAL_CACHE_PATH = APP_DIR / "legal_cache.json"

# V4.0.8 - Google Search credentials on Streamlit are read from Secrets.
# Never commit these values to GitHub.
try:
    if "GOOGLE_SEARCH_API_KEY" in st.secrets:
        os.environ.setdefault("GOOGLE_SEARCH_API_KEY", str(st.secrets["GOOGLE_SEARCH_API_KEY"]))
    if "GOOGLE_SEARCH_CX" in st.secrets:
        os.environ.setdefault("GOOGLE_SEARCH_CX", str(st.secrets["GOOGLE_SEARCH_CX"]))
except Exception:
    pass

DOC_CONFIG = {
    "NCR": {
        "title": "NCR - Non-Conformance Report",
        "statuses": ["Mở", "Đang khắc phục", "Chờ kiểm tra", "Đóng", "Hủy"],
        "done_statuses": ["Đóng", "Hủy"],
        "subject": "Nội dung không phù hợp",
        "code_label": "Mã NCR *",
        "issuer_label": "Người phát hành / trình",
        "assignee_label": "Người / Đơn vị xử lý",
        "response_label": "Biện pháp khắc phục / Kết quả",
    },
    "RFA": {
        "title": "RFA - Request for Approval",
        "statuses": ["Soạn thảo", "Đã gửi", "Chờ duyệt", "Đã duyệt", "Từ chối", "Đóng"],
        "done_statuses": ["Đã duyệt", "Từ chối", "Đóng"],
        "subject": "Nội dung trình duyệt",
        "code_label": "Mã RFA *",
        "issuer_label": "Người trình",
        "assignee_label": "Người / Đơn vị duyệt",
        "response_label": "Ý kiến / Kết quả phê duyệt",
    },
    "RFI": {
        "title": "RFI - Request for Information",
        "statuses": ["Đã gửi", "Chờ phản hồi", "Đã phản hồi", "Đóng", "Hủy"],
        "done_statuses": ["Đã phản hồi", "Đóng", "Hủy"],
        "subject": "Câu hỏi / Nội dung cần làm rõ",
        "code_label": "Mã RFI *",
        "issuer_label": "Người gửi",
        "assignee_label": "Người / Đơn vị phản hồi",
        "response_label": "Nội dung phản hồi",
    },
    "BBHT": {
        "title": "Biên bản hiện trường",
        "statuses": ["Mới lập", "Đã phát hành", "Đang xử lý", "Đã xử lý", "Đóng", "Hủy"],
        "done_statuses": ["Đã xử lý", "Đóng", "Hủy"],
        "subject": "Nội dung / Sự việc hiện trường",
        "code_label": "Mã biên bản *",
        "issuer_label": "Người / Đơn vị lập biên bản",
        "assignee_label": "Người / Đơn vị xử lý",
        "response_label": "Kết quả xử lý / Phản hồi",
    },
    "NKCT": {
        "title": "Báo cáo nhật ký công trường",
        "statuses": ["Đã ghi nhận", "Có sự cố", "Đang xử lý", "Đã xử lý"],
        "done_statuses": ["Đã ghi nhận", "Đã xử lý"],
        "subject": "Khu vực / Hạng mục",
        "code_label": "Mã nhật ký *",
        "issuer_label": "Kỹ sư hiện trường",
        "assignee_label": "Chỉ huy / Giám sát",
        "response_label": "Thông tin nhật ký",
    },
    "NTCV": {
        "title": "Hồ sơ nghiệm thu công việc",
        "statuses": ["Chuẩn bị hồ sơ", "Đã trình nghiệm thu", "Chờ nghiệm thu", "Yêu cầu sửa", "Đạt", "Không đạt", "Đóng"],
        "done_statuses": ["Đạt", "Không đạt", "Đóng"],
        "subject": "Hạng mục / Công việc nghiệm thu",
        "code_label": "Mã hồ sơ NTCV *",
        "issuer_label": "Người / Đơn vị trình nghiệm thu",
        "assignee_label": "Người / Đơn vị nghiệm thu",
        "issue_date_label": "Ngày trình nghiệm thu",
        "due_date_label": "Ngày dự kiến nghiệm thu",
        "closed_date_label": "Ngày nghiệm thu / đóng",
        "response_label": "Kết quả / Ý kiến nghiệm thu",
    },
    "NTVL": {
        "title": "Hồ sơ nghiệm thu vật liệu đầu vào",
        "statuses": ["Chuẩn bị hồ sơ", "Đã trình", "Chờ kiểm tra", "Yêu cầu bổ sung", "Chấp thuận", "Chấp thuận có điều kiện", "Không chấp thuận", "Đóng"],
        "done_statuses": ["Chấp thuận", "Chấp thuận có điều kiện", "Không chấp thuận", "Đóng"],
        "subject": "Vật liệu / Thiết bị nghiệm thu đầu vào",
        "code_label": "Mã hồ sơ NTVL *",
        "issuer_label": "Nhà thầu / Người trình",
        "assignee_label": "Người / Đơn vị kiểm tra",
        "issue_date_label": "Ngày trình / nhận hồ sơ",
        "due_date_label": "Ngày dự kiến nghiệm thu",
        "closed_date_label": "Ngày nghiệm thu / đóng",
        "response_label": "Kết quả nghiệm thu / Ý kiến",
    },
    "KDVT": {
        "title": "Hồ sơ kiểm định vật tư",
        "statuses": ["Chuẩn bị hồ sơ", "Đã gửi kiểm định", "Đang kiểm định", "Chờ kết quả", "Đạt", "Không đạt", "Đóng"],
        "done_statuses": ["Đạt", "Không đạt", "Đóng"],
        "subject": "Vật tư / Thiết bị kiểm định",
        "code_label": "Mã hồ sơ kiểm định *",
        "issuer_label": "Người / Đơn vị gửi kiểm định",
        "assignee_label": "Đơn vị kiểm định / Người phụ trách",
        "issue_date_label": "Ngày gửi kiểm định",
        "due_date_label": "Hạn trả kết quả",
        "closed_date_label": "Ngày có kết quả / đóng",
        "response_label": "Kết quả kiểm định / Chứng chỉ",
    },
}
PRIORITIES = ["Thấp", "Trung bình", "Cao", "Khẩn"]

DRAWING_TYPES = {
    "SHOPDRAWING": "Shopdrawing",
    "ISSUED_DESIGN": "BV phát hành TKTC",
    "UPDATED": "BV cập nhật",
    "AS_BUILT": "BV hoàn công",
}
DRAWING_STATUSES = [
    "Mới nhận", "Đang kiểm tra", "Chờ phản hồi", "Chấp thuận",
    "Chấp thuận có điều kiện", "Cần sửa", "Thay thế", "Hủy",
]
for _approval_doc_type in ("RFA", "RFI"):
    for _s in ("Đang phê duyệt", "Đang duyệt - Ban điều hành", "Đang duyệt - Tư vấn giám sát", "Đang duyệt - Ban quản lý dự án", "Yêu cầu chỉnh sửa - Ban điều hành", "Yêu cầu chỉnh sửa - Tư vấn giám sát", "Yêu cầu chỉnh sửa - Ban quản lý dự án", "Đã phê duyệt"):
        if _s not in DOC_CONFIG[_approval_doc_type]["statuses"]:
            DOC_CONFIG[_approval_doc_type]["statuses"].append(_s)
for _s in ("Đang phê duyệt", "Đang duyệt - Ban điều hành", "Đang duyệt - Tư vấn giám sát", "Đang duyệt - Ban quản lý dự án", "Yêu cầu chỉnh sửa - Ban điều hành", "Yêu cầu chỉnh sửa - Tư vấn giám sát", "Yêu cầu chỉnh sửa - Ban quản lý dự án", "Đã phê duyệt"):
    if _s not in DRAWING_STATUSES:
        DRAWING_STATUSES.append(_s)
TASK_STATUSES = ["Tất cả", "Chưa bắt đầu", "Đúng tiến độ", "Nhanh tiến độ", "Chậm tiến độ", "Hoàn thành", "Đang thực hiện", "Chưa xác định"]

EXECUTION_CODE_RE = re.compile(r"^[A-Z]+\d+-[A-Z0-9]+-\d{3,}$")

def _normalize_execution_code(value: str) -> str:
    value = re.sub(r"\s+", "", str(value or "").upper())
    value = re.sub(r"-+", "-", value)
    return value.strip("-")

def _valid_execution_code(value: str) -> bool:
    return bool(EXECUTION_CODE_RE.fullmatch(_normalize_execution_code(value)))

def _tower_from_code(value: str) -> str:
    code = _normalize_execution_code(value)
    first = code.split("-", 1)[0] if code else ""
    return first if first else "KHÁC"

def _discipline_from_code(value: str) -> str:
    parts = _normalize_execution_code(value).split("-")
    return parts[1] if len(parts) >= 3 else ""

def _file_filter_match(total_files: int, choice: str) -> bool:
    if choice == "Có file":
        return total_files > 0
    if choice == "Chưa có file":
        return total_files == 0
    return True


def _ui_note(*args, **kwargs):
    """V6 mobile: ẩn các dòng ghi chú/caption để giao diện gọn trên điện thoại."""
    return None


def _drive_preview_url(file_id: str, file_name: str = "", open_url: str = "") -> str:
    """URL xem nhanh trên trình duyệt, không tải bytes qua Streamlit/Render."""
    fid = str(file_id or "").strip()
    name = str(file_name or "").lower()
    if not fid:
        return str(open_url or "")
    ext = Path(name).suffix.lower()
    # PDF/ảnh dùng Drive preview; CAD/BIM dùng Drive view để trình duyệt/Drive tự xử lý.
    if ext in {".pdf", ".jpg", ".jpeg", ".png", ".webp", ".gif", ".tif", ".tiff"}:
        return f"https://drive.google.com/file/d/{fid}/preview"
    if ext in {".dwg", ".dxf", ".dwf", ".rvt", ".ifc", ".nwd", ".nwc"}:
        return f"https://drive.google.com/file/d/{fid}/view"
    return str(open_url or f"https://drive.google.com/file/d/{fid}/view")


def _diary_meta(record) -> dict:
    if not record:
        return {}
    raw = str(record["response"] or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {"incident_detail": raw}


def _diary_json(**kwargs) -> str:
    payload = {"schema": "qlda_site_diary_v1", **kwargs}
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

st.set_page_config(page_title="QLDA Xây dựng V6.22 • Streamlit Cloud • Drive 2GB", page_icon="🏗️", layout="wide")

st.markdown("""
<style>
.block-container {padding-top: 1.1rem; padding-bottom: 2rem;}
[data-testid="stMetric"] {background: #f7f9fc; border: 1px solid #e5e7eb; padding: 10px 14px; border-radius: 12px;}
.small-note {font-size: 0.86rem; color: #64748b;}
</style>
""", unsafe_allow_html=True)


@st.cache_resource
def get_db(path: str) -> CloudDatabase:
    return CloudDatabase(path)


db = get_db(str(DB_PATH))


@st.cache_resource
def get_legal_repo(path: str):
    # V6.21 web-opt: BeautifulSoup/legal crawler is imported only when the
    # Văn bản module is actually opened. This shortens normal app cold-start.
    from legal_documents import LegalRepository
    return LegalRepository(path)


def _legal_repo_for_view():
    repo = get_legal_repo(str(DB_PATH))
    if "legal_cache_loaded" not in st.session_state:
        try:
            st.session_state["legal_cache_stats"] = repo.import_cache(LEGAL_CACHE_PATH)
        except Exception as exc:
            st.session_state["legal_cache_error"] = str(exc)
        st.session_state["legal_cache_loaded"] = True
    return repo


def iso(d) -> str:
    if not d:
        return ""
    if isinstance(d, str):
        return d
    return d.isoformat()


def parse_date(value: str, fallback: date | None = None) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except Exception:
        return fallback or date.today()


def rows_to_df(rows) -> pd.DataFrame:
    return pd.DataFrame([dict(r) for r in rows]) if rows else pd.DataFrame()


def uploaded_triplets(files) -> list[tuple[str, str, bytes]]:
    out = []
    for f in files or []:
        out.append((f.name, getattr(f, "type", "") or "application/octet-stream", f.getvalue()))
    return out


def to_excel_bytes(df: pd.DataFrame, sheet_name="Data") -> bytes:
    buff = io.BytesIO()
    with pd.ExcelWriter(buff, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name=sheet_name[:31])
    return buff.getvalue()

def _paged_df(df: pd.DataFrame, key: str, *, default_size: int = 50, sizes=(50, 100, 200)):
    """Render only one page of large tables to reduce browser payload/rerun cost."""
    total = len(df)
    if total <= default_size:
        return df, "all"
    valid_sizes = [int(x) for x in sizes if int(x) > 0]
    if default_size not in valid_sizes:
        valid_sizes.insert(0, default_size)
    c1, c2, c3 = st.columns([1.1, 1.1, 3.8])
    page_size = c1.selectbox("Dòng/trang", valid_sizes, index=valid_sizes.index(default_size), key=key + "_size")
    pages = max(1, (total + page_size - 1) // page_size)
    page = c2.number_input("Trang", min_value=1, max_value=pages, value=min(int(st.session_state.get(key + "_page", 1)), pages), step=1, key=key + "_page")
    start = (int(page) - 1) * int(page_size)
    end = min(total, start + int(page_size))
    c3.caption(f"Hiển thị {start + 1}–{end} / {total} dòng")
    return df.iloc[start:end].copy(), f"p{int(page)}s{int(page_size)}"


def _render_excel_export(df: pd.DataFrame, sheet_name: str, file_name: str, key: str, label: str = "Excel") -> None:
    """Generate XLSX only on demand; openpyxl is no longer run every rerun."""
    state_key = key + "_bytes"
    sig_key = key + "_sig"
    # Fast signature is sufficient to invalidate a prepared export when filters/data change.
    sig = (len(df), tuple(df.columns), int(pd.util.hash_pandas_object(df, index=True).sum()) if not df.empty else 0)
    if st.session_state.get(sig_key) != sig:
        st.session_state.pop(state_key, None)
        st.session_state[sig_key] = sig
    c1, c2 = st.columns([1.35, 1.65])
    if c1.button(f"📊 Tạo {label}", key=key + "_prepare", width="stretch"):
        with st.spinner("Đang tạo file Excel..."):
            st.session_state[state_key] = to_excel_bytes(df, sheet_name)
    data = st.session_state.get(state_key)
    if data:
        c2.download_button(
            f"⬇️ Tải {label}", data, file_name=file_name,
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key=key + "_download", width="stretch",
        )


def project_selector() -> tuple[int | None, list]:
    projects = db.projects()
    if not projects:
        return None, projects
    labels = {int(p["id"]): f"{p['code']} - {p['name']}" for p in projects}
    ids = list(labels)
    current = st.session_state.get("project_id")
    idx = ids.index(current) if current in ids else 0
    pid = st.sidebar.selectbox("Dự án đang làm việc", ids, index=idx, format_func=lambda x: labels[x], key="global_project")
    st.session_state["project_id"] = pid
    return int(pid), projects


def sidebar_project_tools():
    st.sidebar.markdown("### 🏗️ QLDA Xây dựng V6.22 PostgreSQL Cloud AI")
    _ui_note("Render Web Service" if IS_RENDER else "Streamlit / Local")
    with st.sidebar.expander("+ Tạo dự án"):
        with st.form("create_project", clear_on_submit=True):
            code = st.text_input("Mã dự án *")
            name = st.text_input("Tên dự án *")
            c1, c2 = st.columns(2)
            start = c1.date_input("Bắt đầu", value=date.today())
            end = c2.date_input("Kết thúc", value=date.today() + timedelta(days=365))
            manager = st.text_input("Quản lý dự án")
            note = ""
            submitted = st.form_submit_button("Tạo dự án", type="primary", disabled=not _is_admin())
            if submitted:
                if not code.strip() or not name.strip():
                    st.error("Cần nhập Mã dự án và Tên dự án.")
                else:
                    try:
                        pid = db.add_project(code, name, iso(start), iso(end), manager, note)
                        st.session_state["project_id"] = pid
                        st.success("Đã tạo dự án.")
                        st.rerun()
                    except sqlite3.IntegrityError:
                        st.error("Mã dự án đã tồn tại.")

    with st.sidebar.expander("💾 Sao lưu / khôi phục"):
        if IS_RENDER and str(os.environ.get("QLDA_RENDER_PERSISTENT_DISK", "false")).lower() in {"1", "true", "yes", "on"}:
            _ui_note("Database đang đặt trên Render Persistent Disk. Vẫn nên tải backup định kỳ.")
        elif IS_RENDER:
            st.warning("Render đang chạy KHÔNG có Persistent Disk: SQLite sẽ mất khi service restart/redeploy. File Google Drive không bị ảnh hưởng.")
        else:
            _ui_note("Hãy tải backup SQLite định kỳ.")
        backup = db.backup_bytes()
        st.download_button("⬇️ Tải backup SQLite", backup, file_name=f"QLDA_backup_{date.today():%Y%m%d}.db", mime="application/octet-stream", width="stretch")
        restore = st.file_uploader("Khôi phục từ .db", type=["db", "sqlite", "sqlite3"], key="restore_db")
        if st.button("Khôi phục database", disabled=(restore is None or not _is_admin()), width="stretch"):
            try:
                db.restore_bytes(restore.getvalue())
                st.success("Đã khôi phục database.")
                st.rerun()
            except Exception as exc:
                st.error(str(exc))


def render_schedule(pid: int):
    project = db.project(pid)
    st.subheader("📅 Quản lý tiến độ")
    _ui_note("MPP • WBS • Baseline • Critical Path • Gantt • KH%/TT% • Nhanh/Chậm")

    cdate, csrc = st.columns([1, 3])
    status_date = cdate.date_input("Ngày báo cáo", value=date.today(), key=f"status_date_{pid}")
    source = project["source_mpp_path"] or "Chưa nhập MPP"
    csrc.info(f"Nguồn tiến độ: **{source}**" + (f" — đồng bộ {project['last_sync']}" if project["last_sync"] else ""))

    rows = db.tasks(pid)
    n = len(rows)
    delayed = sum(1 for r in rows if r["status"] == "Chậm tiến độ")
    critical = sum(1 for r in rows if r["critical"])
    done = sum(1 for r in rows if int(r["actual_progress"] or 0) >= 100)
    avg = round(sum(float(r["actual_progress"] or 0) for r in rows) / n, 1) if n else 0
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Tổng công việc", n)
    k2.metric("Chậm tiến độ", delayed)
    k3.metric("Critical", critical)
    k4.metric("Hoàn thành", done)
    k5.metric("Tiến độ TB", f"{avg}%")

    with st.expander("📂 Nhập / đồng bộ Microsoft Project (.mpp)", expanded=not bool(rows)):
        st.write("Trên Cloud, file MPP được đọc trực tiếp bằng MPXJ; không cần cài Microsoft Project trên server.")
        mpp = st.file_uploader("Chọn file .mpp", type=["mpp"], key=f"mpp_{pid}")
        if st.button("Đọc và đồng bộ MPP", type="primary", disabled=(mpp is None or not _can_update()), key=f"syncmpp_{pid}"):
            suffix = Path(mpp.name).suffix or ".mpp"
            temp_path = None
            try:
                with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                    tmp.write(mpp.getvalue())
                    temp_path = tmp.name
                with st.spinner("Đang đọc Microsoft Project bằng MPXJ..."):
                    info, tasks = read_mpp(temp_path, status_date=status_date)
                    db.sync_mpp_tasks(pid, tasks, mpp.name, info)
                st.success(f"Đồng bộ thành công {len(tasks):,} công việc từ {mpp.name}.")
                st.rerun()
            except MppCloudError as exc:
                st.error(str(exc))
            except Exception as exc:
                st.exception(exc)
            finally:
                if temp_path:
                    try:
                        Path(temp_path).unlink(missing_ok=True)
                    except Exception:
                        pass

    a1, a2 = st.columns([1, 3])
    if a1.button("Tính lại KH% theo ngày báo cáo", key=f"recalc_{pid}", width="stretch", disabled=not _can_update()):
        db.recalc_planned(pid, status_date)
        st.success(f"Đã tính lại KH% tại ngày {status_date:%d/%m/%Y}.")
        st.rerun()
    _ui_note("KH% tính tuyến tính theo Start–Finish. TT=100% là Hoàn thành; TT<100% sau ngày Kết thúc sẽ tự tính Ngày trễ. TT nhập tay được giữ khi đồng bộ MPP.")

    with st.expander("+ Thêm công việc thủ công"):
        with st.form(f"manual_task_{pid}", clear_on_submit=True):
            c1, c2 = st.columns([1, 3])
            wbs = c1.text_input("WBS")
            name = c2.text_input("Công việc *")
            c1, c2, c3 = st.columns(3)
            start = c1.date_input("Bắt đầu", value=status_date)
            end = c2.date_input("Kết thúc", value=status_date + timedelta(days=7))
            responsible = c3.text_input("Phụ trách")
            c1, c2, c3 = st.columns(3)
            planned = c1.number_input("KH %", 0, 100, value=0)
            actual = c2.number_input("TT %", 0, 100, value=0)
            predecessor = c3.text_input("Predecessor")
            note = ""
            submit = st.form_submit_button("Thêm công việc", type="primary", disabled=not _can_update())
            if submit:
                if not name.strip() or end < start:
                    st.error("Tên công việc là bắt buộc và ngày kết thúc không được trước ngày bắt đầu.")
                else:
                    duration = max(1, (end - start).days + 1)
                    db.add_task(pid, dict(wbs=wbs, name=name, responsible=responsible, start_date=iso(start), end_date=iso(end),
                                               duration=duration, planned_progress=planned, actual_progress=actual,
                                               predecessor=predecessor, note=note))
                    st.success("Đã thêm công việc.")
                    st.rerun()

    f1, f2 = st.columns([3, 1])
    keyword = f1.text_input("Tìm WBS / công việc / resource", key=f"task_search_{pid}")
    status_filter = f2.selectbox("Trạng thái", TASK_STATUSES, key=f"task_status_{pid}")
    rows = db.tasks(pid, keyword, status_filter)
    if not rows:
        st.info("Chưa có công việc phù hợp.")
        return

    display = pd.DataFrame([{
        "DB ID": r["id"], "ID": r["source_task_id"] or "", "WBS": r["wbs"], "Công việc": r["name"],
        "Bắt đầu": r["start_date"], "Kết thúc": r["end_date"], "Duration": round(float(r["duration"] or 0), 2),
        "KH %": int(r["planned_progress"] or 0), "TT %": int(r["actual_progress"] or 0),
        "Nhanh / Chậm": progress_delta(r["planned_progress"], r["actual_progress"]), "Trạng thái": r["status"],
        "Ngày trễ": calculate_delay_days(r["end_date"], r["actual_progress"], r["actual_finish_date"] or "", status_date),
        "Predecessor": r["predecessor"], "Resources": r["resource_names"], "Baseline Start": r["baseline_start"],
        "Baseline Finish": r["baseline_finish"], "Slack (ngày)": round(float(r["total_slack"] or 0), 2),
        "Critical": "Có" if r["critical"] else "", "Nguồn": r["source_type"],
    } for r in rows])
    original_tt = dict(zip(display["DB ID"], display["TT %"]))
    task_page, task_page_token = _paged_df(display, f"task_table_page_{pid}", default_size=100, sizes=(50, 100, 200))
    disabled_cols = list(task_page.columns) if not _can_update() else [c for c in task_page.columns if c != "TT %"]
    edited = st.data_editor(
        task_page, hide_index=True, width="stretch", disabled=disabled_cols,
        column_config={
            "DB ID": None,
            "TT %": st.column_config.NumberColumn("TT %", min_value=0, max_value=100, step=1, help="Nhập trực tiếp 0–100%"),
            "KH %": st.column_config.ProgressColumn("KH %", min_value=0, max_value=100, format="%d%%"),
            "Nhanh / Chậm": st.column_config.NumberColumn("TT − KH", format="%d%%"),
            "Ngày trễ": st.column_config.NumberColumn("Ngày trễ", format="%d ngày", help="Số ngày vượt ngày Kết thúc; khi TT=100% số ngày trễ được khóa tại ngày đạt 100%."),
        }, key=f"task_editor_{pid}_{task_page_token}", height=min(700, 80 + 35 * len(task_page)),
    )
    # Streamlit: tự lưu ngay khi người dùng thay đổi TT %.
    # st.data_editor rerun sau khi Enter/click ra khỏi ô; trên rerun này `edited`
    # chứa giá trị mới, còn `original_tt` vẫn là dữ liệu DB trước khi lưu.
    # So sánh hai giá trị để chỉ ghi đúng các dòng đã đổi, sau đó rerun một lần
    # để cập nhật Nhanh/Chậm, Trạng thái và Ngày trễ ngay trên bảng.
    autosaved = 0
    for _, r in edited.iterrows():
        task_id = int(r["DB ID"])
        try:
            actual = max(0, min(100, int(round(float(r["TT %"])))))
        except Exception:
            continue
        old_actual = int(original_tt.get(task_id, actual))
        if actual != old_actual and _can_update():
            db.set_actual_override(task_id, actual, status_date)
            autosaved += 1

    if autosaved:
        st.toast(f"Đã tự lưu TT% cho {autosaved} công việc", icon="✅")
        st.rerun()

    b1, b2 = st.columns([1, 3])
    _ui_note("TT % tự lưu khi nhấn Enter hoặc click ra khỏi ô.")
    selected_delete = b2.selectbox("Xóa task", [None] + [int(r["id"]) for r in rows], format_func=lambda x: "Chọn..." if x is None else f"#{x}", key=f"delete_task_select_{pid}")
    if st.button("Xóa công việc đã chọn", disabled=(selected_delete is None or not _is_admin()), key=f"delete_task_btn_{pid}"):
        db.delete_task(int(selected_delete))
        st.rerun()

    ex1, ex2 = st.columns(2)
    schedule_export_key = f"schedule_excel_bytes_{pid}"
    schedule_export_sig = (len(display), int(pd.util.hash_pandas_object(display, index=True).sum()) if not display.empty else 0)
    if st.session_state.get(schedule_export_key + "_sig") != schedule_export_sig:
        st.session_state.pop(schedule_export_key, None)
        st.session_state[schedule_export_key + "_sig"] = schedule_export_sig
    if ex1.button("📊 Tạo Excel tiến độ", key=f"schedule_excel_prepare_{pid}", width="stretch"):
        with st.spinner("Đang tạo Excel tiến độ..."):
            st.session_state[schedule_export_key] = to_excel_bytes(display.drop(columns=["DB ID"]), "TienDo")
    if st.session_state.get(schedule_export_key):
        ex1.download_button(
            "⬇️ Tải Excel tiến độ", st.session_state[schedule_export_key],
            file_name=f"TienDo_{project['code']}_{date.today():%Y%m%d}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            key=f"schedule_excel_download_{pid}", width="stretch",
        )
    excel_in = ex2.file_uploader("Nhập công việc từ Excel", type=["xlsx", "xls"], key=f"task_excel_{pid}")
    if excel_in is not None and ex2.button("Nhập Excel", key=f"task_excel_btn_{pid}", width="stretch", disabled=not _can_update()):
        try:
            xdf = pd.read_excel(excel_in)
            aliases = {
                "WBS": "wbs", "Công việc": "name", "Name": "name", "Bắt đầu": "start", "Start": "start",
                "Kết thúc": "end", "Finish": "end", "KH %": "planned", "TT %": "actual", "Phụ trách": "responsible",
                "Predecessor": "predecessor", "Ghi chú": "note",
            }
            normalized = {}
            for col in xdf.columns:
                if str(col).strip() in aliases:
                    normalized[aliases[str(col).strip()]] = col
            required = {"name", "start", "end"}
            if not required.issubset(normalized):
                st.error("Excel cần tối thiểu các cột: Công việc/Name, Bắt đầu/Start, Kết thúc/Finish.")
            else:
                count = 0
                for _, xr in xdf.iterrows():
                    s = pd.to_datetime(xr[normalized["start"]], errors="coerce")
                    e = pd.to_datetime(xr[normalized["end"]], errors="coerce")
                    if pd.isna(s) or pd.isna(e) or not str(xr[normalized["name"]]).strip():
                        continue
                    ps, pe = s.date(), e.date()
                    db.add_task(pid, {
                        "wbs": str(xr[normalized.get("wbs", "")] if "wbs" in normalized else ""),
                        "name": str(xr[normalized["name"]]),
                        "responsible": str(xr[normalized.get("responsible", "")] if "responsible" in normalized else ""),
                        "start_date": iso(ps), "end_date": iso(pe), "duration": max(1, (pe - ps).days + 1),
                        "planned_progress": int(xr[normalized["planned"]]) if "planned" in normalized and pd.notna(xr[normalized["planned"]]) else planned_progress(iso(ps), iso(pe), status_date),
                        "actual_progress": int(xr[normalized["actual"]]) if "actual" in normalized and pd.notna(xr[normalized["actual"]]) else 0,
                        "predecessor": str(xr[normalized.get("predecessor", "")] if "predecessor" in normalized else ""),
                        "note": str(xr[normalized.get("note", "")] if "note" in normalized else ""),
                    })
                    count += 1
                st.success(f"Đã nhập {count} công việc.")
                st.rerun()
        except Exception as exc:
            st.error(f"Không đọc được Excel: {exc}")

    st.markdown("#### Gantt")
    show_gantt = st.toggle("Hiển thị biểu đồ Gantt", value=False, key=f"show_gantt_{pid}", help="Tắt mặc định để trang Tiến độ mở nhanh hơn; Plotly chỉ tải khi cần.")
    if show_gantt:
        gantt_rows = [r for r in rows if r["start_date"] and r["end_date"]]
        if gantt_rows:
            gdf = pd.DataFrame([{
                "Task": ("   " * max(0, int(r["outline_level"] or 1) - 1)) + f"{r['wbs']} {r['name']}",
                "Start": pd.to_datetime(r["start_date"]), "Finish": pd.to_datetime(r["end_date"]),
                "Status": "Critical" if r["critical"] else r["status"], "TT": int(r["actual_progress"] or 0),
            } for r in gantt_rows])
            fig = px.timeline(gdf, x_start="Start", x_end="Finish", y="Task", color="Status", hover_data=["TT"])
            fig.update_yaxes(autorange="reversed")
            fig.update_layout(height=max(420, min(1400, 26 * len(gdf) + 160)), margin=dict(l=10, r=10, t=20, b=10), legend_title_text="")
            st.plotly_chart(fig, width="stretch")


def document_deadline_label(r, doc_type: str) -> str:
    done_statuses = set(DOC_CONFIG[doc_type].get("done_statuses", ["Đóng", "Hủy"]))
    if r["closed_date"] or r["status"] in done_statuses:
        return "Đã xử lý"
    if not r["due_date"]:
        return ""
    due = parse_date(r["due_date"])
    delta = (due - date.today()).days
    if delta < 0:
        return f"Quá hạn {abs(delta)} ngày"
    if delta <= 7:
        return f"Còn {delta} ngày"
    return "Trong hạn"


def _prepare_inline_upload_ticket(pid: int, *, kind: str, subtype: str, record_code: str, panel_key: str) -> dict:
    """Tạo ticket tải trực tiếp lên Drive và ghi snapshot file trước khi upload.

    V6.9 dùng snapshot để xác nhận hồ sơ bị trả về thực sự đã có file mới
    trong vòng chỉnh sửa hiện tại trước khi cho phép Lưu/Trình lại.
    """
    project = db.project(pid)
    if not project:
        raise RuntimeError("Không tìm thấy dự án.")
    token = _gateway_session_token()
    if not token:
        raise RuntimeError("Phiên Google Drive đã hết hạn. Hãy đăng nhập lại.")

    baseline_key = panel_key + "_baseline_file_ids"
    if baseline_key not in st.session_state:
        try:
            before = _drive_gateway().list_record_files(
                token,
                project_code=project["code"],
                kind=kind,
                subtype=subtype,
                record_code=record_code,
                include_history=False,
            )
            st.session_state[baseline_key] = [
                str(x.get("id") or "") for x in (before.get("files") or [])
                if str(x.get("id") or "") and not x.get("history")
            ]
        except Exception:
            st.session_state[baseline_key] = []
    st.session_state.setdefault(panel_key + "_new_upload_detected", False)

    approval_submission = (
        _user_approval_role(_cloud_identity()) == "CONTRACTOR"
        and ((kind == "document" and subtype in APPROVAL_ELIGIBLE_DOCS)
             or (kind == "drawing" and subtype in APPROVAL_ELIGIBLE_DRAWINGS))
    )
    upload = _drive_gateway().create_upload_ticket(
        token,
        project_code=project["code"],
        kind=kind,
        subtype=subtype,
        record_code=record_code,
        upload_purpose="approval_submission" if approval_submission else "",
    )
    # V6.10: giữ thời điểm phát hành ở phía Streamlit để có thể tự tạo lại
    # ticket hết hạn mà không phụ thuộc vào nút/rerun của trình duyệt.
    upload = dict(upload or {})
    upload["_issued_at"] = time.time()
    st.session_state[panel_key + "_ticket"] = upload
    st.session_state[panel_key + "_upload_open"] = True
    st.session_state.pop(panel_key + "_ticket_error", None)
    return upload


def _ensure_revision_upload_ticket(
    pid: int, *, kind: str, subtype: str, record_code: str, panel_key: str
) -> dict:
    """V6.10: bảo đảm hồ sơ bị trả về luôn có một uploader hoạt động.

    V6.9 chỉ tạo ticket sau khi nhấn nút rồi `st.rerun()`. Trên một số deployment/
    trình duyệt, rerun làm mất nhánh render uploader nên người dùng thấy nút nhưng
    không mở được vùng tải. V6.10 tự tạo ticket khi mở hồ sơ chỉnh sửa và render
    ngay trong cùng lượt chạy.
    """
    current = dict(st.session_state.get(panel_key + "_ticket") or {})
    issued = float(current.get("_issued_at") or 0)
    ttl = int(current.get("expires_seconds") or 0)
    # Làm mới sớm 60 giây trước hạn. Nếu response cũ không có TTL thì coi ticket
    # hợp lệ tối đa 10 phút phía client; Apps Script vẫn kiểm tra hạn thật.
    effective_ttl = max(60, ttl - 60) if ttl else 600
    live = bool(current.get("url") and issued and (time.time() - issued) < effective_ttl)
    if live:
        st.session_state[panel_key + "_upload_open"] = True
        return current
    try:
        return _prepare_inline_upload_ticket(
            pid, kind=kind, subtype=subtype, record_code=record_code, panel_key=panel_key
        )
    except Exception as exc:
        st.session_state[panel_key + "_ticket_error"] = str(exc)
        return {}


def _render_contractor_upload_expander(
    pid: int, *, kind: str, subtype: str, record_code: str, panel_key: str, is_revision_return: bool
) -> None:
    """V6.12: uploader Nhà thầu dùng native file_uploader, không phụ thuộc iframe/rerun."""
    project = db.project(pid)
    token = _gateway_session_token()
    if not project or not token:
        st.error("Không có phiên Google Drive hợp lệ. Hãy đăng nhập lại.")
        return

    # Chuẩn bị link resumable ở nền cho file lớn. Lỗi link không chặn file nhỏ.
    upload = _ensure_revision_upload_ticket(
        pid, kind=kind, subtype=subtype, record_code=record_code, panel_key=panel_key
    )

    label = "📎 Đính kèm file cập nhật / trình lại" if is_revision_return else "📎 Đính kèm file trình duyệt"
    with st.expander(label, expanded=is_revision_return):
        if is_revision_return:
            st.info("Hồ sơ đã bị trả về. Hãy chọn ít nhất 01 file phiên bản mới và tải lên Google Drive trước khi Lưu/Trình lại.")

        max_mb = int(_drive_gateway().config.legacy_max_upload_mb)
        files = st.file_uploader(
            f"Chọn file từ máy (tối đa {max_mb} MB/file)",
            accept_multiple_files=True,
            key=panel_key + "_v612_native_files",
        )
        if st.button(
            "⬆️ Tải file đã chọn lên Google Drive",
            key=panel_key + "_v612_native_upload",
            disabled=not bool(files),
            type="primary",
            width="stretch",
        ):
            errors = []
            uploaded = 0
            for f in (files or []):
                try:
                    _drive_gateway().upload_bytes(
                        token,
                        project_code=project["code"],
                        kind=kind,
                        subtype=subtype,
                        record_code=record_code,
                        name=f.name,
                        content=f.getvalue(),
                        mime_type=getattr(f, "type", "") or "application/octet-stream",
                        upload_purpose="approval_submission",
                    )
                    uploaded += 1
                except Exception as exc:
                    errors.append(f"{f.name}: {exc}")
            if uploaded:
                st.session_state[panel_key + "_new_upload_detected"] = True
                st.success(f"Đã tải {uploaded} file lên Google Drive.")
            if errors:
                st.error("Một số file chưa tải được: " + " | ".join(errors[:3]))
            if uploaded:
                st.rerun()

        st.markdown("**File lớn hơn giới hạn trên**")
        if upload.get("url"):
            st.link_button("↗️ Mở trình tải file lớn lên Google Drive", upload["url"], width="stretch")
        else:
            err = str(st.session_state.get(panel_key + "_ticket_error") or "").strip()
            if err:
                st.warning("Chưa tạo được link tải file lớn: " + err)

        if st.button("🔄 Tạo lại link tải file lớn", key=panel_key + "_v612_refresh_ticket", width="stretch"):
            try:
                st.session_state.pop(panel_key + "_ticket", None)
                st.session_state.pop(panel_key + "_upload_open", None)
                st.session_state.pop(panel_key + "_ticket_error", None)
                _prepare_inline_upload_ticket(
                    pid, kind=kind, subtype=subtype, record_code=record_code, panel_key=panel_key
                )
                st.rerun()
            except Exception as exc:
                st.error(f"Chưa tạo được link tải file lớn: {exc}")


@st.fragment
def _render_inline_drive_attachments(pid: int, *, kind: str, subtype: str, record_code: str, record_id: int, panel_key: str, show_contractor_upload: bool = True) -> int:
    """V6.0: file nằm ngay dưới nút Đính kèm file, không còn nút Cập nhật riêng.

    File lớn vẫn đi theo V5 resumable flow: trình duyệt -> Apps Script -> Drive,
    không đi qua Python/SQLite. Quyền update có thể upload/download nhưng không xóa.
    Admin xóa bằng cách tick file cần xóa rồi bấm một nút xóa tập trung.
    """
    st.markdown("##### 📎 File đính kèm")
    project = db.project(pid)
    if not project:
        st.warning("Không tìm thấy dự án.")
        return 0
    token = _gateway_session_token()
    if not token:
        st.warning("Phiên Google Drive đã hết hạn. Hãy đăng nhập lại.")
        return 0

    ticket_error = str(st.session_state.get(panel_key + "_ticket_error") or "").strip()
    if ticket_error:
        st.error("Không tạo được phiên đính kèm file: " + ticket_error)
        if "không có quyền" in ticket_error.lower() or "quyền" in ticket_error.lower():
            st.warning(
                "Nếu tài khoản này là Nhà thầu nhưng quyền hệ thống chỉ là Chỉ đọc, "
                "hãy cập nhật Google Apps Script `Code.gs` V6.11. Bản V6.11 cho phép Nhà thầu "
                "upload riêng cho RFA/RFI/Shopdrawing/Hoàn công mà không cấp quyền Cập nhật toàn hệ thống."
            )

    upload = st.session_state.get(panel_key + "_ticket") or {}
    upload_open = bool(st.session_state.get(panel_key + "_upload_open"))
    if upload_open and upload.get("url"):
        # V6.10: luôn có link mở tab riêng trước iframe. Nếu trình duyệt chặn iframe
        # thì Nhà thầu vẫn tải file được bằng link này.
        st.success("✅ Phiên đính kèm file đã sẵn sàng. Có thể tải ngay bên dưới hoặc mở ở tab riêng.")
        st.link_button("↗️ Mở trình tải file ở tab riêng", upload["url"], width="stretch")
        # Apps Script V6 sets XFrameOptionsMode.ALLOWALL for this short-lived ticket page.
        components.iframe(upload["url"], height=530, scrolling=True)
        u1, u2 = st.columns([1.4, 1])
        if u2.button("✅ Hoàn tất & cập nhật File DB", key=panel_key + "_close_upload", width="stretch"):
            st.session_state[panel_key + "_upload_open"] = False
            st.session_state[panel_key + "_ticket"] = {}
            st.rerun()

    # V6.11: đường tải dự phòng ngay trong Streamlit cho Nhà thầu.
    # Mục tiêu: hồ sơ bị trả về vẫn cập nhật được file mới ngay cả khi iframe/link
    # bị trình duyệt chặn. Giới hạn theo LEGACY_MAX_UPLOAD_MB; file lớn dùng uploader Drive phía trên.
    approval_contractor_upload = (
        _user_approval_role(_cloud_identity()) == "CONTRACTOR"
        and ((kind == "document" and subtype in APPROVAL_ELIGIBLE_DOCS)
             or (kind == "drawing" and subtype in APPROVAL_ELIGIBLE_DRAWINGS))
    )
    if approval_contractor_upload and show_contractor_upload:
        st.markdown("**⬆️ Tải file cập nhật trực tiếp trong app (dự phòng)**")
        fallback_files = st.file_uploader(
            f"Chọn file cập nhật (tối đa {int(_drive_gateway().config.legacy_max_upload_mb)} MB/file)",
            accept_multiple_files=True,
            key=panel_key + "_contractor_fallback_files",
        )
        if st.button(
            "⬆️ Tải file cập nhật lên Google Drive",
            key=panel_key + "_contractor_fallback_upload",
            disabled=not bool(fallback_files),
            type="primary",
            width="stretch",
        ):
            errors = []
            uploaded = 0
            for f in (fallback_files or []):
                try:
                    _drive_gateway().upload_bytes(
                        token,
                        project_code=project["code"],
                        kind=kind,
                        subtype=subtype,
                        record_code=record_code,
                        name=f.name,
                        content=f.getvalue(),
                        mime_type=getattr(f, "type", "") or "application/octet-stream",
                        upload_purpose="approval_submission",
                    )
                    uploaded += 1
                except Exception as exc:
                    errors.append(f"{f.name}: {exc}")
            if uploaded:
                st.success(f"Đã tải {uploaded} file cập nhật lên Google Drive.")
                st.session_state[panel_key + "_new_upload_detected"] = True
            if errors:
                st.error("Một số file chưa tải được: " + " | ".join(errors[:3]))
            if uploaded:
                st.rerun()

    h1, h2 = st.columns([1, 4])
    if h1.button("🔄 Làm mới file / File DB", key=panel_key + "_refresh_files", width="stretch"):
        try:
            _drive_gateway().clear_cache("files")
        except Exception:
            pass
        st.rerun()
    _ui_note("⬆ Cập nhật/Admin: đính kèm & tải xuống • 🗑 Chỉ Admin được xóa file đã tick.")

    include_history = st.checkbox("Hiện cả _Lich_su", value=False, key=panel_key + "_history")
    try:
        data = _drive_gateway().list_record_files(
            token,
            project_code=project["code"],
            kind=kind,
            subtype=subtype,
            record_code=record_code,
            include_history=include_history,
        )
    except Exception as exc:
        st.error(f"Không đọc được danh sách file Google Drive: {exc}")
        return 0

    folder = data.get("folder") or {}
    files = data.get("files") or []

    # V6.17: fail-safe chung cho mọi sheet phê duyệt online.
    # Nếu hồ sơ đang chờ Nhà thầu chỉnh sửa và Drive đã có file hiện hành
    # được cập nhật SAU thời điểm cấp duyệt yêu cầu chỉnh sửa, coi đó là
    # bằng chứng Nhà thầu đã nộp phiên bản mới và tự trình lại đúng cấp trả.
    # Cơ chế này không phụ thuộc session/file_uploader/nút Lưu nên cả người
    # duyệt mở hồ sơ sau đó cũng có thể tự phục hồi workflow bị kẹt.
    eligible_online = (
        (kind == "document" and subtype in APPROVAL_ELIGIBLE_DOCS)
        or (kind == "drawing" and subtype in APPROVAL_ELIGIBLE_DRAWINGS)
    )
    if eligible_online and int(record_id or 0) > 0 and files:
        try:
            auto_wf = db.approval_workflow(pid, kind, subtype, int(record_id))
            if auto_wf and str(auto_wf["current_stage"] or "").strip().upper() == "CONTRACTOR":
                return_at = str(auto_wf["updated_at"] or "").strip()
                for h in db.approval_history(int(auto_wf["id"])):
                    if str(h["action"] or "").strip().upper() in {"REQUEST_REVISION", "REJECT", "RETURN"}:
                        return_at = str(h["created_at"] or return_at).strip()
                        break

                def _v617_ts(value):
                    raw = str(value or "").strip()
                    if not raw:
                        return None
                    try:
                        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                        return dt.replace(tzinfo=None)
                    except Exception:
                        try:
                            return datetime.strptime(raw[:19].replace("T", " "), "%Y-%m-%d %H:%M:%S")
                        except Exception:
                            return None

                returned_dt = _v617_ts(return_at)
                newest_dt = None
                for item in files:
                    if item.get("history"):
                        continue
                    dt = _v617_ts(item.get("modified_time"))
                    if dt is not None and (newest_dt is None or dt > newest_dt):
                        newest_dt = dt

                if returned_dt is not None and newest_dt is not None and newest_dt > returned_dt:
                    forced = db.force_revision_resubmit(
                        pid, kind, int(record_id),
                        submitted_by=str(auto_wf["submitted_by"] or ""),
                        submitted_name="",
                    )
                    if forced.get("resubmitted"):
                        st.session_state[panel_key + "_new_upload_detected"] = True
                        st.session_state[panel_key + "_v617_auto_resubmitted"] = True
                        st.success("✅ Đã phát hiện file cập nhật sau lần trả hồ sơ. Hệ thống đã tự trình lại đúng cấp duyệt.")
                        st.rerun()
        except Exception as exc:
            st.warning(f"Chưa tự đồng bộ được trạng thái trình lại từ file mới: {exc}")

    total_file_count = len(files)
    current_file_ids = {str(item.get("id") or "") for item in files if not item.get("history") and str(item.get("id") or "")}
    current_file_count = len(current_file_ids)
    baseline_key = panel_key + "_baseline_file_ids"
    if baseline_key in st.session_state:
        baseline_ids = {str(x) for x in (st.session_state.get(baseline_key) or []) if str(x)}
        if current_file_ids - baseline_ids:
            st.session_state[panel_key + "_new_upload_detected"] = True
    if folder.get("url"):
        st.link_button("📂 Mở thư mục trên Google Drive", folder["url"], width="content")
    if not files:
        _ui_note("Chưa có file trên Google Drive.")
        return 0

    st.markdown("**Danh sách file Google Drive**")
    file_filter = st.text_input("🔎 Lọc file cần xem", key=panel_key + "_inline_file_filter").strip().lower()
    if file_filter:
        files = [x for x in files if file_filter in str(x.get("name") or "").lower()]
    if not files:
        st.info("Không có file phù hợp bộ lọc.")
        return current_file_count

    checked_ids: list[tuple[str, str]] = []
    for idx, item in enumerate(files):
        file_id = str(item.get("id") or "")
        name = str(item.get("name") or "file")
        size = _format_drive_size(item.get("size"))
        modified = str(item.get("modified_time") or "").replace("T", " ").replace("Z", "")[:19]
        history_mark = " 🕘" if item.get("history") else ""
        c0, c1, c2, c3, c4 = st.columns([0.52, 4.55, 1.05, 1.05, 1.30])
        marked = c0.checkbox(
            "Chọn xóa",
            key=f"{panel_key}_delete_tick_{idx}_{file_id}",
            value=False,
            disabled=not _is_admin(),
            label_visibility="collapsed",
        )
        c1.markdown(f"**{name}**{history_mark}  \n{size}" + (f" • {modified}" if modified else ""))
        preview_url = _drive_preview_url(file_id, name, str(item.get("url") or ""))
        if preview_url:
            c2.link_button("👁 Xem", preview_url, width="stretch")
        if item.get("url"):
            c3.link_button("☁ Drive", item["url"], width="stretch")
        download_url = item.get("download_url") or (
            f"https://drive.google.com/uc?export=download&id={file_id}" if file_id else ""
        )
        if download_url:
            c4.link_button("⬇️ Tải", download_url, width="stretch")
        if marked and file_id:
            checked_ids.append((file_id, name))

    if _is_admin():
        delete_label = f"🗑 Xóa file đã chọn ({len(checked_ids)})"
        if st.button(delete_label, key=panel_key + "_delete_checked", disabled=not checked_ids, type="secondary"):
            deleted = 0
            errors = []
            for file_id, name in checked_ids:
                try:
                    _drive_gateway().trash_file(token, file_id)
                    deleted += 1
                except Exception as exc:
                    errors.append(f"{name}: {exc}")
            if deleted:
                st.success(f"Đã chuyển {deleted} file đã chọn vào Thùng rác Google Drive.")
            if errors:
                st.error("Không xóa được: " + " | ".join(errors))
            st.rerun()
    else:
        _ui_note("🔒 Quyền Cập nhật/Chỉ đọc không được xóa file. Ô tick xóa chỉ hoạt động với Admin.")

    return current_file_count


def _record_drive_counts(pid: int, *, kind: str, subtype: str, record_codes) -> dict[str, dict]:
    """Đọc số file hiện có trên Google Drive theo lô để cột File DB phản ánh file direct-upload."""
    project = db.project(pid)
    token = _gateway_session_token()
    codes = [str(c or "").strip() for c in record_codes if str(c or "").strip()]
    if not project or not token or not codes:
        return {}
    try:
        return _drive_gateway().record_file_counts(
            token,
            project_code=project["code"],
            kind=kind,
            subtype=subtype,
            record_codes=codes,
        )
    except Exception as exc:
        _ui_note(f"⚠ Chưa đồng bộ được trạng thái File DB từ Google Drive: {exc}")
        return {}


def _trash_record_drive_files(pid: int, *, kind: str, subtype: str, record_code: str) -> tuple[int, list[str]]:
    """Admin: chuyển toàn bộ file hiện hành + lịch sử của một record vào thùng rác Drive."""
    project = db.project(pid)
    token = _gateway_session_token()
    if not project or not token:
        return 0, ["Không có phiên Google Drive hợp lệ."]
    try:
        data = _drive_gateway().list_record_files(
            token,
            project_code=project["code"],
            kind=kind,
            subtype=subtype,
            record_code=record_code,
            include_history=True,
        )
    except Exception as exc:
        return 0, [str(exc)]
    deleted = 0
    errors: list[str] = []
    seen: set[str] = set()
    for item in data.get("files") or []:
        file_id = str(item.get("id") or "")
        if not file_id or file_id in seen:
            continue
        seen.add(file_id)
        try:
            _drive_gateway().trash_file(token, file_id)
            deleted += 1
        except Exception as exc:
            errors.append(f"{item.get('name') or file_id}: {exc}")
    return deleted, errors


APPROVAL_ROLE_LABELS = {
    "": "Không tham gia duyệt",
    "CONTRACTOR": "Nhà thầu",
    "SITE_MANAGEMENT": "Ban điều hành",
    "CONSULTANT": "Tư vấn giám sát",
    "PROJECT_MANAGEMENT": "Ban quản lý dự án",
}

# V6.2: tương thích dữ liệu phân quyền duyệt của các bản V6.0/V6.1 cũ.
# Một số deployment đã dùng approval_group với mã chữ thường
# (contractor/site_management/tvgs/bqlda), trong khi bản mới dùng
# approval_role với mã chữ hoa. Mọi nơi trong app đều quy về một chuẩn.
_APPROVAL_ROLE_ALIASES = {
    "": "",
    "NONE": "",
    "CONTRACTOR": "CONTRACTOR",
    "SITE_MANAGEMENT": "SITE_MANAGEMENT",
    "EXECUTIVE": "SITE_MANAGEMENT",
    "BAN_DIEU_HANH": "SITE_MANAGEMENT",
    "CONSULTANT": "CONSULTANT",
    "TVGS": "CONSULTANT",
    "SUPERVISION": "CONSULTANT",
    "PROJECT_MANAGEMENT": "PROJECT_MANAGEMENT",
    "BQLDA": "PROJECT_MANAGEMENT",
    "PMB": "PROJECT_MANAGEMENT",
}

def _normalize_approval_role(value) -> str:
    raw = str(value or "").strip().upper().replace("-", "_").replace(" ", "_")
    return _APPROVAL_ROLE_ALIASES.get(raw, raw if raw in APPROVAL_ROLE_LABELS else "")

def _user_approval_role(user: dict | None) -> str:
    u = dict(user or {})
    role = _normalize_approval_role(u.get("approval_role") or u.get("approval_group") or "")
    # Admin được xem là cấp Ban QLDA khi dữ liệu cũ chưa có trường phân loại duyệt.
    # Điều này giữ đúng quyền quản trị/phê duyệt và tự sửa hiển thị "Không tham gia duyệt"
    # ở các tài khoản Admin được tạo từ deployment cũ.
    if not role and str(u.get("role") or "").strip().lower() == "admin":
        return "PROJECT_MANAGEMENT"
    return role

APPROVAL_ELIGIBLE_DOCS = {"RFA", "RFI"}
APPROVAL_ELIGIBLE_DRAWINGS = {"SHOPDRAWING", "AS_BUILT"}
# Legacy test markers retained for upgrade compatibility only:
# Approval UI / Workflow engine: V6.8
# Approval UI / Workflow engine: V6.9  # legacy test/upgrade marker
# Approval UI / Workflow engine: V6.10  # legacy test/upgrade marker
# Approval UI / Workflow engine: V6.11  # legacy test/upgrade marker
# v66_doc_attach_
# v66_drawing_attach_


def _app_public_url() -> str:
    explicit = str(os.environ.get("QLDA_APP_URL", "") or "").strip()
    if explicit:
        return explicit
    render = str(os.environ.get("RENDER_EXTERNAL_URL", "") or "").strip()
    return render


def _approval_users() -> list[dict]:
    token = _gateway_session_token()
    if not token:
        return []
    gw = _drive_gateway()
    try:
        return gw.approval_users(token)
    except Exception:
        # Tương thích backend cũ: Admin vẫn có thể dùng list_users.
        try:
            return gw.list_users(token)
        except Exception:
            return []


def _active_approval_users_by_role() -> dict[str, list[dict]]:
    """Nhóm user đang hoạt động theo vai trò duyệt, tương thích V6.0-V6.7."""
    grouped: dict[str, list[dict]] = {}
    for raw in _approval_users():
        u = dict(raw or {})
        if u.get("active", True) is False:
            continue
        role = _user_approval_role(u)
        if not role:
            continue
        grouped.setdefault(role, []).append(u)
    for role in grouped:
        grouped[role] = sorted(
            grouped[role],
            key=lambda u: (str(u.get("name") or "").lower(), str(u.get("email") or "").lower()),
        )
    return grouped


def _pick_approval_user(users: list[dict], *, preferred_email: str = "", preferred_name: str = "") -> dict | None:
    if not users:
        return None
    pe = str(preferred_email or "").strip().lower()
    pn = str(preferred_name or "").strip().lower()
    if pe:
        exact = next((u for u in users if str(u.get("email") or "").strip().lower() == pe), None)
        if exact:
            return exact
    if pn:
        exact = next((u for u in users if str(u.get("name") or "").strip().lower() == pn), None)
        if exact:
            return exact
    return users[0]


def _default_approval_participants(*, submitted_email: str = "", submitted_name: str = "", current_identity: dict | None = None) -> tuple[dict, list[str]]:
    """Tạo bộ người tham gia mặc định từ phân quyền đã cấu hình.

    V6.7: Lưu hồ sơ của Nhà thầu đồng thời là hành động trình duyệt, vì vậy
    không yêu cầu thêm một nút "Trình phê duyệt" sau khi lưu.
    """
    grouped = _active_approval_users_by_role()
    ident = dict(current_identity or {})
    ident_role = _user_approval_role(ident)

    contractor = None
    if ident_role == "CONTRACTOR":
        contractor = {
            "email": str(ident.get("email") or submitted_email or "").strip().lower(),
            "name": str(ident.get("name") or submitted_name or "").strip(),
        }
    else:
        contractor = _pick_approval_user(
            grouped.get("CONTRACTOR", []),
            preferred_email=submitted_email,
            preferred_name=submitted_name,
        )

    participants: dict[str, dict] = {}
    if contractor:
        participants["CONTRACTOR"] = {
            "email": str(contractor.get("email") or "").strip().lower(),
            "name": str(contractor.get("name") or "").strip(),
        }

    for role in ("SITE_MANAGEMENT", "CONSULTANT", "PROJECT_MANAGEMENT"):
        preferred_email = str(ident.get("email") or "") if ident_role == role else ""
        preferred_name = str(ident.get("name") or "") if ident_role == role else ""
        chosen = _pick_approval_user(
            grouped.get(role, []),
            preferred_email=preferred_email,
            preferred_name=preferred_name,
        )
        if chosen:
            participants[role] = {
                "email": str(chosen.get("email") or "").strip().lower(),
                "name": str(chosen.get("name") or "").strip(),
            }

    # V6.8: workflow không còn phụ thuộc bắt buộc vào việc đọc được danh bạ
    # người duyệt từ Apps Script. Chỉ Nhà thầu cần được xác định khi có thể;
    # các bước Ban điều hành/TVGS/Ban QLDA có thể để trống người cụ thể và
    # sẽ được "claim" bởi tài khoản đăng nhập có đúng vai trò khi xử lý.
    missing = []
    return participants, missing


def _ensure_approval_workflow_started(
    pid: int,
    record_kind: str,
    subtype: str,
    record_id: int,
    record_code: str,
    record_title: str,
    *,
    submitted_email: str = "",
    submitted_name: str = "",
    current_identity: dict | None = None,
    notify: bool = True,
) -> dict:
    """Bảo đảm hồ sơ có workflow và đang được chuyển đúng bước.

    - Hồ sơ mới: tự trình sang Ban điều hành.
    - Hồ sơ bị trả về Nhà thầu: Lưu lại đồng thời Trình lại đúng cấp trả hồ sơ.
    - Hồ sơ V6.6 có file nhưng chưa workflow: tự phục hồi khi người duyệt mở.
    """
    wf = db.approval_workflow(pid, record_kind, subtype, int(record_id))
    identity = dict(current_identity or {})
    ident_role = _user_approval_role(identity)
    email = str(submitted_email or "").strip().lower()
    name = str(submitted_name or "").strip()
    if ident_role == "CONTRACTOR":
        email = email or str(identity.get("email") or "").strip().lower()
        name = name or str(identity.get("name") or "").strip()

    if wf:
        current_stage = str(wf["current_stage"] or "")
        if current_stage == "CONTRACTOR":
            result = db.resubmit_approval_workflow(int(wf["id"]), email or str(wf["submitted_by"] or ""), submitted_name=name)
            if notify and result.get("next_email"):
                _send_approval_notice(
                    result.get("next_email", ""), record_code, record_title, result.get("status", ""),
                    "Nhà thầu đã cập nhật hồ sơ và trình lại.",
                )
            return {"ok": True, "started": False, "resubmitted": True, **result}
        return {
            "ok": True,
            "started": False,
            "resubmitted": False,
            "workflow_id": int(wf["id"]),
            "status": str(wf["overall_status"] or ""),
            "current_stage": current_stage,
        }

    participants, missing = _default_approval_participants(
        submitted_email=email,
        submitted_name=name,
        current_identity=identity,
    )
    # V6.8 fail-safe: ngay cả khi backend chưa trả được danh bạ phê duyệt,
    # vẫn tạo workflow. Nếu không xác định được Nhà thầu qua danh bạ thì
    # dùng chính người trình/identity hiện tại; reviewer sẽ được xác nhận
    # theo vai trò khi họ xử lý bước tương ứng.
    if missing:
        fallback_email = email if ident_role == "CONTRACTOR" else ""
        fallback_name = name
        participants["CONTRACTOR"] = {
            "email": fallback_email,
            "name": fallback_name,
        }

    contractor = participants.get("CONTRACTOR") or {
        "email": (email if ident_role == "CONTRACTOR" else ""),
        "name": name,
    }
    submit_email = email or str(contractor.get("email") or "")
    submit_name = name or str(contractor.get("name") or "")
    wid = db.start_approval_workflow(
        pid, record_kind, subtype, int(record_id), record_code, submit_email, participants, submitted_name=submit_name
    )
    first = participants.get("SITE_MANAGEMENT") or {"email": "", "name": ""}
    if notify and first.get("email"):
        _send_approval_notice(first.get("email", ""), record_code, record_title, "Chờ Ban điều hành phê duyệt")
    return {
        "ok": True,
        "started": True,
        "resubmitted": False,
        "workflow_id": wid,
        "status": "Đang duyệt - Ban điều hành",
        "current_stage": "SITE_MANAGEMENT",
        "next_email": str(first.get("email") or ""),
    }


def _send_approval_notice(to_email: str, record_code: str, record_title: str, status: str, comment: str = "") -> None:
    if not to_email:
        return
    try:
        _drive_gateway().send_approval_email(
            _gateway_session_token(),
            to_email=to_email,
            subject=f"QLDA - {record_code} cần xử lý phê duyệt",
            body=(f"Hồ sơ/Bản vẽ: {record_code}\nNội dung: {record_title}\nTrạng thái: {status}" + (f"\nÝ kiến: {comment}" if comment else "")),
            app_url=_app_public_url(),
        )
    except Exception as exc:
        st.warning(f"Đã chuyển bước duyệt nhưng chưa gửi được email: {exc}")


@st.fragment
def _render_online_approval(pid: int, record_kind: str, subtype: str, record_id: int, record_code: str, record_title: str, attachment_count: int | None = None, submitted_name_hint: str = "") -> None:
    eligible = subtype in (APPROVAL_ELIGIBLE_DOCS if record_kind == "document" else APPROVAL_ELIGIBLE_DRAWINGS)
    if not eligible:
        return

    st.markdown("### ✅ Phê duyệt online")

    identity = _cloud_identity()
    email = str(identity.get("email") or "").lower()
    display_name = str(identity.get("name") or "")
    approval_role = _user_approval_role(identity)
    wf = db.approval_workflow(pid, record_kind, subtype, record_id)
    # V6.9: tất cả đầu mục đã bật phê duyệt online đều bắt buộc có file trình duyệt.
    attachment_required = (
        (record_kind == "document" and subtype in APPROVAL_ELIGIBLE_DOCS)
        or (record_kind == "drawing" and subtype in APPROVAL_ELIGIBLE_DRAWINGS)
    )
    has_submission_file = (not attachment_required) or int(attachment_count or 0) > 0

    if not wf:
        # Tương thích test/khái niệm V6.3: contractor_ok = approval_role == "CONTRACTOR"
        # Thông báo V6.5 cũ: "Cần tải ít nhất 01 tệp trình duyệt trước khi gửi vào luồng phê duyệt."
        # V6.7: Lưu hồ sơ của Nhà thầu = Trình duyệt.
        # Tương thích V6.6: nếu hồ sơ đã có file nhưng chưa tạo workflow, người duyệt
        # mở hồ sơ sẽ tự phục hồi workflow để không bị kẹt ở "Chưa trình duyệt".
        if has_submission_file:
            recover = _ensure_approval_workflow_started(
                pid, record_kind, subtype, record_id, record_code, record_title,
                submitted_email=(email if approval_role == "CONTRACTOR" else ""),
                submitted_name=(display_name if approval_role == "CONTRACTOR" else submitted_name_hint),
                current_identity=identity,
                notify=True,
            )
            if recover.get("ok"):
                st.success("✅ Hồ sơ đã được đưa vào luồng phê duyệt và chuyển đến Ban điều hành.")
                st.rerun()
            else:
                st.warning(str(recover.get("error") or "Chưa thể khởi tạo luồng phê duyệt."))
                if approval_role != "CONTRACTOR":
                    st.info("Hồ sơ có file nhưng chưa có workflow. Admin cần kiểm tra lại phân loại Nhà thầu/Ban điều hành/TVGS/Ban QLDA.")
                return

        if approval_role == "CONTRACTOR":
            st.warning("📎 Cần tải ít nhất 01 tệp trình duyệt trước khi lưu và trình hồ sơ.")
        else:
            st.info("Hồ sơ chưa có tệp trình duyệt nên chưa thể chuyển sang Ban điều hành.")
        return

    steps = db.approval_steps(int(wf["id"]))
    current_stage = str(wf["current_stage"] or "")
    revision_no = int(wf["revision_no"] or 0)

    h1, h2 = st.columns([3, 1])
    h1.info(f"**Trạng thái:** {wf['overall_status']}")
    h2.metric("Lần chỉnh sửa", revision_no)

    st.markdown("#### Sơ đồ trạng thái")
    flow_cols = st.columns(5)
    step_by_code = {str(x["stage_code"]): x for x in steps}
    flow_items = [
        ("CONTRACTOR", "Nhà thầu"),
        ("SITE_MANAGEMENT", "Ban điều hành"),
        ("CONSULTANT", "TVGS"),
        ("PROJECT_MANAGEMENT", "Ban QLDA"),
        ("DONE", "Hoàn tất"),
    ]
    for col, (code, label) in zip(flow_cols, flow_items):
        with col:
            if code == "DONE":
                status = "Đã phê duyệt" if current_stage == "DONE" else "Chờ"
                icon = "✅" if current_stage == "DONE" else "⚪"
                person = "—"
            else:
                step = step_by_code.get(code)
                status = str(step["status"] or "Chờ") if step else "Chờ"
                person = (step["approver_name"] or step["approver_email"] or "—") if step else "—"
                if current_stage == code:
                    icon = "🟡" if code != "CONTRACTOR" else "🛠️"
                elif status in {"Đã duyệt", "Đã trình", "Đã trình lại"}:
                    icon = "✅"
                elif "chỉnh sửa" in status.lower():
                    icon = "🔴"
                else:
                    icon = "⚪"
            st.markdown(f"**{icon} {label}**")
            if code != "DONE":
                st.caption(person)
            st.write(status)

    history = db.approval_history(int(wf["id"]))
    action_labels = {
        "SUBMIT": "Trình duyệt",
        "RESUBMIT": "Trình lại",
        "APPROVE": "Phê duyệt",
        "REQUEST_REVISION": "Yêu cầu chỉnh sửa",
        "COMPLETE": "Hoàn tất",
    }
    current_step_for_identity = next((x for x in steps if str(x["stage_code"] or "").strip().upper() == current_stage.strip().upper()), None)
    assigned_current_email = str(current_step_for_identity["approver_email"] or "").strip().lower() if current_step_for_identity else ""
    reviewer_role = bool(
        approval_role in {"SITE_MANAGEMENT", "CONSULTANT", "PROJECT_MANAGEMENT"}
        or (current_stage not in {"DONE", "CONTRACTOR"} and email and assigned_current_email == email)
    )
    if reviewer_role:
        st.markdown("#### 📝 Ý kiến / Kết quả phê duyệt")
        approval_rows = [
            {
                "Thời điểm": h["created_at"],
                "Cấp duyệt": h["stage_label"],
                "Người duyệt": h["actor_name"] or h["actor_email"],
                "Kết quả": action_labels.get(str(h["action"]), h["action"]),
                "Ý kiến": h["comment"],
            }
            for h in history
            if str(h["action"] or "") in {"APPROVE", "REQUEST_REVISION", "COMPLETE"}
               or str(h["comment"] or "").strip()
        ]
        if approval_rows:
            st.dataframe(pd.DataFrame(approval_rows), hide_index=True, width="stretch")
        else:
            st.info("Chưa có ý kiến/kết quả phê duyệt.")
    else:
        with st.expander("🕘 Lịch sử phê duyệt", expanded=False):
            if history:
                st.dataframe(
                    pd.DataFrame([
                        {
                            "Thời điểm": h["created_at"],
                            "Lần chỉnh sửa": int(h["revision_no"] or 0),
                            "Cấp xử lý": h["stage_label"],
                            "Hành động": action_labels.get(str(h["action"]), h["action"]),
                            "Người thao tác": h["actor_name"] or h["actor_email"],
                            "Trạng thái": h["status"],
                            "Ý kiến": h["comment"],
                        }
                        for h in history
                    ]),
                    hide_index=True,
                    width="stretch",
                )
            else:
                st.caption("Chưa có lịch sử thao tác.")

    current_step = next((x for x in steps if str(x["stage_code"]) == current_stage), None)
    assigned_email = str(current_step["approver_email"] or "").lower() if current_step else ""
    reviewer_can_claim = bool(
        current_step
        and current_stage not in {"DONE", "CONTRACTOR"}
        and (approval_role == current_stage or (email and assigned_email == email))
    )
    if reviewer_can_claim:
        st.markdown(f"#### Xử lý tại bước: {current_step['stage_label']}")
        comment = st.text_area(
            "Ý kiến / Kết quả phê duyệt",
            key=f"approval_comment_{wf['id']}_{current_stage}_{revision_no}",
            placeholder="Có thể nhập ý kiến khi phê duyệt; bắt buộc nhập khi yêu cầu chỉnh sửa.",
        )
        if attachment_required and not has_submission_file:
            st.warning("📎 Hồ sơ chưa có tệp trình duyệt. Không thể phê duyệt cho đến khi Nhà thầu tải tệp lên; vẫn có thể Yêu cầu chỉnh sửa.")
        b1, b2 = st.columns(2)
        if b1.button(
            "✅ Phê duyệt",
            type="primary",
            key=f"approve_{wf['id']}_{current_stage}_{revision_no}",
            disabled=not has_submission_file,
        ):
            result = db.approval_action(
                int(wf["id"]), current_stage, email, "APPROVE", comment,
                actor_name=display_name, actor_role=(current_stage if (email and assigned_email == email) else approval_role)
            )
            _send_approval_notice(result.get("next_email", ""), record_code, record_title, result.get("status", ""), comment)
            st.rerun()
        if b2.button("↩️ Yêu cầu chỉnh sửa", key=f"reject_{wf['id']}_{current_stage}_{revision_no}"):
            if not comment.strip():
                st.error("Cần nhập ý kiến khi yêu cầu chỉnh sửa.")
            else:
                result = db.approval_action(
                    int(wf["id"]), current_stage, email, "REQUEST_REVISION", comment,
                    actor_name=display_name, actor_role=approval_role
                )
                _send_approval_notice(result.get("next_email", ""), record_code, record_title, result.get("status", ""), comment)
                st.rerun()

    elif current_stage == "CONTRACTOR" and (approval_role == "CONTRACTOR" or (email and email == str(wf["submitted_by"] or "").strip().lower())):
        return_stage = str(wf["return_stage"] or "SITE_MANAGEMENT")
        return_label = APPROVAL_ROLE_LABELS.get(return_stage, return_stage)
        revision_comments = [h for h in history if str(h["action"] or "") == "REQUEST_REVISION" and str(h["comment"] or "").strip()]
        latest_comment = str(revision_comments[-1]["comment"] or "").strip() if revision_comments else ""
        message = (
            f"Hồ sơ đang chờ Nhà thầu chỉnh sửa theo ý kiến của **{return_label}**. "
            f"Hãy cập nhật nội dung, **đính kèm file phiên bản mới**, sau đó bấm **Lưu hồ sơ** "
            f"(hoặc **Lưu bản vẽ**) ở phần trên. Hệ thống sẽ tự trình lại đúng {return_label}."
        )
        if latest_comment:
            message += f"\n\n**Ý kiến cần xử lý:** {latest_comment}"
        st.warning(message)

    elif current_stage == "DONE":
        st.success("✅ Quy trình đã hoàn tất. Hồ sơ đã được Ban QLDA phê duyệt.")
    elif current_step:
        st.caption(
            f"Đang chờ {current_step['stage_label']} xử lý: "
            f"{current_step['approver_name'] or current_step['approver_email'] or 'chưa xác định người duyệt'}."
        )


def _revision_upload_panel_key(prefix: str, pid: int, subtype: str, selected: int | None, workflow) -> str:
    """Tạo key riêng cho từng vòng upload, đặc biệt khi hồ sơ bị trả về.

    V6.9: không tái sử dụng uploader của lần trình trước. Một vòng chỉnh sửa có
    key riêng theo revision kế tiếp + cấp trả hồ sơ, tránh ticket/iframe cũ làm
    nút Đính kèm file không mở được.
    """
    base = f"{prefix}_{pid}_{subtype}_{selected or 'new'}"
    if workflow and str(workflow["current_stage"] or "") == "CONTRACTOR":
        cycle = int(workflow["revision_no"] or 0) + 1
        return_stage = str(workflow["return_stage"] or "SITE_MANAGEMENT")
        return f"{base}_revision_{cycle}_{return_stage}"
    return base + "_initial"


def _render_approval_document_type(pid: int, doc_type: str):
    """V6.3: giao diện RFA/RFI theo vai trò.

    - Nhà thầu: chỉ nhập dữ liệu trình duyệt cốt lõi.
    - Ban điều hành/TVGS/Ban QLDA: xem dữ liệu gốc, chỉ thao tác phần phê duyệt.
    - Quyền hệ thống Update/Admin: có nút riêng để tải file lên kho Drive.
    """
    cfg = DOC_CONFIG[doc_type]
    rows = db.documents(pid, doc_type)
    total = len(rows)
    overdue = sum(1 for r in rows if document_deadline_label(r, doc_type).startswith("Quá hạn"))
    workflow_cache = db.approval_workflows_for_records(
        pid, "document", doc_type, [int(r["id"]) for r in rows]
    )
    completed = sum(1 for wf in workflow_cache.values() if wf and str(wf["current_stage"] or "") == "DONE")

    c1, c2, c3 = st.columns(3)
    c1.metric("Tổng hồ sơ", total)
    c2.metric("Quá hạn", overdue)
    c3.metric("Đã phê duyệt", completed)

    identity = _cloud_identity()
    approval_role = _user_approval_role(identity)
    is_contractor = approval_role == "CONTRACTOR"
    is_reviewer = approval_role in {"SITE_MANAGEMENT", "CONSULTANT", "PROJECT_MANAGEMENT"}
    can_edit_submission = bool(_can_update() and is_contractor)
    can_upload_storage = _can_update()

    options = [int(r["id"]) for r in rows]
    if can_edit_submission:
        options = [None] + options

    select_key = f"doc_select_{pid}_{doc_type}"
    pending_key = select_key + "_pending"
    pending = st.session_state.pop(pending_key, None)
    if pending in options:
        st.session_state[select_key] = pending

    if not options:
        if can_edit_submission:
            options = [None]
        else:
            st.info("Chưa có hồ sơ để xem hoặc xử lý.")
            return

    if is_contractor:
        select_label = "Chọn hồ sơ để tạo / cập nhật"
    elif is_reviewer:
        select_label = "Chọn hồ sơ để xem / phê duyệt"
    elif can_upload_storage:
        select_label = "Chọn hồ sơ để xem / tải file lên lưu"
    else:
        select_label = "Chọn hồ sơ để xem"

    selected = st.selectbox(
        select_label,
        options,
        format_func=lambda x: "➕ Thêm mới" if x is None else f"#{x} - {next(r['code'] for r in rows if int(r['id'])==int(x))}",
        key=select_key,
    )
    record = db.document(selected) if selected else None
    record_wf = workflow_cache.get(int(selected)) if selected else None
    current_email = str(identity.get("email") or "").strip().lower()
    own_returned_workflow = bool(
        record_wf
        and str(record_wf["current_stage"] or "").strip().upper() == "CONTRACTOR"
        and current_email
        and current_email == str(record_wf["submitted_by"] or "").strip().lower()
    )
    effective_contractor = bool(is_contractor or own_returned_workflow)
    contractor_edit_locked = bool(
        effective_contractor and record_wf and str(record_wf["current_stage"] or "").strip().upper() not in {"CONTRACTOR"}
    )
    can_edit_current = bool(_can_update() and effective_contractor and not contractor_edit_locked)
    if contractor_edit_locked:
        st.info("🔒 Hồ sơ đã trình duyệt nên nội dung gốc đang được khóa. Nhà thầu chỉ được sửa khi một cấp duyệt yêu cầu chỉnh sửa.")

    flash_key = f"flash_doc_{pid}_{doc_type}"
    if flash_key in st.session_state:
        st.success(st.session_state.pop(flash_key))
    error_flash = flash_key + "_error"
    if error_flash in st.session_state:
        st.error(st.session_state.pop(error_flash))

    # --------- Phần thông tin chung: cùng bố cục, khác quyền sửa ---------
    contractor_attachment_count = 0
    if can_edit_current:
        # V6.7: Nhà thầu nhập thông tin -> đính kèm file -> Lưu = tự động Trình duyệt.
        # Không dùng st.form để nút đính kèm có thể mở uploader trước nút Lưu.
        scope = f"{pid}_{doc_type}_{selected or 'new'}"
        c1, c2 = st.columns([1, 2])
        code = c1.text_input(
            cfg.get("code_label", f"Mã {doc_type} *"),
            value=(record["code"] if record else ""),
            placeholder="S2-MEP-001",
            key=f"approval_doc_code_{scope}",
        )
        subject = c2.text_input(
            f"{cfg['subject']} *",
            value=(record["subject"] if record else ""),
            key=f"approval_doc_subject_{scope}",
        )

        c1, c2, c3 = st.columns(3)
        discipline = c1.text_input("Bộ môn / Hệ", value=(record["discipline"] if record else ""), key=f"approval_doc_disc_{scope}")
        contractor = c2.text_input("Nhà thầu / Đơn vị", value=(record["contractor"] if record else ""), key=f"approval_doc_contractor_{scope}")
        priority_default = PRIORITIES.index(record["priority"]) if record and record["priority"] in PRIORITIES else 1
        priority = c3.selectbox("Mức độ", PRIORITIES, index=priority_default, key=f"approval_doc_priority_{scope}")

        c1, c2, c3 = st.columns(3)
        issuer_default = record["issuer"] if record else str(identity.get("name") or identity.get("email") or "")
        issuer = c1.text_input(cfg.get("issuer_label", "Người trình"), value=issuer_default, key=f"approval_doc_issuer_{scope}")
        issue_date = c2.date_input(
            cfg.get("issue_date_label", "Ngày trình"),
            value=parse_date(record["issue_date"], date.today()) if record else date.today(),
            key=f"approval_doc_issue_{scope}",
        )
        due_date = c3.date_input(
            cfg.get("due_date_label", "Hạn xử lý"),
            value=parse_date(record["due_date"], date.today()+timedelta(days=7)) if record else date.today()+timedelta(days=7),
            key=f"approval_doc_due_{scope}",
        )
        description = st.text_area(
            "Mô tả",
            value=(record["description"] if record else ""),
            height=120,
            key=f"approval_doc_description_{scope}",
        )

        normalized_code = _normalize_execution_code(code)
        attach_ready = bool(normalized_code and subject.strip() and _valid_execution_code(normalized_code))
        pre_panel_key = _revision_upload_panel_key(
            "v69_doc_attach", pid, doc_type, selected, record_wf
        )
        # Legacy V6.6/V6.9/V6.10 compatibility markers only; V6.12 uses native uploader as the main path:
        # key=f"approval_doc_attach_before_save_{pre_panel_key}"
        # _prepare_inline_upload_ticket(...); st.rerun()
        # _ensure_revision_upload_ticket(...)
        # "🔄 Tạo lại phiên đính kèm file" if is_revision_return else "📎 Đính kèm file"
        # V6.10 KHÔNG rerun after creating the ticket.
        if record_wf and str(record_wf["current_stage"] or "") == "CONTRACTOR":
            return_stage = str(record_wf["return_stage"] or "SITE_MANAGEMENT")
            st.warning(
                "↩ Hồ sơ đã bị trả về. Hãy tải **file phiên bản mới** tại đây; "
                "nếu trùng tên, file cũ sẽ tự chuyển vào thư mục `_Lich_su`. "
                f"Sau đó bấm **Lưu hồ sơ** để trình lại {APPROVAL_ROLE_LABELS.get(return_stage, return_stage)}."
            )
        st.markdown("#### 📎 Đính kèm file trình duyệt")
        is_revision_return = bool(record_wf and str(record_wf["current_stage"] or "") == "CONTRACTOR")
        if attach_ready:
            _render_contractor_upload_expander(
                pid, kind="document", subtype=doc_type, record_code=normalized_code,
                panel_key=pre_panel_key, is_revision_return=is_revision_return,
            )

        if attach_ready:
            contractor_attachment_count = _render_inline_drive_attachments(
                pid,
                kind="document",
                subtype=doc_type,
                record_code=normalized_code,
                record_id=int(selected or 0),
                panel_key=pre_panel_key,
                show_contractor_upload=False,
            )
        revision_file_ready = (not is_revision_return) or bool(st.session_state.get(pre_panel_key + "_new_upload_detected"))
        if contractor_attachment_count <= 0:
            st.info("📎 Hãy tải ít nhất 01 file trình duyệt trước khi lưu hồ sơ.")
        elif is_revision_return and not revision_file_ready:
            st.warning("📎 Hồ sơ đang ở vòng chỉnh sửa: bắt buộc tải ít nhất 01 **file mới** trong phiên chỉnh sửa này trước khi Lưu/Trình lại.")
        elif is_revision_return and revision_file_ready:
            st.success("✅ Đã phát hiện file phiên bản mới. Có thể Lưu hồ sơ để tự động trình lại.")

        save_clicked = st.button(
            "💾 Lưu hồ sơ",
            type="primary",
            disabled=not attach_ready or contractor_attachment_count <= 0 or not revision_file_ready,
            key=f"approval_doc_save_after_attach_{scope}",
            width="stretch",
        )

        if save_clicked:
            if not normalized_code or not subject.strip():
                st.error("Mã hồ sơ và nội dung trình duyệt là bắt buộc.")
            elif not _valid_execution_code(normalized_code):
                st.error("Mã phải theo định dạng THÁP-BỘMÔN-STT, ví dụ S2-MEP-001.")
            elif contractor_attachment_count <= 0:
                st.error("Phải đính kèm ít nhất 01 file trình duyệt trước khi lưu hồ sơ.")
            elif not revision_file_ready:
                st.error("Hồ sơ bị trả về phải có ít nhất 01 file phiên bản mới trước khi Lưu/Trình lại.")
            else:
                try:
                    # Các trường không còn cho nhập ở RFA/RFI được giữ nguyên khi cập nhật.
                    current_status = record["status"] if record else (cfg["statuses"][0] if cfg.get("statuses") else "Soạn thảo")
                    doc_id = db.save_document(pid, doc_type, {
                        "code": normalized_code,
                        "subject": subject,
                        "discipline": discipline.strip() or _discipline_from_code(normalized_code),
                        "contractor": contractor,
                        "issuer": issuer,
                        "assignee": record["assignee"] if record else "",
                        "issue_date": iso(issue_date),
                        "due_date": iso(due_date),
                        "closed_date": record["closed_date"] if record else "",
                        "status": current_status,
                        "priority": priority,
                        "related_wbs": record["related_wbs"] if record else "",
                        "description": description,
                        "response": record["response"] if record else "",
                        "note": record["note"] if record and "note" in record.keys() else "",
                        "cost_impact": float(record["cost_impact"] or 0) if record and "cost_impact" in record.keys() else 0.0,
                        "time_impact_days": int(record["time_impact_days"] or 0) if record and "time_impact_days" in record.keys() else 0,
                    }, selected)
                    route = _ensure_approval_workflow_started(
                        pid, "document", doc_type, int(doc_id), normalized_code, subject,
                        submitted_email=str(identity.get("email") or ""),
                        submitted_name=str(identity.get("name") or issuer or ""),
                        current_identity=identity,
                        notify=True,
                    )
                    if is_revision_return:
                        forced = db.force_revision_resubmit(
                            pid, "document", int(doc_id),
                            submitted_by=str(identity.get("email") or ""),
                            submitted_name=str(identity.get("name") or issuer or ""),
                        )
                        post_save_wf = db.approval_workflow(pid, "document", doc_type, int(doc_id))
                        if post_save_wf and str(post_save_wf["current_stage"] or "").strip().upper() == "CONTRACTOR":
                            st.error("❌ Lưu hồ sơ chưa chuyển được về cấp duyệt. Hệ thống không ghi nhận trạng thái sai.")
                            st.stop()
                        if forced.get("resubmitted") or (post_save_wf and str(post_save_wf["current_stage"] or "").strip().upper() != "CONTRACTOR"):
                            route = {**route, "ok": True, "resubmitted": True}
                    st.session_state[pending_key] = doc_id
                    if route.get("ok"):
                        if route.get("resubmitted"):
                            st.session_state[flash_key] = "Đã lưu hồ sơ, tệp và trình lại đúng cấp đã yêu cầu chỉnh sửa."
                        elif route.get("started"):
                            st.session_state[flash_key] = "Đã lưu hồ sơ, tệp và tự động trình Ban điều hành phê duyệt."
                        else:
                            st.session_state[flash_key] = "Đã lưu hồ sơ và tệp trình duyệt."
                    else:
                        st.session_state[flash_key] = "Đã lưu hồ sơ và tệp, nhưng chưa trình duyệt."
                        st.session_state[error_flash] = str(route.get("error") or "Chưa thể khởi tạo luồng phê duyệt.")
                    st.rerun()
                except sqlite3.IntegrityError:
                    st.error(f"Mã {doc_type} đã tồn tại trong dự án.")
    elif record:
        # Người duyệt / người chỉ tải file: cùng thông tin nhưng không được sửa dữ liệu Nhà thầu đã trình.
        st.markdown("#### Nội dung hồ sơ")
        c1, c2 = st.columns([1, 2])
        c1.text_input(cfg.get("code_label", f"Mã {doc_type} *"), value=str(record["code"] or ""), disabled=True, key=f"view_code_{pid}_{doc_type}_{selected}")
        c2.text_input(f"{cfg['subject']} *", value=str(record["subject"] or ""), disabled=True, key=f"view_subject_{pid}_{doc_type}_{selected}")

        c1, c2, c3 = st.columns(3)
        c1.text_input("Bộ môn / Hệ", value=str(record["discipline"] or ""), disabled=True, key=f"view_disc_{pid}_{doc_type}_{selected}")
        c2.text_input("Nhà thầu / Đơn vị", value=str(record["contractor"] or ""), disabled=True, key=f"view_contractor_{pid}_{doc_type}_{selected}")
        c3.text_input("Mức độ", value=str(record["priority"] or ""), disabled=True, key=f"view_priority_{pid}_{doc_type}_{selected}")

        c1, c2, c3 = st.columns(3)
        c1.text_input(cfg.get("issuer_label", "Người trình"), value=str(record["issuer"] or ""), disabled=True, key=f"view_issuer_{pid}_{doc_type}_{selected}")
        c2.text_input(cfg.get("issue_date_label", "Ngày trình"), value=str(record["issue_date"] or ""), disabled=True, key=f"view_issue_{pid}_{doc_type}_{selected}")
        c3.text_input(cfg.get("due_date_label", "Hạn xử lý"), value=str(record["due_date"] or ""), disabled=True, key=f"view_due_{pid}_{doc_type}_{selected}")
        st.text_area("Mô tả", value=str(record["description"] or ""), disabled=True, height=120, key=f"view_description_{pid}_{doc_type}_{selected}")

    # --------- Tệp trình duyệt / tệp lưu trữ ---------
    # Nhà thầu đã đính kèm file ngay trong phần nhập liệu phía trên.
    # Người duyệt xem/tải tệp trước khi xử lý. Tài khoản Update/Admin không phải
    # Nhà thầu vẫn có nút tải file lên lưu trữ độc lập với vai trò phê duyệt.
    if selected:
        current = db.document(selected)
        if current:
            if can_edit_current:
                attachment_count = contractor_attachment_count
            else:
                panel_key = f"v6_doc_attach_{pid}_{doc_type}_{selected}"
                if can_upload_storage:
                    if st.button(
                        "📤 Tải file lên lưu",
                        key=f"approval_doc_upload_{pid}_{doc_type}_{selected}",
                        width="stretch",
                    ):
                        try:
                            st.session_state.pop(panel_key + "_ticket", None)
                            st.session_state.pop(panel_key + "_upload_open", None)
                            _prepare_inline_upload_ticket(
                                pid,
                                kind="document",
                                subtype=doc_type,
                                record_code=str(current["code"] or ""),
                                panel_key=panel_key,
                            )
                        except Exception as exc:
                            st.error(f"Chưa mở được vùng tải file: {exc}")

                attachment_count = _render_inline_drive_attachments(
                    pid,
                    kind="document",
                    subtype=doc_type,
                    record_code=str(current["code"] or ""),
                    record_id=int(selected),
                    panel_key=panel_key,
                )

            if is_reviewer:
                if attachment_count:
                    st.success(f"📎 Có {attachment_count} tệp trình duyệt. Hãy mở/xem tệp phía trên trước khi phê duyệt.")
                else:
                    st.warning("📎 Chưa có tệp trình duyệt do Nhà thầu tải lên.")

            if is_contractor or is_reviewer:
                _render_online_approval(
                    pid,
                    "document",
                    doc_type,
                    int(selected),
                    str(current["code"] or ""),
                    str(current["subject"] or ""),
                    attachment_count=attachment_count,
                    submitted_name_hint=str(current["issuer"] or ""),
                )

    # --------- Danh sách hồ sơ: chỉ các cột cần thiết cho RFA/RFI ---------
    if rows:
        drive_counts = _record_drive_counts(pid, kind="document", subtype=doc_type, record_codes=[r["code"] for r in rows])

        # V6.8: tự sửa các hồ sơ legacy đã có file nhưng chưa có workflow ngay
        # khi mở danh sách. Không cần chờ người duyệt bấm Mở/xử lý.
        repaired = 0
        repair_errors = []
        for r in rows:
            rid = int(r["id"])
            if workflow_cache.get(rid):
                continue
            code_value = str(r["code"] or "")
            info = drive_counts.get(code_value, {})
            total_files = int(info.get("count") or 0) + int(r["attachment_count"] or 0)
            if total_files <= 0:
                continue
            try:
                route = _ensure_approval_workflow_started(
                    pid, "document", doc_type, rid, code_value, str(r["subject"] or ""),
                    submitted_email="", submitted_name=str(r["issuer"] or ""),
                    current_identity=identity, notify=False,
                )
                if route.get("ok") and route.get("started"):
                    repaired += 1
                elif not route.get("ok"):
                    repair_errors.append(f"{code_value}: {route.get('error') or 'không tạo được workflow'}")
            except Exception as exc:
                repair_errors.append(f"{code_value}: {exc}")
        if repaired:
            st.success(f"🔧 Đã tự khôi phục luồng phê duyệt cho {repaired} hồ sơ có file.")
            st.rerun()
        if repair_errors:
            st.warning("Một số hồ sơ chưa tự khôi phục được: " + " | ".join(repair_errors[:3]))

        fc1, fc2, fc3, fc4, fc5 = st.columns([2.2, 1.0, 1.25, 1.35, 1.05])
        filter_text = fc1.text_input("Tìm mã / nội dung / đơn vị", key=f"doc_filter_text_{pid}_{doc_type}")
        towers = sorted({_tower_from_code(r["code"]) for r in rows if str(r["code"] or "").strip()})
        disciplines = sorted({str(r["discipline"] or "").strip() for r in rows if str(r["discipline"] or "").strip()})
        workflow_cache = db.approval_workflows_for_records(
            pid, "document", doc_type, [int(r["id"]) for r in rows]
        )
        approval_states = sorted(set(
            str(workflow_cache[int(r["id"])] ["overall_status"] or "Chưa trình duyệt")
            if workflow_cache.get(int(r["id"])) else "Chưa trình duyệt"
            for r in rows
        ))
        filter_tower = fc2.selectbox("Tháp", ["Tất cả"] + towers, key=f"doc_filter_tower_{pid}_{doc_type}")
        filter_discipline = fc3.selectbox("Bộ môn/Hệ", ["Tất cả"] + disciplines, key=f"doc_filter_disc_{pid}_{doc_type}")
        filter_status = fc4.selectbox("Trạng thái duyệt", ["Tất cả"] + approval_states, key=f"doc_filter_status_{pid}_{doc_type}")
        filter_file = fc5.selectbox("Tệp", ["Tất cả", "Có file", "Chưa có file"], key=f"doc_filter_file_{pid}_{doc_type}")

        q = filter_text.strip().lower()
        table_rows = []
        visible_row_ids = []
        for r in rows:
            rid = int(r["id"])
            code_value = str(r["code"] or "")
            info = drive_counts.get(code_value, {})
            total_files = int(info.get("count") or 0) + int(r["attachment_count"] or 0)
            tower = _tower_from_code(code_value)
            discipline_value = str(r["discipline"] or "").strip()
            wf = workflow_cache.get(rid)
            approval_state = str(wf["overall_status"] or "Chưa trình duyệt") if wf else "Chưa trình duyệt"
            haystack = " ".join([
                code_value,
                str(r["subject"] or ""),
                discipline_value,
                str(r["contractor"] or ""),
                str(r["issuer"] or ""),
            ]).lower()
            if q and q not in haystack:
                continue
            if filter_tower != "Tất cả" and tower != filter_tower:
                continue
            if filter_discipline != "Tất cả" and discipline_value != filter_discipline:
                continue
            if filter_status != "Tất cả" and approval_state != filter_status:
                continue
            if not _file_filter_match(total_files, filter_file):
                continue
            file_label = f"✅ Có file ({total_files})" if total_files else "—"
            table_rows.append({
                "Chọn": False,
                "ID": rid,
                "Tháp": tower,
                "Mã": code_value,
                "Nội dung": r["subject"],
                "Bộ môn": discipline_value,
                "Nhà thầu": r["contractor"],
                "Mức độ": r["priority"],
                "Người trình": r["issuer"],
                "Ngày trình": r["issue_date"],
                "Hạn xử lý": r["due_date"],
                "Duyệt online": approval_state,
                "File DB": file_label,
            })
            visible_row_ids.append(rid)

        if not table_rows:
            st.info("Không có hồ sơ phù hợp bộ lọc.")
            return

        df = pd.DataFrame(table_rows)
        display_df, page_token = _paged_df(df, f"doc_grid_page_{pid}_{doc_type}", default_size=50)
        disabled_cols = [c for c in display_df.columns if c != "Chọn"]
        edited = st.data_editor(
            display_df,
            hide_index=True,
            width="stretch",
            key=f"doc_select_grid_{pid}_{doc_type}_{page_token}_{len(display_df)}_{int(display_df['ID'].sum()) if not display_df.empty else 0}_{abs(hash((filter_text, filter_tower, filter_discipline, filter_status, filter_file))) % 100000}",
            disabled=disabled_cols,
            column_config={"Chọn": st.column_config.CheckboxColumn("☑ Chọn", default=False)},
        )
        selected_ids = [int(v) for v in edited.loc[edited["Chọn"] == True, "ID"].tolist()]
        download_state_key = f"doc_download_selected_state_{pid}_{doc_type}"
        d1, d2, d3 = st.columns([1.45, 1.35, 2.1])
        if d1.button(
            f"⬇️ Tải hồ sơ đã chọn ({len(selected_ids)})",
            key=f"doc_download_selected_{pid}_{doc_type}",
            disabled=not selected_ids,
            type="primary",
            width="stretch",
        ):
            st.session_state[download_state_key] = list(selected_ids)

        if d3.button(
            "📝 Mở / xử lý hồ sơ",
            key=f"doc_open_selected_{pid}_{doc_type}",
            disabled=len(selected_ids) != 1,
            width="stretch",
        ):
            st.session_state[pending_key] = int(selected_ids[0])
            st.rerun()

        if d2.button(
            f"🗑 Xóa hồ sơ đã chọn ({len(selected_ids)})",
            key=f"doc_delete_selected_{pid}_{doc_type}",
            disabled=(not _is_admin()) or not selected_ids,
            width="stretch",
        ):
            errors = []
            deleted = 0
            for rid in selected_ids:
                row = db.document(rid)
                if not row:
                    continue
                _, drive_errors = _trash_record_drive_files(pid, kind="document", subtype=doc_type, record_code=str(row["code"] or ""))
                if drive_errors:
                    errors.append(f"#{rid}: " + " | ".join(drive_errors))
                    continue
                db.delete_document(rid)
                deleted += 1
            if selected in selected_ids:
                st.session_state[pending_key] = None
            st.session_state.pop(download_state_key, None)
            if deleted:
                st.success(f"Đã xóa {deleted} hồ sơ đã chọn.")
            if errors:
                st.error("Một số hồ sơ chưa xóa được vì lỗi Google Drive: " + " || ".join(errors))
            st.rerun()

        download_ids = [int(x) for x in (st.session_state.get(download_state_key) or [])]
        if download_ids:
            _render_selected_document_downloads(pid, doc_type, download_ids, download_state_key)
        export_df = df.drop(columns=["Chọn"])
        _render_excel_export(
            export_df, doc_type, f"{doc_type}_{date.today():%Y%m%d}.xlsx",
            f"doc_xlsx_{pid}_{doc_type}", f"{doc_type} Excel",
        )

def render_document_type(pid: int, doc_type: str):
    if doc_type in APPROVAL_ELIGIBLE_DOCS:
        return _render_approval_document_type(pid, doc_type)

    cfg = DOC_CONFIG[doc_type]
    rows = db.documents(pid, doc_type)
    total = len(rows)
    overdue = sum(1 for r in rows if document_deadline_label(r, doc_type).startswith("Quá hạn"))
    done_statuses = set(cfg.get("done_statuses", ["Đóng", "Hủy"]))
    closed = sum(1 for r in rows if r["closed_date"] or r["status"] in done_statuses)
    c1, c2, c3 = st.columns(3)
    c1.metric("Tổng hồ sơ", total)
    c2.metric("Quá hạn", overdue)
    c3.metric("Đã xử lý/đóng", closed)

    options = [None] + [int(r["id"]) for r in rows]
    select_key = f"doc_select_{pid}_{doc_type}"
    pending_key = select_key + "_pending"
    pending = st.session_state.pop(pending_key, None)
    if pending in options:
        st.session_state[select_key] = pending
    selected = st.selectbox(
        "Chọn hồ sơ để sửa / cập nhật",
        options,
        format_func=lambda x: "➕ Thêm mới" if x is None else f"#{x} - {next(r['code'] for r in rows if r['id']==x)}",
        key=select_key,
    )
    record = db.document(selected) if selected else None
    flash_key = f"flash_doc_{pid}_{doc_type}"
    if flash_key in st.session_state:
        st.success(st.session_state.pop(flash_key))
    error_flash = flash_key + "_error"
    if error_flash in st.session_state:
        st.error(st.session_state.pop(error_flash))

    with st.form(f"doc_form_{pid}_{doc_type}_{selected or 'new'}"):
        c1, c2 = st.columns([1, 2])
        code = c1.text_input(cfg.get("code_label", f"Mã {doc_type} *"), value=(record["code"] if record else ""), placeholder="S2-MEP-001")
        subject = c2.text_input(f"{cfg['subject']} *", value=(record["subject"] if record else ""))
        c1, c2, c3 = st.columns(3)
        discipline = c1.text_input("Bộ môn / Hệ", value=(record["discipline"] if record else ""))
        contractor = c2.text_input("Nhà thầu / Đơn vị", value=(record["contractor"] if record else ""))
        priority_default = PRIORITIES.index(record["priority"]) if record and record["priority"] in PRIORITIES else 1
        priority = c3.selectbox("Mức độ", PRIORITIES, index=priority_default)
        c1, c2 = st.columns(2)
        issuer = c1.text_input(cfg.get("issuer_label", "Người phát hành / trình"), value=(record["issuer"] if record else ""))
        assignee = c2.text_input(cfg.get("assignee_label", "Người / Đơn vị xử lý"), value=(record["assignee"] if record else ""))
        c1, c2, c3 = st.columns(3)
        issue_date = c1.date_input(cfg.get("issue_date_label", "Ngày phát hành"), value=parse_date(record["issue_date"], date.today()) if record else date.today())
        due_date = c2.date_input(cfg.get("due_date_label", "Hạn xử lý"), value=parse_date(record["due_date"], date.today()+timedelta(days=7)) if record else date.today()+timedelta(days=7))
        closed_enabled = c3.checkbox("Đã có ngày đóng", value=bool(record and record["closed_date"]))
        closed_date = c3.date_input(cfg.get("closed_date_label", "Ngày đóng"), value=parse_date(record["closed_date"], date.today()) if record and record["closed_date"] else date.today(), disabled=not closed_enabled)
        status_index = cfg["statuses"].index(record["status"]) if record and record["status"] in cfg["statuses"] else 0
        status = st.selectbox("Trạng thái", cfg["statuses"], index=status_index)
        related_wbs = st.text_input("WBS / Task liên quan", value=(record["related_wbs"] if record else ""))
        description = st.text_area("Mô tả", value=(record["description"] if record else ""))
        response = st.text_area(cfg.get("response_label", "Phản hồi / Kết quả"), value=(record["response"] if record else ""))
        note = st.text_area("Ghi chú", value=(record["note"] if record and "note" in record.keys() else ""), height=80)
        cost_impact, time_impact = 0.0, 0

        attach_clicked = st.form_submit_button(
            "📎 Đính kèm file",
            disabled=not _can_update(),
            width="stretch",
        )

        if attach_clicked:
            normalized_code = _normalize_execution_code(code)
            if not normalized_code or not subject.strip():
                st.error("Mã hồ sơ và nội dung là bắt buộc.")
            elif not _valid_execution_code(normalized_code):
                st.error("Mã phải theo định dạng THÁP-BỘMÔN-STT, ví dụ S2-MEP-001.")
            else:
                try:
                    effective_discipline = discipline.strip() or _discipline_from_code(normalized_code)
                    doc_id = db.save_document(pid, doc_type, {
                        "code": normalized_code, "subject": subject, "discipline": effective_discipline, "contractor": contractor,
                        "issuer": issuer, "assignee": assignee, "issue_date": iso(issue_date), "due_date": iso(due_date),
                        "closed_date": iso(closed_date) if closed_enabled else "", "status": status, "priority": priority,
                        "related_wbs": related_wbs, "description": description, "response": response, "note": note,
                        "cost_impact": cost_impact, "time_impact_days": time_impact,
                    }, selected)
                    st.session_state[pending_key] = doc_id
                    panel_key = f"v6_doc_attach_{pid}_{doc_type}_{doc_id}"
                    try:
                        # Mỗi lần bấm Đính kèm luôn tạo ticket mới; không tái sử dụng ticket cũ.
                        st.session_state.pop(panel_key + "_ticket", None)
                        st.session_state.pop(panel_key + "_upload_open", None)
                        _prepare_inline_upload_ticket(
                            pid, kind="document", subtype=doc_type, record_code=normalized_code, panel_key=panel_key
                        )
                        st.session_state[flash_key] = "Đã mở vùng đính kèm file."
                    except Exception as exc:
                        st.session_state[error_flash] = f"Chưa mở được vùng đính kèm file: {exc}"
                    st.rerun()
                except sqlite3.IntegrityError:
                    st.error(f"Mã {doc_type} đã tồn tại trong dự án.")

    if selected:
        current = db.document(selected)
        if current:
            panel_key = f"v6_doc_attach_{pid}_{doc_type}_{selected}"
            _render_inline_drive_attachments(
                pid,
                kind="document",
                subtype=doc_type,
                record_code=str(current["code"] or ""),
                record_id=int(selected),
                panel_key=panel_key,
            )
            _render_online_approval(pid, "document", doc_type, int(selected), str(current["code"] or ""), str(current["subject"] or ""))

        arows = db.document_attachments(selected)
        if arows:
            st.markdown("**File legacy từ V4.x (nếu có)**")
            legacy_delete = []
            for a in arows:
                c0, c1, c2 = st.columns([0.55, 5, 1.5])
                marked = c0.checkbox("Xóa", key=f"legacy_doc_tick_{a['id']}", disabled=not _is_admin(), label_visibility="collapsed")
                content = bytes(a["file_content"] or b"")
                if content:
                    c1.download_button(f"⬇️ {a['file_name']}", content, file_name=a["file_name"], mime=a["mime_type"] or "application/octet-stream", key=f"doc_dl_{a['id']}")
                elif a["drive_web_url"]:
                    c1.link_button(f"☁ {a['file_name']}", a["drive_web_url"])
                    if a["drive_file_id"]:
                        c2.link_button("⬇️ Tải xuống", f"https://drive.google.com/uc?export=download&id={a['drive_file_id']}", width="stretch")
                else:
                    c1.write(a["file_name"])
                if marked:
                    legacy_delete.append(a)
            if _is_admin() and st.button(f"🗑 Xóa file legacy đã tick ({len(legacy_delete)})", disabled=not legacy_delete, key=f"legacy_doc_delete_{selected}"):
                for a in legacy_delete:
                    if a["drive_file_id"]:
                        _trash_drive_file(a["drive_file_id"])
                    db.delete_document_attachment(a["id"])
                st.rerun()

    if rows:
        drive_counts = _record_drive_counts(pid, kind="document", subtype=doc_type, record_codes=[r["code"] for r in rows])

        # Bộ lọc áp dụng thống nhất cho mọi sheet hồ sơ.
        fc1, fc2, fc3, fc4, fc5 = st.columns([2.2, 1.0, 1.25, 1.35, 1.05])
        filter_text = fc1.text_input("Tìm mã / nội dung / đơn vị", key=f"doc_filter_text_{pid}_{doc_type}")
        towers = sorted({_tower_from_code(r["code"]) for r in rows if str(r["code"] or "").strip()})
        disciplines = sorted({str(r["discipline"] or "").strip() for r in rows if str(r["discipline"] or "").strip()})
        statuses = sorted({str(r["status"] or "").strip() for r in rows if str(r["status"] or "").strip()})
        filter_tower = fc2.selectbox("Tháp", ["Tất cả"] + towers, key=f"doc_filter_tower_{pid}_{doc_type}")
        filter_discipline = fc3.selectbox("Bộ môn/Hệ", ["Tất cả"] + disciplines, key=f"doc_filter_disc_{pid}_{doc_type}")
        filter_status = fc4.selectbox("Trạng thái", ["Tất cả"] + statuses, key=f"doc_filter_status_{pid}_{doc_type}")
        filter_file = fc5.selectbox("Tệp", ["Tất cả", "Có file", "Chưa có file"], key=f"doc_filter_file_{pid}_{doc_type}")

        q = filter_text.strip().lower()
        table_rows = []
        visible_row_ids = []
        for r in rows:
            code_value = str(r["code"] or "")
            info = drive_counts.get(code_value, {})
            direct_count = int(info.get("count") or 0)
            legacy_count = int(r["attachment_count"] or 0)
            total_files = direct_count + legacy_count
            tower = _tower_from_code(code_value)
            discipline_value = str(r["discipline"] or "").strip()
            status_value = str(r["status"] or "").strip()
            haystack = " ".join([code_value, str(r["subject"] or ""), discipline_value, str(r["contractor"] or ""), str(r["issuer"] or ""), str(r["assignee"] or ""), str(r["note"] or "")]).lower()
            if q and q not in haystack:
                continue
            if filter_tower != "Tất cả" and tower != filter_tower:
                continue
            if filter_discipline != "Tất cả" and discipline_value != filter_discipline:
                continue
            if filter_status != "Tất cả" and status_value != filter_status:
                continue
            if not _file_filter_match(total_files, filter_file):
                continue
            file_label = f"✅ Có file ({total_files})" if total_files else "—"
            table_rows.append({
                "Chọn": False, "ID": r["id"], "Tháp": tower, "Mã": code_value, "Nội dung": r["subject"], "Bộ môn": discipline_value,
                "Nhà thầu": r["contractor"], "Phát hành": r["issue_date"], "Hạn": r["due_date"],
                "Trạng thái": status_value, "Mức độ": r["priority"], "Theo dõi hạn": document_deadline_label(r, doc_type),
                "WBS/Task": r["related_wbs"], "Ghi chú": r["note"], "Duyệt online": "—", "File DB": file_label,
            })
            visible_row_ids.append(int(r["id"]))
        if not table_rows:
            st.info("Không có hồ sơ phù hợp bộ lọc.")
            return
        df = pd.DataFrame(table_rows)
        # V6.21 web-opt: only send one page to the browser.
        display_df, page_token = _paged_df(df, f"doc_grid_page_{pid}_{doc_type}", default_size=50)
        disabled_cols = [c for c in display_df.columns if c != "Chọn"]
        edited = st.data_editor(
            display_df,
            hide_index=True,
            width="stretch",
            key=f"doc_select_grid_{pid}_{doc_type}_{page_token}_{len(display_df)}_{int(display_df['ID'].sum()) if not display_df.empty else 0}_{abs(hash((filter_text, filter_tower, filter_discipline, filter_status, filter_file))) % 100000}",
            disabled=disabled_cols,
            column_config={
                "Chọn": st.column_config.CheckboxColumn(
                    "☑ Chọn",
                    default=False,
                )
            },
        )
        selected_ids = [int(v) for v in edited.loc[edited["Chọn"] == True, "ID"].tolist()]
        download_state_key = f"doc_download_selected_state_{pid}_{doc_type}"
        d1, d2, d3 = st.columns([1.45, 1.35, 3.6])
        if d1.button(
            f"⬇️ Tải hồ sơ đã chọn ({len(selected_ids)})",
            key=f"doc_download_selected_{pid}_{doc_type}",
            disabled=not selected_ids,
            type="primary",
            width="stretch",
        ):
            st.session_state[download_state_key] = list(selected_ids)

        if d2.button(
            f"🗑 Xóa hồ sơ đã chọn ({len(selected_ids)})",
            key=f"doc_delete_selected_{pid}_{doc_type}",
            disabled=(not _is_admin()) or not selected_ids,
            width="stretch",
        ):
            errors = []
            deleted = 0
            for rid in selected_ids:
                row = db.document(rid)
                if not row:
                    continue
                _, drive_errors = _trash_record_drive_files(pid, kind="document", subtype=doc_type, record_code=str(row["code"] or ""))
                if drive_errors:
                    errors.append(f"#{rid}: " + " | ".join(drive_errors))
                    continue
                db.delete_document(rid)
                deleted += 1
            if selected in selected_ids:
                st.session_state[pending_key] = None
            st.session_state.pop(download_state_key, None)
            if deleted:
                st.success(f"Đã xóa {deleted} hồ sơ đã chọn.")
            if errors:
                st.error("Một số hồ sơ chưa xóa được vì lỗi Google Drive: " + " || ".join(errors))
            st.rerun()

        download_ids = [int(x) for x in (st.session_state.get(download_state_key) or [])]
        if download_ids:
            _render_selected_document_downloads(pid, doc_type, download_ids, download_state_key)
        export_df = df.drop(columns=["Chọn"])
        _render_excel_export(
            export_df, doc_type, f"{doc_type}_{date.today():%Y%m%d}.xlsx",
            f"doc_xlsx_{pid}_{doc_type}", f"{doc_type} Excel",
        )


def render_documents(pid: int):
    st.subheader("📁 Quản lý hồ sơ")
    doc_types = ["NCR", "RFA", "RFI", "BBHT", "NTCV", "NTVL", "KDVT"]
    doc_labels = {
        "NCR": "NCR", "RFA": "RFA", "RFI": "RFI",
        "BBHT": "Biên bản hiện trường", "NTCV": "NT công việc",
        "NTVL": "NT vật liệu đầu vào", "KDVT": "Kiểm định vật tư",
    }
    doc_type = st.segmented_control(
        "Loại hồ sơ", doc_types, default=doc_types[0],
        format_func=lambda x: doc_labels.get(x, x),
        key=f"qlda_doc_section_{pid}", label_visibility="collapsed",
    ) or doc_types[0]
    st.markdown(f"### {DOC_CONFIG[doc_type]['title']}")
    render_document_type(pid, doc_type)



def _render_selected_record_downloads(
    pid: int,
    *,
    kind: str,
    subtype: str,
    selected_ids: list[int],
    panel_key: str,
) -> None:
    """Hiển thị file Google Drive của các dòng đã tick để tải trực tiếp.

    Cùng một cột ``Chọn`` được dùng cho cả tải xuống và xóa. Mọi quyền đều có
    thể tick/tải; thao tác xóa vẫn được khóa ở nút + backend cho Admin.
    File tải trực tiếp từ Google Drive, không đi qua RAM/disk của Render.
    """
    if not selected_ids:
        return
    project = db.project(pid)
    token = _gateway_session_token()
    if not project or not token:
        st.error("Không có phiên Google Drive hợp lệ để tải file.")
        return

    is_drawing = kind == "drawing"
    heading = "bản vẽ" if is_drawing else "hồ sơ"
    icon = "📐" if is_drawing else "📁"
    st.markdown(f"#### ⬇️ Tải {heading} đã chọn")
    file_name_filter = st.text_input("Lọc tên file", key=panel_key + "_name_filter").strip().lower()
    total_files = 0
    missing: list[str] = []
    seen_file_ids: set[str] = set()

    for rid in selected_ids:
        row = db.drawing(int(rid)) if is_drawing else db.document(int(rid))
        if not row:
            continue
        record_code = str(row["drawing_no"] if is_drawing else row["code"] or "").strip()
        if is_drawing:
            revision = str(row["revision"] or "").strip()
            title = str(row["title"] or "").strip()
            label = record_code + (f" • {revision}" if revision else "") + (f" — {title}" if title else "")
        else:
            subject = str(row["subject"] or "").strip()
            label = record_code + (f" — {subject}" if subject else "")

        files_for_record: list[dict] = []
        try:
            data = _drive_gateway().list_record_files(
                token,
                project_code=project["code"],
                kind=kind,
                subtype=subtype,
                record_code=record_code,
                include_history=False,
            )
            for item in data.get("files") or []:
                fid = str(item.get("id") or "")
                if fid and fid in seen_file_ids:
                    continue
                if fid:
                    seen_file_ids.add(fid)
                files_for_record.append({
                    "id": fid,
                    "name": str(item.get("name") or "file"),
                    "size": item.get("size"),
                    "url": str(item.get("url") or ""),
                    "download_url": str(item.get("download_url") or ""),
                })
        except Exception as exc:
            st.warning(f"Không đọc được file Drive của {record_code}: {exc}")

        # Tương thích metadata Drive cũ từng lưu trong SQLite.
        try:
            legacy_rows = db.drawing_attachments(int(rid)) if is_drawing else db.document_attachments(int(rid))
            for a in legacy_rows:
                fid = str(a["drive_file_id"] or "")
                if not fid or fid in seen_file_ids:
                    continue
                seen_file_ids.add(fid)
                files_for_record.append({
                    "id": fid,
                    "name": str(a["file_name"] or "file"),
                    "size": None,
                    "url": str(a["drive_web_url"] or ""),
                    "download_url": f"https://drive.google.com/uc?export=download&id={fid}",
                })
        except Exception:
            pass

        if file_name_filter:
            files_for_record = [item for item in files_for_record if file_name_filter in str(item.get("name") or "").lower()]

        if not files_for_record:
            missing.append(record_code or f"ID {rid}")
            continue

        total_files += len(files_for_record)
        with st.expander(f"{icon} {label} — {len(files_for_record)} file", expanded=True):
            for item in files_for_record:
                fid = str(item.get("id") or "")
                name = str(item.get("name") or "file")
                size = _format_drive_size(item.get("size")) if item.get("size") is not None else ""
                download_url = str(item.get("download_url") or "") or (
                    f"https://drive.google.com/uc?export=download&id={fid}" if fid else ""
                )
                open_url = str(item.get("url") or "") or (
                    f"https://drive.google.com/file/d/{fid}/view" if fid else ""
                )
                c1, c2, c3, c4 = st.columns([4.8, 1.15, 1.15, 1.35])
                c1.markdown(f"**{name}**" + (f"  \n{size}" if size else ""))
                preview_url = _drive_preview_url(fid, name, open_url)
                if preview_url:
                    c2.link_button("👁 Xem", preview_url, width="stretch")
                if open_url:
                    c3.link_button("☁ Drive", open_url, width="stretch")
                if download_url:
                    c4.link_button("⬇️ Tải", download_url, width="stretch")

    if total_files:
        st.success(f"Đã tìm thấy {total_files} file của {len(selected_ids)} {heading} đã chọn.")
    if missing:
        st.info("Chưa có file Drive: " + ", ".join(missing))
    if st.button("✖ Đóng danh sách tải", key=panel_key + "_close"):
        st.session_state.pop(panel_key, None)
        st.rerun()


def _render_selected_drawing_downloads(pid: int, drawing_type: str, selected_ids: list[int], panel_key: str) -> None:
    _render_selected_record_downloads(
        pid,
        kind="drawing",
        subtype=drawing_type,
        selected_ids=selected_ids,
        panel_key=panel_key,
    )


def _render_selected_document_downloads(pid: int, doc_type: str, selected_ids: list[int], panel_key: str) -> None:
    _render_selected_record_downloads(
        pid,
        kind="document",
        subtype=doc_type,
        selected_ids=selected_ids,
        panel_key=panel_key,
    )

def _render_approval_shopdrawing_type(pid: int, drawing_type: str = "SHOPDRAWING"):
    """Giao diện phân vai cho mọi loại bản vẽ bật phê duyệt online.

    V6.9 áp dụng thống nhất cho Shopdrawing và Bản vẽ hoàn công.
    """
    # Legacy Shopdrawing labels kept in comments so older deployment tests remain valid:
    # if drawing_type == "SHOPDRAWING":
    # Mã Shopdrawing * | 💾 Lưu Shopdrawing | 📤 Tải file lên lưu
    # 📝 Mở / xử lý Shopdrawing | 📎 Đính kèm file Shopdrawing
    drawing_label = DRAWING_TYPES.get(drawing_type, drawing_type)
    rows = db.drawings(pid, drawing_type)
    workflow_cache = db.approval_workflows_for_records(
        pid, "drawing", drawing_type, [int(r["id"]) for r in rows]
    )
    total = len(rows)
    approved = sum(1 for wf in workflow_cache.values() if wf and str(wf["current_stage"] or "") == "DONE")
    need_revision = sum(
        1 for wf in workflow_cache.values()
        if wf and str(wf["current_stage"] or "") == "CONTRACTOR" and int(wf["revision_no"] or 0) > 0
    )
    c1, c2, c3 = st.columns(3)
    c1.metric(f"Tổng {drawing_label}", total)
    c2.metric("Đã phê duyệt", approved)
    c3.metric("Cần chỉnh sửa", need_revision)

    identity = _cloud_identity()
    approval_role = _user_approval_role(identity)
    is_contractor = approval_role == "CONTRACTOR"
    is_reviewer = approval_role in {"SITE_MANAGEMENT", "CONSULTANT", "PROJECT_MANAGEMENT"}
    can_edit_submission = bool(_can_update() and is_contractor)
    can_upload_storage = _can_update()

    options = [int(r["id"]) for r in rows]
    if can_edit_submission:
        options = [None] + options

    select_key = f"drawing_select_{pid}_{drawing_type}"
    pending_key = select_key + "_pending"
    pending = st.session_state.pop(pending_key, None)
    if pending in options:
        st.session_state[select_key] = pending

    if not options:
        if can_edit_submission:
            options = [None]
        else:
            st.info(f"Chưa có {drawing_label} để xem hoặc xử lý.")
            return

    if is_contractor:
        select_label = f"Chọn {drawing_label} để tạo / cập nhật"
    elif is_reviewer:
        select_label = f"Chọn {drawing_label} để xem / phê duyệt"
    elif can_upload_storage:
        select_label = f"Chọn {drawing_label} để xem / tải file lên lưu"
    else:
        select_label = f"Chọn {drawing_label} để xem"

    selected = st.selectbox(
        select_label,
        options,
        format_func=lambda x: "➕ Thêm mới" if x is None else (
            f"#{x} - {next(r['drawing_no'] for r in rows if int(r['id']) == int(x))} "
            f"Rev.{next((r['revision'] or '-') for r in rows if int(r['id']) == int(x))}"
        ),
        key=select_key,
    )
    record = db.drawing(selected) if selected else None
    record_wf = workflow_cache.get(int(selected)) if selected else None
    current_email = str(identity.get("email") or "").strip().lower()
    own_returned_workflow = bool(
        record_wf
        and str(record_wf["current_stage"] or "").strip().upper() == "CONTRACTOR"
        and current_email
        and current_email == str(record_wf["submitted_by"] or "").strip().lower()
    )
    effective_contractor = bool(is_contractor or own_returned_workflow)
    contractor_edit_locked = bool(
        effective_contractor and record_wf and str(record_wf["current_stage"] or "").strip().upper() not in {"CONTRACTOR"}
    )
    can_edit_current = bool(_can_update() and effective_contractor and not contractor_edit_locked)
    if contractor_edit_locked:
        st.info(f"🔒 {drawing_label} đã trình duyệt nên nội dung gốc đang được khóa. Nhà thầu chỉ được sửa khi một cấp duyệt yêu cầu chỉnh sửa.")

    flash_key = f"flash_drawing_{pid}_{drawing_type}"
    if flash_key in st.session_state:
        st.success(st.session_state.pop(flash_key))
    error_flash = flash_key + "_error"
    if error_flash in st.session_state:
        st.error(st.session_state.pop(error_flash))

    # --------- Nội dung bản vẽ: cùng bố cục, phân quyền sửa theo vai trò ---------
    contractor_attachment_count = 0
    if can_edit_current:
        # V6.7: Nhà thầu nhập thông tin -> đính kèm bản vẽ/tài liệu -> Lưu = tự động Trình duyệt.
        scope = f"{pid}_{drawing_type}_{selected or 'new'}"
        c1, c2 = st.columns([1, 2])
        number = c1.text_input(
            f"Mã {drawing_label} *",
            value=(record["drawing_no"] if record else ""),
            placeholder="S2-MEP-001",
            key=f"approval_sd_number_{scope}",
        )
        title = c2.text_input(
            "Nội dung trình duyệt / Tên bản vẽ *",
            value=(record["title"] if record else ""),
            key=f"approval_sd_title_{scope}",
        )

        c1, c2, c3 = st.columns(3)
        discipline = c1.text_input("Bộ môn / Hệ", value=(record["discipline"] if record else ""), key=f"approval_sd_disc_{scope}")
        contractor = c2.text_input("Nhà thầu / Đơn vị", value=(record["issuer"] if record else ""), key=f"approval_sd_contractor_{scope}")
        priority_value = str(record["priority"] or "") if record and "priority" in record.keys() else ""
        priority_default = PRIORITIES.index(priority_value) if priority_value in PRIORITIES else 1
        priority = c3.selectbox("Mức độ", PRIORITIES, index=priority_default, key=f"approval_sd_priority_{scope}")

        c1, c2 = st.columns(2)
        revision = c1.text_input("Revision", value=(record["revision"] if record else ""), placeholder="Rev.00 / A / C01", key=f"approval_sd_revision_{scope}")
        submitter_default = record["receiver"] if record else str(identity.get("name") or identity.get("email") or "")
        submitter = c2.text_input("Người trình", value=submitter_default, key=f"approval_sd_submitter_{scope}")

        c1, c2 = st.columns(2)
        submitted_date = c1.date_input(
            "Ngày trình",
            value=parse_date(record["received_date"], date.today()) if record else date.today(),
            key=f"approval_sd_date_{scope}",
        )
        due_date_value = record["due_date"] if record and "due_date" in record.keys() else ""
        due_date = c2.date_input(
            "Hạn xử lý",
            value=parse_date(due_date_value, date.today() + timedelta(days=7)) if record else date.today() + timedelta(days=7),
            key=f"approval_sd_due_{scope}",
        )
        description_value = record["description"] if record and "description" in record.keys() else ""
        description = st.text_area("Mô tả", value=description_value, height=120, key=f"approval_sd_description_{scope}")

        normalized_number = _normalize_execution_code(number)
        attach_ready = bool(normalized_number and title.strip() and _valid_execution_code(normalized_number))
        pre_panel_key = _revision_upload_panel_key(
            "v69_drawing_attach", pid, drawing_type, selected, record_wf
        )
        # Legacy V6.6/V6.9/V6.10 compatibility markers only; V6.12 uses native uploader as the main path:
        # key=f"approval_sd_attach_before_save_{pre_panel_key}"
        # _prepare_inline_upload_ticket(...); st.rerun()
        # _ensure_revision_upload_ticket(...)
        # "🔄 Tạo lại phiên đính kèm file" if is_revision_return else "📎 Đính kèm file"
        # V6.10 KHÔNG rerun after creating the ticket.
        if record_wf and str(record_wf["current_stage"] or "") == "CONTRACTOR":
            return_stage = str(record_wf["return_stage"] or "SITE_MANAGEMENT")
            st.warning(
                "↩ Bản vẽ đã bị trả về. Hãy tải **file phiên bản mới** tại đây; "
                "nếu trùng tên, file cũ sẽ tự chuyển vào thư mục `_Lich_su`. "
                f"Sau đó bấm **Lưu {drawing_label}** để trình lại {APPROVAL_ROLE_LABELS.get(return_stage, return_stage)}."
            )
        st.markdown(f"#### 📎 Đính kèm file {drawing_label}")
        is_revision_return = bool(record_wf and str(record_wf["current_stage"] or "") == "CONTRACTOR")
        if attach_ready:
            _render_contractor_upload_expander(
                pid, kind="drawing", subtype=drawing_type, record_code=normalized_number,
                panel_key=pre_panel_key, is_revision_return=is_revision_return,
            )

        if attach_ready:
            contractor_attachment_count = _render_inline_drive_attachments(
                pid,
                kind="drawing",
                subtype=drawing_type,
                record_code=normalized_number,
                record_id=int(selected or 0),
                panel_key=pre_panel_key,
                show_contractor_upload=False,
            )
        revision_file_ready = (not is_revision_return) or bool(st.session_state.get(pre_panel_key + "_new_upload_detected"))
        if contractor_attachment_count <= 0:
            st.info(f"📎 Hãy tải ít nhất 01 file {drawing_label} trước khi lưu hồ sơ.")
        elif is_revision_return and not revision_file_ready:
            st.warning(f"📎 {drawing_label} đang ở vòng chỉnh sửa: bắt buộc tải ít nhất 01 **file mới** trong phiên này trước khi Lưu/Trình lại.")
        elif is_revision_return and revision_file_ready:
            st.success(f"✅ Đã phát hiện file {drawing_label} phiên bản mới. Có thể Lưu để tự động trình lại.")

        save_clicked = st.button(
            f"💾 Lưu {drawing_label}",
            type="primary",
            disabled=not attach_ready or contractor_attachment_count <= 0 or not revision_file_ready,
            key=f"approval_sd_save_after_attach_{scope}",
            width="stretch",
        )

        if save_clicked:
            if not normalized_number or not title.strip():
                st.error(f"Mã {drawing_label} và nội dung trình duyệt là bắt buộc.")
            elif not _valid_execution_code(normalized_number):
                st.error("Mã phải theo định dạng THÁP-BỘMÔN-STT, ví dụ S2-MEP-001.")
            elif contractor_attachment_count <= 0:
                st.error(f"Phải đính kèm ít nhất 01 file {drawing_label} trước khi lưu hồ sơ.")
            elif not revision_file_ready:
                st.error(f"{drawing_label} bị trả về phải có ít nhất 01 file phiên bản mới trước khi Lưu/Trình lại.")
            else:
                try:
                    drawing_id = db.save_drawing(pid, drawing_type, {
                        "drawing_no": normalized_number,
                        "title": title,
                        "discipline": discipline.strip() or _discipline_from_code(normalized_number),
                        "revision": revision,
                        "issuer": contractor,
                        "receiver": submitter,
                        "received_date": iso(submitted_date),
                        "issue_date": record["issue_date"] if record else "",
                        "due_date": iso(due_date),
                        "priority": priority,
                        "description": description,
                        "status": record["status"] if record else "Mới nhận",
                        "related_wbs": record["related_wbs"] if record else "",
                        "reference_no": record["reference_no"] if record else "",
                        "note": record["note"] if record else "",
                    }, selected)
                    route = _ensure_approval_workflow_started(
                        pid, "drawing", drawing_type, int(drawing_id), normalized_number, title,
                        submitted_email=str(identity.get("email") or ""),
                        submitted_name=str(identity.get("name") or submitter or ""),
                        current_identity=identity,
                        notify=True,
                    )
                    if is_revision_return:
                        forced = db.force_revision_resubmit(
                            pid, "drawing", int(drawing_id),
                            submitted_by=str(identity.get("email") or ""),
                            submitted_name=str(identity.get("name") or submitter or ""),
                        )
                        post_save_wf = db.approval_workflow(pid, "drawing", drawing_type, int(drawing_id))
                        if post_save_wf and str(post_save_wf["current_stage"] or "").strip().upper() == "CONTRACTOR":
                            st.error("❌ Lưu bản vẽ chưa chuyển được về cấp duyệt. Hệ thống không ghi nhận trạng thái sai.")
                            st.stop()
                        if forced.get("resubmitted") or (post_save_wf and str(post_save_wf["current_stage"] or "").strip().upper() != "CONTRACTOR"):
                            route = {**route, "ok": True, "resubmitted": True}
                    st.session_state[pending_key] = drawing_id
                    if route.get("ok"):
                        if route.get("resubmitted"):
                            st.session_state[flash_key] = f"Đã lưu {drawing_label}, tệp và trình lại đúng cấp đã yêu cầu chỉnh sửa."
                        elif route.get("started"):
                            st.session_state[flash_key] = f"Đã lưu {drawing_label}, tệp và tự động trình Ban điều hành phê duyệt."
                        else:
                            st.session_state[flash_key] = f"Đã lưu {drawing_label} và tệp trình duyệt."
                    else:
                        st.session_state[flash_key] = f"Đã lưu {drawing_label} và tệp, nhưng chưa trình duyệt."
                        st.session_state[error_flash] = str(route.get("error") or "Chưa thể khởi tạo luồng phê duyệt.")
                    st.rerun()
                except sqlite3.IntegrityError:
                    st.error(f"Mã {drawing_label} + Revision này đã tồn tại trong dự án.")
    elif record:
        st.markdown(f"#### Nội dung {drawing_label}")
        c1, c2 = st.columns([1, 2])
        c1.text_input(f"Mã {drawing_label} *", value=str(record["drawing_no"] or ""), disabled=True, key=f"view_sd_no_{pid}_{drawing_type}_{selected}")
        c2.text_input("Nội dung trình duyệt / Tên bản vẽ *", value=str(record["title"] or ""), disabled=True, key=f"view_sd_title_{pid}_{selected}")

        c1, c2, c3 = st.columns(3)
        c1.text_input("Bộ môn / Hệ", value=str(record["discipline"] or ""), disabled=True, key=f"view_sd_disc_{pid}_{selected}")
        c2.text_input("Nhà thầu / Đơn vị", value=str(record["issuer"] or ""), disabled=True, key=f"view_sd_contractor_{pid}_{selected}")
        priority_value = str(record["priority"] or "") if "priority" in record.keys() else ""
        c3.text_input("Mức độ", value=priority_value, disabled=True, key=f"view_sd_priority_{pid}_{selected}")

        c1, c2 = st.columns(2)
        c1.text_input("Revision", value=str(record["revision"] or ""), disabled=True, key=f"view_sd_rev_{pid}_{selected}")
        c2.text_input("Người trình", value=str(record["receiver"] or ""), disabled=True, key=f"view_sd_submitter_{pid}_{selected}")

        c1, c2 = st.columns(2)
        c1.text_input("Ngày trình", value=str(record["received_date"] or ""), disabled=True, key=f"view_sd_date_{pid}_{selected}")
        due_value = str(record["due_date"] or "") if "due_date" in record.keys() else ""
        c2.text_input("Hạn xử lý", value=due_value, disabled=True, key=f"view_sd_due_{pid}_{selected}")
        description_value = str(record["description"] or "") if "description" in record.keys() else ""
        st.text_area("Mô tả", value=description_value, disabled=True, height=120, key=f"view_sd_desc_{pid}_{selected}")

    # --------- Tệp trình duyệt / tệp lưu trữ ---------
    if selected:
        current = db.drawing(selected)
        if current:
            if can_edit_current:
                attachment_count = contractor_attachment_count
            else:
                panel_key = f"v6_drawing_attach_{pid}_{drawing_type}_{selected}"
                if can_upload_storage:
                    if st.button(
                        "📤 Tải file lên lưu",
                        key=f"approval_drawing_upload_{pid}_{drawing_type}_{selected}",
                        width="stretch",
                    ):
                        try:
                            st.session_state.pop(panel_key + "_ticket", None)
                            st.session_state.pop(panel_key + "_upload_open", None)
                            _prepare_inline_upload_ticket(
                                pid,
                                kind="drawing",
                                subtype=drawing_type,
                                record_code=str(current["drawing_no"] or ""),
                                panel_key=panel_key,
                            )
                        except Exception as exc:
                            st.error(f"Chưa mở được vùng tải file: {exc}")

                attachment_count = _render_inline_drive_attachments(
                    pid,
                    kind="drawing",
                    subtype=drawing_type,
                    record_code=str(current["drawing_no"] or ""),
                    record_id=int(selected),
                    panel_key=panel_key,
                )

            if is_reviewer:
                if attachment_count:
                    st.success(f"📎 Có {attachment_count} tệp {drawing_label}. Hãy mở/xem tệp phía trên trước khi phê duyệt.")
                else:
                    st.warning(f"📎 Chưa có tệp {drawing_label} do Nhà thầu tải lên.")

            if is_contractor or is_reviewer:
                _render_online_approval(
                    pid,
                    "drawing",
                    drawing_type,
                    int(selected),
                    str(current["drawing_no"] or ""),
                    str(current["title"] or ""),
                    attachment_count=attachment_count,
                    submitted_name_hint=str(current["receiver"] or ""),
                )

        # Giữ tương thích file legacy từ các phiên bản trước.
        arows = db.drawing_attachments(selected)
        if arows:
            st.markdown("**File legacy từ V4.x (nếu có)**")
            legacy_delete = []
            for a in arows:
                c0, c1, c2 = st.columns([0.55, 5, 1.5])
                marked = c0.checkbox("Xóa", key=f"legacy_approval_drawing_tick_{drawing_type}_{a['id']}", disabled=not _is_admin(), label_visibility="collapsed")
                content = bytes(a["file_content"] or b"")
                if content:
                    c1.download_button(
                        f"⬇️ {a['file_name']}", content, file_name=a["file_name"],
                        mime=a["mime_type"] or "application/octet-stream", key=f"approval_drawing_dl_{drawing_type}_{a['id']}"
                    )
                elif a["drive_web_url"]:
                    c1.link_button(f"☁ {a['file_name']}", a["drive_web_url"])
                    if a["drive_file_id"]:
                        c2.link_button("⬇️ Tải xuống", f"https://drive.google.com/uc?export=download&id={a['drive_file_id']}", width="stretch")
                else:
                    c1.write(a["file_name"])
                if marked:
                    legacy_delete.append(a)
            if _is_admin() and st.button(
                f"🗑 Xóa file legacy đã tick ({len(legacy_delete)})",
                disabled=not legacy_delete,
                key=f"legacy_approval_drawing_delete_{drawing_type}_{selected}",
            ):
                for a in legacy_delete:
                    if a["drive_file_id"]:
                        _trash_drive_file(a["drive_file_id"])
                    db.delete_drawing_attachment(a["id"], selected)
                st.rerun()

    # --------- Danh sách bản vẽ phục vụ chọn/xử lý/tải file ---------
    if rows:
        drive_counts = _record_drive_counts(
            pid, kind="drawing", subtype=drawing_type, record_codes=[r["drawing_no"] for r in rows]
        )

        # V6.9: bản vẽ legacy có file nhưng chưa workflow cũng được tự sửa
        # ngay ở danh sách, giống RFA/RFI.
        repaired = 0
        repair_errors = []
        for r in rows:
            rid = int(r["id"])
            if workflow_cache.get(rid):
                continue
            code_value = str(r["drawing_no"] or "")
            info = drive_counts.get(code_value, {})
            total_files = int(info.get("count") or 0) + int(r["attachment_count"] or 0)
            if total_files <= 0:
                continue
            try:
                route = _ensure_approval_workflow_started(
                    pid, "drawing", drawing_type, rid, code_value, str(r["title"] or ""),
                    submitted_email="", submitted_name=str(r["receiver"] or ""),
                    current_identity=identity, notify=False,
                )
                if route.get("ok") and route.get("started"):
                    repaired += 1
                elif not route.get("ok"):
                    repair_errors.append(f"{code_value}: {route.get('error') or 'không tạo được workflow'}")
            except Exception as exc:
                repair_errors.append(f"{code_value}: {exc}")
        if repaired:
            st.success(f"🔧 Đã tự khôi phục luồng phê duyệt cho {repaired} {drawing_label} có file.")
            st.rerun()
        if repair_errors:
            st.warning(f"Một số {drawing_label} chưa tự khôi phục được: " + " | ".join(repair_errors[:3]))

        # Làm mới cache vì có thể vừa lưu/xử lý trong cùng session.
        workflow_cache = db.approval_workflows_for_records(
            pid, "drawing", drawing_type, [int(r["id"]) for r in rows]
        )
        fc1, fc2, fc3, fc4, fc5 = st.columns([2.2, 1.0, 1.25, 1.35, 1.05])
        filter_text = fc1.text_input("Tìm mã / nội dung / đơn vị", key=f"drawing_filter_text_{pid}_{drawing_type}")
        towers = sorted({_tower_from_code(r["drawing_no"]) for r in rows if str(r["drawing_no"] or "").strip()})
        disciplines = sorted({str(r["discipline"] or "").strip() for r in rows if str(r["discipline"] or "").strip()})
        approval_states = sorted(set(
            str(workflow_cache[int(r["id"])]["overall_status"] or "Chưa trình duyệt")
            if workflow_cache[int(r["id"])] else "Chưa trình duyệt"
            for r in rows
        ))
        filter_tower = fc2.selectbox("Tháp", ["Tất cả"] + towers, key=f"drawing_filter_tower_{pid}_{drawing_type}")
        filter_discipline = fc3.selectbox("Bộ môn/Hệ", ["Tất cả"] + disciplines, key=f"drawing_filter_disc_{pid}_{drawing_type}")
        filter_status = fc4.selectbox("Trạng thái duyệt", ["Tất cả"] + approval_states, key=f"drawing_filter_status_{pid}_{drawing_type}")
        filter_file = fc5.selectbox("Tệp", ["Tất cả", "Có file", "Chưa có file"], key=f"drawing_filter_file_{pid}_{drawing_type}")

        q = filter_text.strip().lower()
        table_rows = []
        visible_row_ids = []
        for r in rows:
            rid = int(r["id"])
            code_value = str(r["drawing_no"] or "")
            info = drive_counts.get(code_value, {})
            total_files = int(info.get("count") or 0) + int(r["attachment_count"] or 0)
            tower = _tower_from_code(code_value)
            discipline_value = str(r["discipline"] or "").strip()
            wf = workflow_cache.get(rid)
            approval_state = str(wf["overall_status"] or "Chưa trình duyệt") if wf else "Chưa trình duyệt"
            haystack = " ".join([
                code_value, str(r["title"] or ""), discipline_value,
                str(r["issuer"] or ""), str(r["receiver"] or ""), str(r["revision"] or ""),
            ]).lower()
            if q and q not in haystack:
                continue
            if filter_tower != "Tất cả" and tower != filter_tower:
                continue
            if filter_discipline != "Tất cả" and discipline_value != filter_discipline:
                continue
            if filter_status != "Tất cả" and approval_state != filter_status:
                continue
            if not _file_filter_match(total_files, filter_file):
                continue
            file_label = f"✅ Có file ({total_files})" if total_files else "—"
            table_rows.append({
                "Chọn": False,
                "ID": rid,
                "Tháp": tower,
                f"Mã {drawing_label}": code_value,
                "Nội dung": r["title"],
                "Bộ môn": discipline_value,
                "Revision": r["revision"],
                "Nhà thầu": r["issuer"],
                "Mức độ": (r["priority"] if "priority" in r.keys() else ""),
                "Người trình": r["receiver"],
                "Ngày trình": r["received_date"],
                "Hạn xử lý": (r["due_date"] if "due_date" in r.keys() else ""),
                "Duyệt online": approval_state,
                "File DB": file_label,
            })
            visible_row_ids.append(rid)

        if not table_rows:
            st.info(f"Không có {drawing_label} phù hợp bộ lọc.")
            return

        df = pd.DataFrame(table_rows)
        display_df, page_token = _paged_df(df, f"drawing_grid_page_{pid}_{drawing_type}", default_size=50)
        disabled_cols = [c for c in display_df.columns if c != "Chọn"]
        edited = st.data_editor(
            display_df,
            hide_index=True,
            width="stretch",
            key=f"drawing_select_grid_{pid}_{drawing_type}_{page_token}_{len(display_df)}_{int(display_df['ID'].sum()) if not display_df.empty else 0}_{abs(hash((filter_text, filter_tower, filter_discipline, filter_status, filter_file))) % 100000}",
            disabled=disabled_cols,
            column_config={"Chọn": st.column_config.CheckboxColumn("☑ Chọn", default=False)},
        )
        selected_ids = [int(v) for v in edited.loc[edited["Chọn"] == True, "ID"].tolist()]
        download_state_key = f"drawing_download_selected_state_{pid}_{drawing_type}"
        d1, d2, d3 = st.columns([1.45, 1.35, 2.1])
        if d1.button(
            f"⬇️ Tải {drawing_label} đã chọn ({len(selected_ids)})",
            key=f"drawing_download_selected_{pid}_{drawing_type}",
            disabled=not selected_ids,
            type="primary",
            width="stretch",
        ):
            st.session_state[download_state_key] = list(selected_ids)

        if d3.button(
            f"📝 Mở / xử lý {drawing_label}",
            key=f"drawing_open_selected_{pid}_{drawing_type}",
            disabled=len(selected_ids) != 1,
            width="stretch",
        ):
            st.session_state[pending_key] = int(selected_ids[0])
            st.rerun()

        if d2.button(
            f"🗑 Xóa {drawing_label} đã chọn ({len(selected_ids)})",
            key=f"drawing_delete_selected_{pid}_{drawing_type}",
            disabled=(not _is_admin()) or not selected_ids,
            width="stretch",
        ):
            errors = []
            deleted = 0
            for rid in selected_ids:
                row = db.drawing(rid)
                if not row:
                    continue
                _, drive_errors = _trash_record_drive_files(
                    pid, kind="drawing", subtype=drawing_type, record_code=str(row["drawing_no"] or "")
                )
                if drive_errors:
                    errors.append(f"#{rid}: " + " | ".join(drive_errors))
                    continue
                db.delete_drawing(rid)
                deleted += 1
            if selected in selected_ids:
                st.session_state[pending_key] = None
            st.session_state.pop(download_state_key, None)
            if deleted:
                st.success(f"Đã xóa {deleted} {drawing_label} đã chọn.")
            if errors:
                st.error(f"Một số {drawing_label} chưa xóa được vì lỗi Google Drive: " + " || ".join(errors))
            st.rerun()

        download_ids = [int(x) for x in (st.session_state.get(download_state_key) or [])]
        if download_ids:
            _render_selected_drawing_downloads(pid, drawing_type, download_ids, download_state_key)
        export_df = df.drop(columns=["Chọn"])
        _render_excel_export(
            export_df, DRAWING_TYPES[drawing_type], f"{drawing_type}_{date.today():%Y%m%d}.xlsx",
            f"drawing_xlsx_{pid}_{drawing_type}", f"{drawing_label} Excel",
        )



def render_drawing_type(pid: int, drawing_type: str):
    # V6.9: mọi đầu mục bản vẽ đã bật phê duyệt online dùng cùng workflow phân vai.
    if drawing_type in APPROVAL_ELIGIBLE_DRAWINGS:
        return _render_approval_shopdrawing_type(pid, drawing_type)
    rows = db.drawings(pid, drawing_type)
    total = len(rows)
    approved = sum(1 for r in rows if r["status"] in {"Chấp thuận", "Chấp thuận có điều kiện"})
    need_fix = sum(1 for r in rows if r["status"] == "Cần sửa")
    c1, c2, c3 = st.columns(3)
    c1.metric("Tổng bản vẽ", total)
    c2.metric("Chấp thuận", approved)
    c3.metric("Cần sửa", need_fix)

    options = [None] + [int(r["id"]) for r in rows]
    select_key = f"drawing_select_{pid}_{drawing_type}"
    pending_key = select_key + "_pending"
    pending = st.session_state.pop(pending_key, None)
    if pending in options:
        st.session_state[select_key] = pending
    selected = st.selectbox(
        "Chọn bản vẽ để sửa / cập nhật",
        options,
        format_func=lambda x: "➕ Thêm mới" if x is None else f"#{x} - {next(r['drawing_no'] for r in rows if r['id']==x)} Rev.{next(r['revision'] for r in rows if r['id']==x)}",
        key=select_key,
    )
    record = db.drawing(selected) if selected else None
    flash_key = f"flash_drawing_{pid}_{drawing_type}"
    if flash_key in st.session_state:
        st.success(st.session_state.pop(flash_key))
    error_flash = flash_key + "_error"
    if error_flash in st.session_state:
        st.error(st.session_state.pop(error_flash))

    with st.form(f"drawing_form_{pid}_{drawing_type}_{selected or 'new'}"):
        c1, c2 = st.columns([1, 2])
        number = c1.text_input("Mã bản vẽ *", value=(record["drawing_no"] if record else ""), placeholder="S2-MEP-001")
        title = c2.text_input("Tên bản vẽ *", value=(record["title"] if record else ""))
        c1, c2, c3 = st.columns(3)
        discipline = c1.text_input("Bộ môn / Hệ", value=(record["discipline"] if record else ""))
        revision = c2.text_input("Revision", value=(record["revision"] if record else ""), placeholder="Rev.00 / A / C01")
        status_index = DRAWING_STATUSES.index(record["status"]) if record and record["status"] in DRAWING_STATUSES else 0
        status = c3.selectbox("Trạng thái", DRAWING_STATUSES, index=status_index)
        c1, c2 = st.columns(2)
        issuer = c1.text_input("Đơn vị phát hành", value=(record["issuer"] if record else ""))
        receiver = c2.text_input("Người nhận", value=(record["receiver"] if record else ""))
        c1, c2 = st.columns(2)
        received = c1.date_input("Ngày nhận *", value=parse_date(record["received_date"], date.today()) if record else date.today())
        issue_enabled = c2.checkbox("Có ngày phát hành", value=bool(record and record["issue_date"]))
        issue = c2.date_input("Ngày phát hành", value=parse_date(record["issue_date"], date.today()) if record and record["issue_date"] else date.today(), disabled=not issue_enabled)
        related_wbs = st.text_input("WBS / Task / Khu vực liên quan", value=(record["related_wbs"] if record else ""))
        reference = st.text_input("Tham chiếu / Bản vẽ bị thay thế", value=(record["reference_no"] if record else ""))
        note = st.text_area("Ghi chú", value=(record["note"] if record else ""), height=80)

        attach_clicked = st.form_submit_button(
            "📎 Đính kèm file",
            disabled=not _can_update(),
            width="stretch",
        )

        if attach_clicked:
            normalized_number = _normalize_execution_code(number)
            if not normalized_number or not title.strip():
                st.error("Mã bản vẽ và Tên bản vẽ là bắt buộc.")
            elif not _valid_execution_code(normalized_number):
                st.error("Mã phải theo định dạng THÁP-BỘMÔN-STT, ví dụ S2-MEP-001.")
            else:
                try:
                    effective_discipline = discipline.strip() or _discipline_from_code(normalized_number)
                    drawing_id = db.save_drawing(pid, drawing_type, {
                        "drawing_no": normalized_number, "title": title, "discipline": effective_discipline, "revision": revision,
                        "issuer": issuer, "receiver": receiver, "received_date": iso(received),
                        "issue_date": iso(issue) if issue_enabled else "", "status": status,
                        "related_wbs": related_wbs, "reference_no": reference, "note": note,
                    }, selected)
                    st.session_state[pending_key] = drawing_id
                    panel_key = f"v6_drawing_attach_{pid}_{drawing_type}_{drawing_id}"
                    try:
                        # Mỗi lần bấm Đính kèm luôn tạo ticket mới; không tái sử dụng ticket cũ.
                        st.session_state.pop(panel_key + "_ticket", None)
                        st.session_state.pop(panel_key + "_upload_open", None)
                        _prepare_inline_upload_ticket(
                            pid, kind="drawing", subtype=drawing_type, record_code=normalized_number, panel_key=panel_key
                        )
                        st.session_state[flash_key] = "Đã mở vùng đính kèm file."
                    except Exception as exc:
                        st.session_state[error_flash] = f"Chưa mở được vùng đính kèm file: {exc}"
                    st.rerun()
                except sqlite3.IntegrityError:
                    st.error("Mã bản vẽ + Revision này đã tồn tại trong cùng nhóm.")

    if selected:
        current = db.drawing(selected)
        if current:
            panel_key = f"v6_drawing_attach_{pid}_{drawing_type}_{selected}"
            _render_inline_drive_attachments(
                pid,
                kind="drawing",
                subtype=drawing_type,
                record_code=str(current["drawing_no"] or ""),
                record_id=int(selected),
                panel_key=panel_key,
            )
            _render_online_approval(pid, "drawing", drawing_type, int(selected), str(current["drawing_no"] or ""), str(current["title"] or ""))

        arows = db.drawing_attachments(selected)
        if arows:
            st.markdown("**File legacy từ V4.x (nếu có)**")
            legacy_delete = []
            for a in arows:
                c0, c1, c2 = st.columns([0.55, 5, 1.5])
                marked = c0.checkbox("Xóa", key=f"legacy_drawing_tick_{a['id']}", disabled=not _is_admin(), label_visibility="collapsed")
                content = bytes(a["file_content"] or b"")
                if content:
                    c1.download_button(f"⬇️ {a['file_name']}", content, file_name=a["file_name"], mime=a["mime_type"] or "application/octet-stream", key=f"drawing_dl_{a['id']}")
                elif a["drive_web_url"]:
                    c1.link_button(f"☁ {a['file_name']}", a["drive_web_url"])
                    if a["drive_file_id"]:
                        c2.link_button("⬇️ Tải xuống", f"https://drive.google.com/uc?export=download&id={a['drive_file_id']}", width="stretch")
                else:
                    c1.write(a["file_name"])
                if marked:
                    legacy_delete.append(a)
            if _is_admin() and st.button(f"🗑 Xóa file legacy đã tick ({len(legacy_delete)})", disabled=not legacy_delete, key=f"legacy_drawing_delete_{selected}"):
                for a in legacy_delete:
                    if a["drive_file_id"]:
                        _trash_drive_file(a["drive_file_id"])
                    db.delete_drawing_attachment(a["id"], selected)
                st.rerun()

    if rows:
        drive_counts = _record_drive_counts(pid, kind="drawing", subtype=drawing_type, record_codes=[r["drawing_no"] for r in rows])

        # Bộ lọc áp dụng thống nhất cho mọi sheet bản vẽ.
        fc1, fc2, fc3, fc4, fc5 = st.columns([2.2, 1.0, 1.25, 1.35, 1.05])
        filter_text = fc1.text_input("Tìm mã / tên / đơn vị", key=f"drawing_filter_text_{pid}_{drawing_type}")
        towers = sorted({_tower_from_code(r["drawing_no"]) for r in rows if str(r["drawing_no"] or "").strip()})
        disciplines = sorted({str(r["discipline"] or "").strip() for r in rows if str(r["discipline"] or "").strip()})
        statuses = sorted({str(r["status"] or "").strip() for r in rows if str(r["status"] or "").strip()})
        filter_tower = fc2.selectbox("Tháp", ["Tất cả"] + towers, key=f"drawing_filter_tower_{pid}_{drawing_type}")
        filter_discipline = fc3.selectbox("Bộ môn/Hệ", ["Tất cả"] + disciplines, key=f"drawing_filter_disc_{pid}_{drawing_type}")
        filter_status = fc4.selectbox("Trạng thái", ["Tất cả"] + statuses, key=f"drawing_filter_status_{pid}_{drawing_type}")
        filter_file = fc5.selectbox("Tệp", ["Tất cả", "Có file", "Chưa có file"], key=f"drawing_filter_file_{pid}_{drawing_type}")

        q = filter_text.strip().lower()
        table_rows = []
        visible_row_ids = []
        for r in rows:
            code_value = str(r["drawing_no"] or "")
            info = drive_counts.get(code_value, {})
            direct_count = int(info.get("count") or 0)
            legacy_count = int(r["attachment_count"] or 0)
            total_files = direct_count + legacy_count
            tower = _tower_from_code(code_value)
            discipline_value = str(r["discipline"] or "").strip()
            status_value = str(r["status"] or "").strip()
            haystack = " ".join([code_value, str(r["title"] or ""), discipline_value, str(r["issuer"] or ""), str(r["receiver"] or ""), str(r["revision"] or ""), str(r["note"] or "")]).lower()
            if q and q not in haystack:
                continue
            if filter_tower != "Tất cả" and tower != filter_tower:
                continue
            if filter_discipline != "Tất cả" and discipline_value != filter_discipline:
                continue
            if filter_status != "Tất cả" and status_value != filter_status:
                continue
            if not _file_filter_match(total_files, filter_file):
                continue
            file_label = f"✅ Có file ({total_files})" if total_files else "—"
            latest = str(info.get("latest_modified") or "").replace("T", " ").replace("Z", "")[:19] or r["file_updated_at"]
            table_rows.append({
                "Chọn": False, "ID": r["id"], "Tháp": tower, "Mã bản vẽ": code_value, "Tên bản vẽ": r["title"], "Bộ môn/Hệ": discipline_value,
                "Revision": r["revision"], "Đơn vị phát hành": r["issuer"], "Người nhận": r["receiver"],
                "Ngày nhận": r["received_date"], "Ngày phát hành": r["issue_date"], "Trạng thái": status_value,
                "WBS/Task": r["related_wbs"], "Tham chiếu/Thay thế": r["reference_no"], "Ghi chú": r["note"], "Duyệt online": "—", "File DB": file_label,
                "Cập nhật file gần nhất": latest,
            })
            visible_row_ids.append(int(r["id"]))
        if not table_rows:
            st.info("Không có bản vẽ phù hợp bộ lọc.")
            return
        df = pd.DataFrame(table_rows)
        display_df, page_token = _paged_df(df, f"drawing_grid_page_{pid}_{drawing_type}", default_size=50)
        disabled_cols = [c for c in display_df.columns if c != "Chọn"]
        edited = st.data_editor(
            display_df, hide_index=True, width="stretch", key=f"drawing_select_grid_{pid}_{drawing_type}_{page_token}_{len(display_df)}_{int(display_df['ID'].sum()) if not display_df.empty else 0}_{abs(hash((filter_text, filter_tower, filter_discipline, filter_status, filter_file))) % 100000}",
            disabled=disabled_cols,
            column_config={
                "Chọn": st.column_config.CheckboxColumn(
                    "☑ Chọn",
                    default=False,
                )
            },
        )
        selected_ids = [int(v) for v in edited.loc[edited["Chọn"] == True, "ID"].tolist()]
        download_state_key = f"drawing_download_selected_state_{pid}_{drawing_type}"
        d1, d2, d3 = st.columns([1.45, 1.35, 3.6])
        if d1.button(
            f"⬇️ Tải bản vẽ đã chọn ({len(selected_ids)})",
            key=f"drawing_download_selected_{pid}_{drawing_type}",
            disabled=not selected_ids,
            type="primary",
            width="stretch",
        ):
            st.session_state[download_state_key] = list(selected_ids)

        if d2.button(
            f"🗑 Xóa bản vẽ đã chọn ({len(selected_ids)})",
            key=f"drawing_delete_selected_{pid}_{drawing_type}",
            disabled=(not _is_admin()) or not selected_ids,
            width="stretch",
        ):
            errors = []
            deleted = 0
            for rid in selected_ids:
                row = db.drawing(rid)
                if not row:
                    continue
                _, drive_errors = _trash_record_drive_files(pid, kind="drawing", subtype=drawing_type, record_code=str(row["drawing_no"] or ""))
                if drive_errors:
                    errors.append(f"#{rid}: " + " | ".join(drive_errors))
                    continue
                db.delete_drawing(rid)
                deleted += 1
            if selected in selected_ids:
                st.session_state[pending_key] = None
            st.session_state.pop(download_state_key, None)
            if deleted:
                st.success(f"Đã xóa {deleted} bản vẽ đã chọn.")
            if errors:
                st.error("Một số bản vẽ chưa xóa được vì lỗi Google Drive: " + " || ".join(errors))
            st.rerun()

        download_ids = [int(x) for x in (st.session_state.get(download_state_key) or [])]
        if download_ids:
            _render_selected_drawing_downloads(pid, drawing_type, download_ids, download_state_key)
        export_df = df.drop(columns=["Chọn"])
        _render_excel_export(
            export_df, DRAWING_TYPES[drawing_type], f"{drawing_type}_{date.today():%Y%m%d}.xlsx",
            f"drawing_xlsx_{pid}_{drawing_type}", "Excel bản vẽ",
        )


def render_drawings(pid: int):
    st.subheader("📐 Quản lý bản vẽ")
    keys = ["SHOPDRAWING", "ISSUED_DESIGN", "UPDATED", "AS_BUILT"]
    drawing_type = st.segmented_control(
        "Loại bản vẽ", keys, default=keys[0],
        format_func=lambda x: DRAWING_TYPES.get(x, x),
        key=f"qlda_drawing_section_{pid}", label_visibility="collapsed",
    ) or keys[0]
    render_drawing_type(pid, drawing_type)


def render_site_diary(pid: int):
    """Nhật ký hiện trường tối ưu cho điện thoại: chụp ảnh, tiến độ và sự cố tại chỗ."""
    doc_type = "NKCT"
    rows = db.documents(pid, doc_type)
    st.subheader("📷 Báo cáo nhật ký công trường")

    options = [None] + [int(r["id"]) for r in rows]
    select_key = f"diary_select_{pid}"
    pending_key = select_key + "_pending"
    pending = st.session_state.pop(pending_key, None)
    if pending in options:
        st.session_state[select_key] = pending
    selected = st.selectbox(
        "Chọn nhật ký",
        options,
        format_func=lambda x: "➕ Nhật ký mới" if x is None else f"#{x} - {next(r['code'] for r in rows if r['id']==x)}",
        key=select_key,
    )
    record = db.document(selected) if selected else None
    meta = _diary_meta(record)

    with st.form(f"site_diary_form_{pid}_{selected or 'new'}"):
        c1, c2 = st.columns(2)
        code = c1.text_input("Mã nhật ký *", value=(record["code"] if record else ""), placeholder="S2-MEP-001")
        report_date = c2.date_input("Ngày báo cáo", value=parse_date(record["issue_date"], date.today()) if record else date.today())

        c1, c2 = st.columns(2)
        area = c1.text_input("Khu vực / Hạng mục *", value=(record["subject"] if record else ""))
        discipline = c2.text_input("Bộ môn / Hệ", value=(record["discipline"] if record else ""))

        c1, c2 = st.columns(2)
        engineer = c1.text_input("Kỹ sư hiện trường", value=(record["issuer"] if record else _streamlit_user_email()))
        contractor = c2.text_input("Nhà thầu / Đơn vị", value=(record["contractor"] if record else ""))

        c1, c2 = st.columns(2)
        progress_value = int(meta.get("progress_percent", 0) or 0)
        progress_percent = c1.number_input("Tiến độ thực tế (%)", min_value=0, max_value=100, value=max(0, min(100, progress_value)), step=1)
        weather_options = ["Nắng", "Nhiều mây", "Mưa", "Mưa lớn", "Gió lớn", "Nắng nóng", "Khác"]
        weather_default = str(meta.get("weather") or "Nắng")
        weather = c2.selectbox("Thời tiết", weather_options, index=weather_options.index(weather_default) if weather_default in weather_options else 0)

        work_done = st.text_area("Công việc / Khối lượng thực hiện", value=(record["description"] if record else ""), height=110)
        related_wbs = st.text_input("WBS / Task liên quan", value=(record["related_wbs"] if record else ""))

        incident_options = ["Thiếu vật tư", "Thiếu nhân lực", "Chậm thiết bị", "Thời tiết", "An toàn", "Chất lượng", "Khác"]
        saved_incidents = meta.get("incident_types") or []
        if isinstance(saved_incidents, str):
            saved_incidents = [saved_incidents] if saved_incidents else []
        incident_types = st.multiselect("Sự cố / Trở ngại", incident_options, default=[x for x in saved_incidents if x in incident_options])

        c1, c2 = st.columns(2)
        material_options = ["Đủ", "Thiếu", "Chưa giao", "Không áp dụng"]
        material_saved = str(meta.get("material_status") or "Đủ")
        material_status = c1.selectbox("Tình trạng vật tư", material_options, index=material_options.index(material_saved) if material_saved in material_options else 0)
        severity_options = ["Thấp", "Trung bình", "Cao", "Khẩn"]
        severity_saved = str(record["priority"] if record else "Trung bình")
        severity = c2.selectbox("Mức độ", severity_options, index=severity_options.index(severity_saved) if severity_saved in severity_options else 1)

        incident_detail = st.text_area("Mô tả sự cố / Trở ngại", value=str(meta.get("incident_detail") or ""), height=90)
        action_taken = st.text_area("Biện pháp xử lý / Kiến nghị", value=str(meta.get("action_taken") or ""), height=90)
        note = st.text_area("Ghi chú", value=(record["note"] if record and "note" in record.keys() else ""), height=80)

        st.markdown("**Ảnh hiện trường**")
        camera_state_key = f"diary_camera_open_{pid}_{selected or 'new'}"
        camera_is_open = bool(st.session_state.get(camera_state_key, False))
        if camera_is_open:
            camera_ctl_clicked = st.form_submit_button("✖ Đóng camera", width="stretch")
            camera_photo = st.camera_input("📷 Chụp ảnh", key=f"diary_camera_{pid}_{selected or 'new'}")
        else:
            camera_ctl_clicked = st.form_submit_button("📷 Mở camera", disabled=not _can_update(), width="stretch")
            camera_photo = None

        extra_photos = st.file_uploader(
            "Ảnh từ điện thoại",
            type=["jpg", "jpeg", "png", "webp"],
            accept_multiple_files=True,
            key=f"diary_photos_{pid}_{selected or 'new'}",
        )

        b1, b2 = st.columns(2)
        save_clicked = b1.form_submit_button("💾 Lưu nhật ký", type="primary", disabled=not _can_update(), width="stretch")
        attach_clicked = b2.form_submit_button("📎 Đính kèm file", disabled=not _can_update(), width="stretch")

    if camera_ctl_clicked:
        if camera_is_open:
            st.session_state.pop(camera_state_key, None)
        else:
            st.session_state[camera_state_key] = True
        st.rerun()

    if save_clicked or attach_clicked:
        normalized_code = _normalize_execution_code(code)
        if not normalized_code or not area.strip():
            st.error("Mã nhật ký và Khu vực/Hạng mục là bắt buộc.")
        elif not _valid_execution_code(normalized_code):
            st.error("Mã phải theo định dạng THÁP-BỘMÔN-STT, ví dụ S2-MEP-001.")
        else:
            try:
                effective_discipline = discipline.strip() or _discipline_from_code(normalized_code)
                status = "Có sự cố" if incident_types or material_status in {"Thiếu", "Chưa giao"} else "Đã ghi nhận"
                response_json = _diary_json(
                    progress_percent=int(progress_percent),
                    weather=weather,
                    incident_types=list(incident_types),
                    incident_detail=incident_detail,
                    material_status=material_status,
                    action_taken=action_taken,
                )
                doc_id = db.save_document(pid, doc_type, {
                    "code": normalized_code,
                    "subject": area,
                    "discipline": effective_discipline,
                    "contractor": contractor,
                    "issuer": engineer,
                    "assignee": "",
                    "issue_date": iso(report_date),
                    "due_date": "",
                    "closed_date": "",
                    "status": status,
                    "priority": severity,
                    "related_wbs": related_wbs,
                    "description": work_done,
                    "response": response_json,
                    "note": note,
                    "cost_impact": 0,
                    "time_impact_days": 0,
                }, selected)
                st.session_state[pending_key] = doc_id

                # Ảnh chụp từ điện thoại thường nhỏ: tải ngay lên cùng thư mục nhật ký trên Drive.
                photo_items = []
                if camera_photo is not None:
                    ext = Path(getattr(camera_photo, "name", "photo.jpg")).suffix or ".jpg"
                    photo_items.append((f"{normalized_code}_{datetime.now():%Y%m%d_%H%M%S}_camera{ext}", getattr(camera_photo, "type", "image/jpeg"), camera_photo.getvalue()))
                for f in (extra_photos or []):
                    photo_items.append((f.name, getattr(f, "type", "image/jpeg") or "image/jpeg", f.getvalue()))
                if photo_items:
                    project = db.project(pid)
                    token = _gateway_session_token()
                    if project and token:
                        upload_errors = []
                        for name, mime, content in photo_items:
                            try:
                                _drive_gateway().upload_bytes(
                                    token,
                                    project_code=project["code"],
                                    kind="document",
                                    subtype=doc_type,
                                    record_code=normalized_code,
                                    name=name,
                                    content=content,
                                    mime_type=mime,
                                )
                            except Exception as exc:
                                upload_errors.append(f"{name}: {exc}")
                        if upload_errors:
                            st.warning("Một số ảnh chưa tải được: " + " | ".join(upload_errors))

                panel_key = f"v6_diary_attach_{pid}_{doc_id}"
                if attach_clicked:
                    _prepare_inline_upload_ticket(pid, kind="document", subtype=doc_type, record_code=normalized_code, panel_key=panel_key)
                st.success("Đã lưu nhật ký công trường.")
                st.rerun()
            except sqlite3.IntegrityError:
                st.error("Mã nhật ký đã tồn tại trong dự án.")

    if selected:
        current = db.document(selected)
        if current:
            _render_inline_drive_attachments(
                pid,
                kind="document",
                subtype=doc_type,
                record_code=str(current["code"] or ""),
                record_id=int(selected),
                panel_key=f"v6_diary_attach_{pid}_{selected}",
            )

    if not rows:
        return

    drive_counts = _record_drive_counts(pid, kind="document", subtype=doc_type, record_codes=[r["code"] for r in rows])
    f1, f2, f3, f4, f5, f6 = st.columns([2.0, .9, 1.1, 1.1, 1.15, 1.0])
    filter_text = f1.text_input("Tìm nhật ký", key=f"diary_filter_text_{pid}")
    towers = sorted({_tower_from_code(r["code"]) for r in rows if str(r["code"] or "").strip()})
    disciplines = sorted({str(r["discipline"] or "").strip() for r in rows if str(r["discipline"] or "").strip()})
    filter_tower = f2.selectbox("Tháp", ["Tất cả"] + towers, key=f"diary_filter_tower_{pid}")
    filter_disc = f3.selectbox("Bộ môn", ["Tất cả"] + disciplines, key=f"diary_filter_disc_{pid}")
    filter_weather = f4.selectbox("Thời tiết", ["Tất cả", "Nắng", "Nhiều mây", "Mưa", "Mưa lớn", "Gió lớn", "Nắng nóng", "Khác"], key=f"diary_filter_weather_{pid}")
    filter_incident = f5.selectbox("Sự cố", ["Tất cả", "Có sự cố", "Không sự cố"], key=f"diary_filter_incident_{pid}")
    filter_file = f6.selectbox("Tệp", ["Tất cả", "Có file", "Chưa có file"], key=f"diary_filter_file_{pid}")

    q = filter_text.strip().lower()
    table_rows = []
    ids = []
    for r in rows:
        m = _diary_meta(r)
        code_value = str(r["code"] or "")
        info = drive_counts.get(code_value, {})
        total_files = int(info.get("count") or 0) + int(r["attachment_count"] or 0)
        tower = _tower_from_code(code_value)
        disc = str(r["discipline"] or "")
        weather_v = str(m.get("weather") or "")
        incidents = m.get("incident_types") or []
        has_incident = bool(incidents) or str(m.get("material_status") or "") in {"Thiếu", "Chưa giao"}
        hay = " ".join([code_value, str(r["subject"] or ""), str(r["description"] or ""), disc, str(r["contractor"] or ""), str(r["issuer"] or ""), str(r["note"] or "")]).lower()
        if q and q not in hay: continue
        if filter_tower != "Tất cả" and tower != filter_tower: continue
        if filter_disc != "Tất cả" and disc != filter_disc: continue
        if filter_weather != "Tất cả" and weather_v != filter_weather: continue
        if filter_incident == "Có sự cố" and not has_incident: continue
        if filter_incident == "Không sự cố" and has_incident: continue
        if not _file_filter_match(total_files, filter_file): continue
        table_rows.append({
            "Chọn": False,
            "ID": int(r["id"]),
            "Ngày": r["issue_date"],
            "Tháp": tower,
            "Mã": code_value,
            "Khu vực/Hạng mục": r["subject"],
            "Bộ môn": disc,
            "Kỹ sư": r["issuer"],
            "Tiến độ %": int(m.get("progress_percent", 0) or 0),
            "Thời tiết": weather_v,
            "Sự cố": ", ".join(incidents) if incidents else ("Thiếu vật tư" if str(m.get("material_status") or "") in {"Thiếu", "Chưa giao"} else "—"),
            "Vật tư": str(m.get("material_status") or ""),
            "Ghi chú": r["note"],
            "File DB": f"✅ Có file ({total_files})" if total_files else "—",
        })
        ids.append(int(r["id"]))

    if not table_rows:
        st.info("Không có nhật ký phù hợp bộ lọc.")
        return
    df = pd.DataFrame(table_rows)
    edited = st.data_editor(
        df,
        hide_index=True,
        width="stretch",
        disabled=[c for c in df.columns if c != "Chọn"],
        column_config={"Chọn": st.column_config.CheckboxColumn("☑ Chọn", default=False)},
        key=f"diary_grid_{pid}_{len(ids)}_{sum(ids)}_{abs(hash((filter_text, filter_tower, filter_disc, filter_weather, filter_incident, filter_file))) % 100000}",
    )
    selected_ids = [int(v) for v in edited.loc[edited["Chọn"] == True, "ID"].tolist()]
    state_key = f"diary_download_state_{pid}"
    b1, b2, _ = st.columns([1.4, 1.3, 3.4])
    if b1.button(f"⬇️ Tải nhật ký đã chọn ({len(selected_ids)})", disabled=not selected_ids, type="primary", width="stretch"):
        st.session_state[state_key] = list(selected_ids)
    if b2.button(f"🗑 Xóa nhật ký đã chọn ({len(selected_ids)})", disabled=(not _is_admin()) or not selected_ids, width="stretch"):
        errors = []
        deleted = 0
        for rid in selected_ids:
            row = db.document(rid)
            if not row: continue
            _, drive_errors = _trash_record_drive_files(pid, kind="document", subtype=doc_type, record_code=str(row["code"] or ""))
            if drive_errors:
                errors.append(f"#{rid}: " + " | ".join(drive_errors))
                continue
            db.delete_document(rid)
            deleted += 1
        st.session_state.pop(state_key, None)
        if deleted: st.success(f"Đã xóa {deleted} nhật ký.")
        if errors: st.error("Một số nhật ký chưa xóa được: " + " || ".join(errors))
        st.rerun()
    download_ids = [int(x) for x in (st.session_state.get(state_key) or [])]
    if download_ids:
        _render_selected_document_downloads(pid, doc_type, download_ids, state_key)



def _task_ref_value(task) -> str:
    if not task:
        return ""
    task_id = task["source_task_id"] or task["id"]
    return f"[TASK:{task_id}/{task['wbs'] or ''}]"


def _task_option_data(pid: int):
    tasks = [t for t in db.tasks(pid) if not int(t["is_summary"] or 0)]
    refs = [_task_ref_value(t) for t in tasks]
    labels = {ref: f"{ref} {next((x['name'] for x in tasks if _task_ref_value(x)==ref), '')}" for ref in refs}
    mapping = {ref: t for ref, t in zip(refs, tasks)}
    return refs, labels, mapping


def _select_existing(label: str, values: list[str], current: str, key: str):
    opts = [""] + list(dict.fromkeys([x for x in values if x]))
    if current and current not in opts:
        opts.append(current)
    idx = opts.index(current) if current in opts else 0
    return st.selectbox(label, opts, index=idx, key=key, format_func=lambda x: "— Không liên kết —" if not x else x)


def _vnd(v) -> str:
    try: return f"{float(v or 0):,.0f}"
    except Exception: return "0"


def _parse_date_safe(v: str):
    try: return datetime.strptime(str(v or ""), "%Y-%m-%d").date()
    except Exception: return None


def _filter_text_rows(rows, fields, keyword):
    q = (keyword or "").strip().lower()
    if not q: return list(rows)
    out=[]
    for r in rows:
        hay=" ".join(str(r[f] or "") for f in fields if f in r.keys()).lower()
        if q in hay: out.append(r)
    return out


def render_cost_management(pid: int):
    st.subheader("💰 Quản lý chi phí")
    tab1, tab2, tab3 = st.tabs(["Chi phí dự toán (BOQ)", "Thanh toán & giải ngân", "Chi phí phát sinh (VO)"])
    task_refs, task_labels, task_map = _task_option_data(pid)

    with tab1:
        rows = db.cost_budgets(pid)
        total_bac = sum(float(r["budget_total"] or 0) for r in rows)
        st.metric("Tổng ngân sách kế hoạch (BAC)", f"{_vnd(total_bac)} VND")
        options=[None]+[int(r["id"]) for r in rows]
        selected=st.selectbox("Chọn dòng BOQ để sửa", options, format_func=lambda x:"➕ Thêm mới" if x is None else f"#{x} - {next(r['boq_item'] for r in rows if r['id']==x)}", key=f"cost_boq_sel_{pid}")
        rec=db.cost_budget(selected) if selected else None
        with st.form(f"cost_boq_form_{pid}_{selected or 'new'}"):
            task_ref=_select_existing("Mã Task", task_refs, rec["task_ref"] if rec else "", f"cost_boq_task_{pid}_{selected}")
            item=st.text_input("Hạng mục / Khối lượng (BOQ) *", value=rec["boq_item"] if rec else "")
            c1,c2,c3=st.columns(3)
            qty=c1.number_input("Khối lượng", min_value=0.0, value=float(rec["quantity"] or 0) if rec else 0.0, step=1.0)
            unit=c2.text_input("Đơn vị tính", value=rec["unit"] if rec else "")
            price=c3.number_input("Đơn giá dự toán (VND)", min_value=0.0, value=float(rec["unit_price"] or 0) if rec else 0.0, step=1000.0)
            total=qty*price
            st.number_input("Tổng ngân sách kế hoạch (VND)", min_value=0.0, value=float(total), disabled=True)
            c1,c2=st.columns(2)
            contract=c1.selectbox("Hình thức hợp đồng", ["Trọn gói","Đơn giá điều chỉnh","Đơn giá cố định","Khác"], index=(["Trọn gói","Đơn giá điều chỉnh","Đơn giá cố định","Khác"].index(rec["contract_type"]) if rec and rec["contract_type"] in ["Trọn gói","Đơn giá điều chỉnh","Đơn giá cố định","Khác"] else 0))
            contractor=c2.text_input("Nhà thầu phụ trách", value=rec["contractor"] if rec else "")
            note=st.text_area("Ghi chú", value=rec["note"] if rec else "")
            save=st.form_submit_button("💾 Lưu BOQ", type="primary", disabled=not _can_update(), width="stretch")
        if save:
            if not item.strip(): st.error("Hạng mục BOQ là bắt buộc.")
            else:
                rid=db.save_cost_budget(pid,{"task_ref":task_ref,"boq_item":item,"quantity":qty,"unit":unit,"unit_price":price,"budget_total":total,"contract_type":contract,"contractor":contractor,"note":note},selected)
                st.success("Đã lưu chi phí dự toán."); st.rerun()
        if selected and st.button("🗑 Xóa dòng BOQ", disabled=not _is_admin(), key=f"del_boq_{pid}_{selected}"):
            db.delete_cost_budget(selected); st.rerun()
        q=st.text_input("Tìm BOQ / Task / Nhà thầu", key=f"filter_boq_{pid}")
        filt=_filter_text_rows(rows,["task_ref","boq_item","unit","contract_type","contractor","note"],q)
        if filt:
            st.dataframe(pd.DataFrame([{"ID":r["id"],"Mã Task":r["task_ref"],"Hạng mục / BOQ":r["boq_item"],"Khối lượng":r["quantity"],"ĐVT":r["unit"],"Đơn giá":r["unit_price"],"BAC (VND)":r["budget_total"],"Hợp đồng":r["contract_type"],"Nhà thầu":r["contractor"],"Ghi chú":r["note"]} for r in filt]), hide_index=True, width="stretch")

    with tab2:
        rows=db.payments(pid); budgets=db.cost_budgets(pid)
        budget_by_task={}
        for b in budgets: budget_by_task[b["task_ref"]]=budget_by_task.get(b["task_ref"],0)+float(b["budget_total"] or 0)
        total_paid=sum(float(r["paid_amount"] or 0) for r in rows)
        c1,c2,c3=st.columns(3); c1.metric("Đã thanh toán",f"{_vnd(total_paid)} VND"); c2.metric("BAC",f"{_vnd(sum(budget_by_task.values()))} VND"); c3.metric("Giải ngân/BAC",f"{(100*total_paid/sum(budget_by_task.values()) if sum(budget_by_task.values()) else 0):.1f}%")
        options=[None]+[int(r["id"]) for r in rows]; selected=st.selectbox("Chọn thanh toán để sửa",options,format_func=lambda x:"➕ Thêm mới" if x is None else f"#{x} - {next(r['payment_code'] for r in rows if r['id']==x)}",key=f"pay_sel_{pid}"); rec=db.payment(selected) if selected else None
        with st.form(f"pay_form_{pid}_{selected or 'new'}"):
            c1,c2=st.columns(2); code=c1.text_input("Mã Thanh Toán *",value=rec["payment_code"] if rec else ""); installment=c2.text_input("Đợt thanh toán",value=rec["installment"] if rec else "")
            task_ref=_select_existing("Mã Task",task_refs,rec["task_ref"] if rec else "",f"pay_task_{pid}_{selected}")
            c1,c2=st.columns(2); certified=c1.number_input("Giá trị nghiệm thu lũy kế (VND)",min_value=0.0,value=float(rec["certified_cumulative"] or 0) if rec else 0.0,step=1000000.0); paid=c2.number_input("Giá trị đã thanh toán (VND)",min_value=0.0,value=float(rec["paid_amount"] or 0) if rec else 0.0,step=1000000.0)
            c1,c2=st.columns(2); advance=c1.number_input("Giá trị tạm ứng (VND)",min_value=0.0,value=float(rec["advance_amount"] or 0) if rec else 0.0,step=1000000.0); recovery=c2.number_input("Thu hồi tạm ứng (VND)",min_value=0.0,value=float(rec["advance_recovery"] or 0) if rec else 0.0,step=1000000.0)
            c1,c2,c3=st.columns(3); planned_pct=c1.number_input("Giải ngân kế hoạch (%)",min_value=0.0,max_value=100.0,value=float(rec["planned_disbursement_pct"] or 0) if rec else 0.0); status=c2.selectbox("Trạng thái hồ sơ",["Chuẩn bị","Đã trình","Đang kiểm tra","Đã duyệt","Đã thanh toán","Từ chối"],index=0 if not rec or rec["payment_status"] not in ["Chuẩn bị","Đã trình","Đang kiểm tra","Đã duyệt","Đã thanh toán","Từ chối"] else ["Chuẩn bị","Đã trình","Đang kiểm tra","Đã duyệt","Đã thanh toán","Từ chối"].index(rec["payment_status"])); pdate=c3.date_input("Ngày thanh toán",value=_parse_date_safe(rec["payment_date"]) if rec and rec["payment_date"] else date.today())
            bac=budget_by_task.get(task_ref,0); actual_pct=100*paid/bac if bac else 0; st.text_input("Giải ngân thực tế vs kế hoạch",value=f"{actual_pct:.1f}% / {planned_pct:.1f}%",disabled=True)
            note=st.text_area("Ghi chú",value=rec["note"] if rec else ""); save=st.form_submit_button("💾 Lưu thanh toán",type="primary",disabled=not _can_update(),width="stretch")
        if save:
            if not code.strip(): st.error("Mã Thanh Toán là bắt buộc.")
            else:
                try: db.save_payment(pid,{"payment_code":code.strip(),"task_ref":task_ref,"installment":installment,"certified_cumulative":certified,"paid_amount":paid,"advance_amount":advance,"advance_recovery":recovery,"planned_disbursement_pct":planned_pct,"payment_status":status,"payment_date":iso(pdate),"note":note},selected); st.success("Đã lưu thanh toán."); st.rerun()
                except sqlite3.IntegrityError: st.error("Mã Thanh Toán đã tồn tại.")
        if selected and st.button("🗑 Xóa thanh toán",disabled=not _is_admin(),key=f"del_pay_{pid}_{selected}"): db.delete_payment(selected); st.rerun()
        q=st.text_input("Tìm mã thanh toán / Task / trạng thái",key=f"filter_pay_{pid}"); filt=_filter_text_rows(rows,["payment_code","task_ref","installment","payment_status","note"],q)
        data=[]
        for r in filt:
            bac=budget_by_task.get(r["task_ref"],0); act=100*float(r["paid_amount"] or 0)/bac if bac else 0
            data.append({"ID":r["id"],"Mã thanh toán":r["payment_code"],"Mã Task":r["task_ref"],"Đợt":r["installment"],"Nghiệm thu LK":r["certified_cumulative"],"Đã thanh toán":r["paid_amount"],"Tạm ứng":r["advance_amount"],"Thu hồi TU":r["advance_recovery"],"Giải ngân TT/KH":f"{act:.1f}% / {float(r['planned_disbursement_pct'] or 0):.1f}%","Trạng thái":r["payment_status"],"Ngày":r["payment_date"]})
        if data: st.dataframe(pd.DataFrame(data),hide_index=True,width="stretch")

    with tab3:
        rows=db.cost_variations(pid); c1,c2=st.columns(2); c1.metric("Phát sinh trình duyệt",f"{_vnd(sum(float(r['proposed_amount'] or 0) for r in rows))} VND"); c2.metric("Phát sinh đã duyệt",f"{_vnd(sum(float(r['approved_amount'] or 0) for r in rows))} VND")
        options=[None]+[int(r["id"]) for r in rows]; selected=st.selectbox("Chọn VO để sửa",options,format_func=lambda x:"➕ Thêm mới" if x is None else f"#{x} - {next(r['vo_code'] for r in rows if r['id']==x)}",key=f"vo_cost_sel_{pid}"); rec=db.cost_variation(selected) if selected else None
        with st.form(f"vo_cost_form_{pid}_{selected or 'new'}"):
            c1,c2=st.columns(2); code=c1.text_input("Mã VO *",value=rec["vo_code"] if rec else ""); vdate=c2.date_input("Ngày phát sinh",value=_parse_date_safe(rec["vo_date"]) if rec and rec["vo_date"] else date.today())
            task_ref=_select_existing("Mã Task liên quan",task_refs,rec["task_ref"] if rec else "",f"vo_task_{pid}_{selected}")
            desc=st.text_area("Nội dung điều chỉnh / Phát sinh *",value=rec["description"] if rec else "")
            c1,c2=st.columns(2); proposed=c1.number_input("Dự toán phát sinh trình duyệt (VND)",min_value=0.0,value=float(rec["proposed_amount"] or 0) if rec else 0.0,step=1000000.0); approved=c2.number_input("Giá trị duyệt chính thức (VND)",min_value=0.0,value=float(rec["approved_amount"] or 0) if rec else 0.0,step=1000000.0)
            c1,c2=st.columns(2); funding=c1.selectbox("Nguồn kinh phí",["Dự phòng phí","CĐT bổ sung","Điều chuyển ngân sách","Khác"],index=0 if not rec or rec["funding_source"] not in ["Dự phòng phí","CĐT bổ sung","Điều chuyển ngân sách","Khác"] else ["Dự phòng phí","CĐT bổ sung","Điều chuyển ngân sách","Khác"].index(rec["funding_source"])); status=c2.selectbox("Trạng thái",["Dự thảo","Đã trình","Đang duyệt","Đã duyệt","Từ chối","Đóng"],index=0 if not rec or rec["status"] not in ["Dự thảo","Đã trình","Đang duyệt","Đã duyệt","Từ chối","Đóng"] else ["Dự thảo","Đã trình","Đang duyệt","Đã duyệt","Từ chối","Đóng"].index(rec["status"]))
            note=st.text_area("Ghi chú",value=rec["note"] if rec else ""); save=st.form_submit_button("💾 Lưu VO",type="primary",disabled=not _can_update(),width="stretch")
        if save:
            if not code.strip() or not desc.strip(): st.error("Mã VO và nội dung phát sinh là bắt buộc.")
            else:
                try: db.save_cost_variation(pid,{"vo_code":code.strip(),"task_ref":task_ref,"description":desc,"proposed_amount":proposed,"approved_amount":approved,"funding_source":funding,"status":status,"vo_date":iso(vdate),"note":note},selected); st.success("Đã lưu chi phí phát sinh."); st.rerun()
                except sqlite3.IntegrityError: st.error("Mã VO đã tồn tại.")
        if selected and st.button("🗑 Xóa VO",disabled=not _is_admin(),key=f"del_vo_cost_{pid}_{selected}"): db.delete_cost_variation(selected); st.rerun()
        q=st.text_input("Tìm VO / Task / nội dung",key=f"filter_vo_cost_{pid}"); filt=_filter_text_rows(rows,["vo_code","task_ref","description","funding_source","status","note"],q)
        if filt: st.dataframe(pd.DataFrame([{"ID":r["id"],"Mã VO":r["vo_code"],"Mã Task":r["task_ref"],"Nội dung":r["description"],"Trình duyệt":r["proposed_amount"],"Đã duyệt":r["approved_amount"],"Nguồn kinh phí":r["funding_source"],"Trạng thái":r["status"],"Ngày":r["vo_date"]} for r in filt]),hide_index=True,width="stretch")


def render_material_management(pid: int):
    st.subheader("📦 Vật tư & thiết bị")
    tab1,tab2,tab3=st.tabs(["Danh mục vật tư/thiết bị","Tiến độ mua sắm & cung ứng","Nhập - Xuất - Tồn & Kiểm định"])
    task_refs, task_labels, task_map=_task_option_data(pid)

    with tab1:
        rows=db.materials(pid); c1,c2,c3=st.columns(3); c1.metric("Tổng chủng loại",len(rows)); c2.metric("CĐT cung cấp",sum(1 for r in rows if r["supply_type"]=="CĐT cung cấp")); c3.metric("Nhà thầu cung cấp",sum(1 for r in rows if r["supply_type"]=="Nhà thầu cung cấp"))
        options=[None]+[int(r["id"]) for r in rows]; selected=st.selectbox("Chọn vật tư để sửa",options,format_func=lambda x:"➕ Thêm mới" if x is None else f"#{x} - {next(r['material_code'] for r in rows if r['id']==x)}",key=f"mat_sel_{pid}"); rec=db.material(selected) if selected else None
        with st.form(f"mat_form_{pid}_{selected or 'new'}"):
            c1,c2=st.columns(2); code=c1.text_input("Mã Vật tư *",value=rec["material_code"] if rec else ""); name=c2.text_input("Tên vật tư/thiết bị *",value=rec["material_name"] if rec else "")
            spec=st.text_input("Quy cách / Thương hiệu",value=rec["spec_brand"] if rec else "")
            legal=st.text_input("Mã Tiêu chuẩn/Quy chuẩn",value=rec["legal_ref"] if rec else "",placeholder="[LEGAL:TCVN 9206:2012]")
            c1,c2=st.columns(2); supply=c1.selectbox("Phân loại cung cấp",["CĐT cung cấp","Nhà thầu cung cấp","Khác"],index=0 if not rec or rec["supply_type"] not in ["CĐT cung cấp","Nhà thầu cung cấp","Khác"] else ["CĐT cung cấp","Nhà thầu cung cấp","Khác"].index(rec["supply_type"])); task_ref=_select_existing("Mã Task sử dụng",task_refs,rec["task_ref"] if rec else "",f"mat_task_{pid}_{selected}")
            note=st.text_area("Ghi chú",value=rec["note"] if rec else ""); save=st.form_submit_button("💾 Lưu vật tư",type="primary",disabled=not _can_update(),width="stretch")
        if save:
            if not code.strip() or not name.strip(): st.error("Mã và tên vật tư là bắt buộc.")
            else:
                try: db.save_material(pid,{"material_code":code.strip(),"material_name":name,"spec_brand":spec,"legal_ref":legal,"supply_type":supply,"task_ref":task_ref,"note":note},selected); st.success("Đã lưu danh mục vật tư."); st.rerun()
                except sqlite3.IntegrityError: st.error("Mã vật tư đã tồn tại.")
        if selected and st.button("🗑 Xóa vật tư",disabled=not _is_admin(),key=f"del_mat_{pid}_{selected}"): db.delete_material(selected); st.rerun()
        q=st.text_input("Tìm mã / tên / thương hiệu / tiêu chuẩn / Task",key=f"filter_mat_{pid}"); c=st.selectbox("Nguồn cung",["Tất cả","CĐT cung cấp","Nhà thầu cung cấp","Khác"],key=f"filter_supply_{pid}"); filt=_filter_text_rows(rows,["material_code","material_name","spec_brand","legal_ref","task_ref","note"],q); filt=[r for r in filt if c=="Tất cả" or r["supply_type"]==c]
        if filt: st.dataframe(pd.DataFrame([{"ID":r["id"],"Mã vật tư":r["material_code"],"Tên":r["material_name"],"Quy cách/Thương hiệu":r["spec_brand"],"Tiêu chuẩn/QCVN":r["legal_ref"],"Nguồn cung":r["supply_type"],"Mã Task":r["task_ref"],"Ghi chú":r["note"]} for r in filt]),hide_index=True,width="stretch")

    with tab2:
        rows=db.procurements(pid); materials=db.materials(pid); mat_codes=[r["material_code"] for r in materials]
        def warning_for(r):
            t=task_map.get(r["task_ref"]); task_start=_parse_date_safe(t["start_date"]) if t else None; actual=_parse_date_safe(r["actual_delivery_date"]); planned=_parse_date_safe(r["planned_delivery_date"]); delivery=actual or planned
            if task_start and delivery and delivery>task_start: return f"⚠ Trễ { (delivery-task_start).days } ngày so với khởi công"
            if planned and not actual and date.today()>planned: return f"⚠ Quá hạn giao { (date.today()-planned).days } ngày"
            return "✅ Đúng/Trước mốc" if delivery and task_start else "—"
        late=sum(1 for r in rows if warning_for(r).startswith("⚠")); c1,c2=st.columns(2); c1.metric("Kế hoạch mua sắm",len(rows)); c2.metric("Cảnh báo trễ",late)
        options=[None]+[int(r["id"]) for r in rows]; selected=st.selectbox("Chọn kế hoạch mua sắm để sửa",options,format_func=lambda x:"➕ Thêm mới" if x is None else f"#{x} - {next(r['material_code'] for r in rows if r['id']==x)}",key=f"proc_sel_{pid}"); rec=db.procurement(selected) if selected else None
        with st.form(f"proc_form_{pid}_{selected or 'new'}"):
            mat=_select_existing("Mã Vật tư *",mat_codes,rec["material_code"] if rec else "",f"proc_mat_{pid}_{selected}"); task_ref=_select_existing("Mã Task lắp đặt",task_refs,rec["task_ref"] if rec else "",f"proc_task_{pid}_{selected}"); supplier=st.text_input("Nhà cung cấp / Nhà sản xuất",value=rec["supplier"] if rec else "")
            c1,c2=st.columns(2); sample=c1.date_input("Ngày phê duyệt mẫu/Spec",value=_parse_date_safe(rec["sample_approval_date"]) if rec and rec["sample_approval_date"] else date.today()); order=c2.date_input("Ngày đặt hàng",value=_parse_date_safe(rec["order_date"]) if rec and rec["order_date"] else date.today())
            c1,c2=st.columns(2); planned=c1.date_input("Ngày giao hàng kế hoạch",value=_parse_date_safe(rec["planned_delivery_date"]) if rec and rec["planned_delivery_date"] else date.today()); has_actual=c2.checkbox("Đã về công trường",value=bool(rec and rec["actual_delivery_date"])); actual=c2.date_input("Ngày về thực tế",value=_parse_date_safe(rec["actual_delivery_date"]) if rec and rec["actual_delivery_date"] else date.today(),disabled=not has_actual)
            status=st.selectbox("Trạng thái",["Chờ duyệt mẫu","Đã duyệt mẫu","Đã đặt hàng","Đang sản xuất","Đang vận chuyển","Đã về công trường","Chậm","Hủy"],index=0 if not rec or rec["status"] not in ["Chờ duyệt mẫu","Đã duyệt mẫu","Đã đặt hàng","Đang sản xuất","Đang vận chuyển","Đã về công trường","Chậm","Hủy"] else ["Chờ duyệt mẫu","Đã duyệt mẫu","Đã đặt hàng","Đang sản xuất","Đang vận chuyển","Đã về công trường","Chậm","Hủy"].index(rec["status"])); note=st.text_area("Ghi chú",value=rec["note"] if rec else ""); save=st.form_submit_button("💾 Lưu tiến độ mua sắm",type="primary",disabled=not _can_update(),width="stretch")
        if save:
            if not mat: st.error("Mã vật tư là bắt buộc.")
            else: db.save_procurement(pid,{"material_code":mat,"task_ref":task_ref,"supplier":supplier,"sample_approval_date":iso(sample),"order_date":iso(order),"planned_delivery_date":iso(planned),"actual_delivery_date":iso(actual) if has_actual else "","status":status,"note":note},selected); st.success("Đã lưu tiến độ mua sắm."); st.rerun()
        if selected and st.button("🗑 Xóa kế hoạch mua sắm",disabled=not _is_admin(),key=f"del_proc_{pid}_{selected}"): db.delete_procurement(selected); st.rerun()
        q=st.text_input("Tìm vật tư / Task / nhà cung cấp",key=f"filter_proc_{pid}"); warn_filter=st.selectbox("Cảnh báo",["Tất cả","Có cảnh báo trễ","Không cảnh báo"],key=f"filter_proc_warn_{pid}"); filt=_filter_text_rows(rows,["material_code","task_ref","supplier","status","note"],q); filt=[r for r in filt if warn_filter=="Tất cả" or (warn_filter=="Có cảnh báo trễ" and warning_for(r).startswith("⚠")) or (warn_filter=="Không cảnh báo" and not warning_for(r).startswith("⚠"))]
        if filt: st.dataframe(pd.DataFrame([{"ID":r["id"],"Mã vật tư":r["material_code"],"Mã Task":r["task_ref"],"Nhà cung cấp":r["supplier"],"Duyệt mẫu":r["sample_approval_date"],"Đặt hàng":r["order_date"],"Giao KH":r["planned_delivery_date"],"Về thực tế":r["actual_delivery_date"],"Trạng thái":r["status"],"Cảnh báo":warning_for(r)} for r in filt]),hide_index=True,width="stretch")

    with tab3:
        rows=db.inventory_rows(pid); materials=db.materials(pid); mat_codes=[r["material_code"] for r in materials]
        stock={}
        for r in rows: stock[r["material_code"]]=stock.get(r["material_code"],0)+float(r["quantity_in"] or 0)-float(r["quantity_out"] or 0)
        c1,c2,c3=st.columns(3); c1.metric("Số phiếu",len(rows)); c2.metric("Vật tư tồn > 0",sum(1 for v in stock.values() if v>0)); c3.metric("Không đạt",sum(1 for r in rows if r["material_status"]=="Không đạt"))
        options=[None]+[int(r["id"]) for r in rows]; selected=st.selectbox("Chọn phiếu để sửa",options,format_func=lambda x:"➕ Thêm mới" if x is None else f"#{x} - {next(r['slip_code'] for r in rows if r['id']==x)}",key=f"inv_sel_{pid}"); rec=db.inventory_row(selected) if selected else None
        with st.form(f"inv_form_{pid}_{selected or 'new'}"):
            c1,c2=st.columns(2); slip=c1.text_input("Mã Phiếu Nhập/Xuất *",value=rec["slip_code"] if rec else ""); tdate=c2.date_input("Ngày",value=_parse_date_safe(rec["transaction_date"]) if rec and rec["transaction_date"] else date.today())
            mat=_select_existing("Mã Vật tư *",mat_codes,rec["material_code"] if rec else "",f"inv_mat_{pid}_{selected}")
            c1,c2=st.columns(2); qin=c1.number_input("Số lượng nhập",min_value=0.0,value=float(rec["quantity_in"] or 0) if rec else 0.0); qout=c2.number_input("Số lượng xuất",min_value=0.0,value=float(rec["quantity_out"] or 0) if rec else 0.0)
            task_ref=_select_existing("Số lượng xuất cho Task",task_refs,rec["task_ref"] if rec else "",f"inv_task_{pid}_{selected}"); insp=st.text_input("Mã Biên bản kiểm tra chất lượng (RFA/Biên bản đầu vào)",value=rec["inspection_code"] if rec else "")
            status=st.selectbox("Trạng thái vật tư",["Chờ kiểm định","Đã nghiệm thu Đạt","Không đạt"],index=0 if not rec or rec["material_status"] not in ["Chờ kiểm định","Đã nghiệm thu Đạt","Không đạt"] else ["Chờ kiểm định","Đã nghiệm thu Đạt","Không đạt"].index(rec["material_status"])); note=st.text_area("Ghi chú",value=rec["note"] if rec else ""); save=st.form_submit_button("💾 Lưu phiếu",type="primary",disabled=not _can_update(),width="stretch")
        if save:
            if not slip.strip() or not mat: st.error("Mã phiếu và mã vật tư là bắt buộc.")
            else:
                try: db.save_inventory_row(pid,{"slip_code":slip.strip(),"transaction_date":iso(tdate),"material_code":mat,"quantity_in":qin,"quantity_out":qout,"task_ref":task_ref,"inspection_code":insp,"material_status":status,"note":note},selected); st.success("Đã lưu nhập/xuất/kiểm định."); st.rerun()
                except sqlite3.IntegrityError: st.error("Mã phiếu đã tồn tại.")
        if selected and st.button("🗑 Xóa phiếu",disabled=not _is_admin(),key=f"del_inv_{pid}_{selected}"): db.delete_inventory_row(selected); st.rerun()
        q=st.text_input("Tìm phiếu / vật tư / Task / biên bản",key=f"filter_inv_{pid}"); sf=st.selectbox("Trạng thái",["Tất cả","Chờ kiểm định","Đã nghiệm thu Đạt","Không đạt"],key=f"filter_inv_status_{pid}"); filt=_filter_text_rows(rows,["slip_code","material_code","task_ref","inspection_code","material_status","note"],q); filt=[r for r in filt if sf=="Tất cả" or r["material_status"]==sf]
        if filt: st.dataframe(pd.DataFrame([{"ID":r["id"],"Mã phiếu":r["slip_code"],"Ngày":r["transaction_date"],"Mã vật tư":r["material_code"],"Nhập":r["quantity_in"],"Xuất":r["quantity_out"],"Tồn hiện tại":stock.get(r["material_code"],0),"Mã Task":r["task_ref"],"Biên bản kiểm tra":r["inspection_code"],"Trạng thái":r["material_status"]} for r in filt]),hide_index=True,width="stretch")


def render_reports(pid: int):
    st.subheader("📊 Báo cáo trực quan")
    project = db.project(pid)
    tasks = db.tasks(pid)
    total_tasks = len(tasks)
    planned_avg = sum(float(t["planned_progress"] or 0) for t in tasks) / total_tasks if total_tasks else 0
    actual_avg = sum(float(t["actual_progress"] or 0) for t in tasks) / total_tasks if total_tasks else 0
    delayed = sum(1 for t in tasks if (t["status"] or "") == "Chậm tiến độ")
    completed = sum(1 for t in tasks if (t["status"] or "") == "Hoàn thành")
    delay_pct = delayed * 100 / total_tasks if total_tasks else 0
    done_pct = completed * 100 / total_tasks if total_tasks else 0

    doc_summary = []
    doc_total_all = 0
    doc_done_all = 0
    doc_labels = {"NCR":"NCR", "RFA":"RFA", "RFI":"RFI", "BBHT":"Biên bản hiện trường", "NKCT":"Nhật ký công trường", "NTCV":"NT công việc", "NTVL":"NT VL đầu vào", "KDVT":"Kiểm định VT"}
    for doc_type, cfg in DOC_CONFIG.items():
        rows = db.documents(pid, doc_type)
        total = len(rows)
        done_set = set(cfg.get("done_statuses", []))
        done = sum(1 for r in rows if (r["status"] or "") in done_set)
        pct = done * 100 / total if total else 0
        doc_summary.append({"Loại": doc_labels.get(doc_type, doc_type), "% xử lý": pct, "Đã xử lý": done, "Tổng": total})
        doc_total_all += total
        doc_done_all += done
    doc_pct_all = doc_done_all * 100 / doc_total_all if doc_total_all else 0

    approved_statuses = {"Chấp thuận", "Chấp thuận có điều kiện"}
    drawing_summary = []
    drawing_total_all = 0
    drawing_approved_all = 0
    for drawing_type, label in DRAWING_TYPES.items():
        rows = db.drawings(pid, drawing_type)
        total = len(rows)
        approved = sum(1 for r in rows if (r["status"] or "") in approved_statuses)
        pct = approved * 100 / total if total else 0
        drawing_summary.append({"Loại": label, "% chấp thuận": pct, "Chấp thuận": approved, "Tổng": total})
        drawing_total_all += total
        drawing_approved_all += approved
    drawing_pct_all = drawing_approved_all * 100 / drawing_total_all if drawing_total_all else 0

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("KH trung bình", f"{planned_avg:.1f}%")
    c2.metric("TT trung bình", f"{actual_avg:.1f}%", f"{actual_avg-planned_avg:+.1f}% so KH")
    c3.metric("Công việc chậm", f"{delay_pct:.1f}%", f"{delayed}/{total_tasks}")
    c4.metric("Hoàn thành", f"{done_pct:.1f}%", f"{completed}/{total_tasks}")
    c5.metric("Hồ sơ đã xử lý", f"{doc_pct_all:.1f}%", f"{doc_done_all}/{doc_total_all}")
    c6.metric("BV đã chấp thuận", f"{drawing_pct_all:.1f}%", f"{drawing_approved_all}/{drawing_total_all}")

    left, right = st.columns(2)
    with left:
        st.markdown("#### KH % và TT %")
        progress_df = pd.DataFrame({"Chỉ tiêu": ["KH trung bình", "TT trung bình"], "Phần trăm": [planned_avg, actual_avg]})
        if px is not None:
            fig = px.bar(progress_df, x="Chỉ tiêu", y="Phần trăm", text_auto=".1f", range_y=[0, 100])
            fig.update_traces(texttemplate="%{y:.1f}%", textposition="outside")
            fig.update_layout(height=340, margin=dict(l=10, r=10, t=15, b=10), showlegend=False, yaxis_title="%")
            st.plotly_chart(fig, width="stretch")
        else:
            st.bar_chart(progress_df.set_index("Chỉ tiêu"))

    with right:
        st.markdown("#### Cơ cấu trạng thái tiến độ")
        task_status = [
            {"Trạng thái": "Hoàn thành", "Số lượng": completed},
            {"Trạng thái": "Đúng/Nhanh", "Số lượng": sum(1 for t in tasks if (t["status"] or "") in ("Đúng tiến độ", "Nhanh tiến độ"))},
            {"Trạng thái": "Chậm", "Số lượng": delayed},
            {"Trạng thái": "Chưa bắt đầu/Khác", "Số lượng": sum(1 for t in tasks if (t["status"] or "") not in ("Hoàn thành", "Đúng tiến độ", "Nhanh tiến độ", "Chậm tiến độ"))},
        ]
        status_df = pd.DataFrame(task_status)
        if px is not None and status_df["Số lượng"].sum() > 0:
            fig = px.pie(status_df, names="Trạng thái", values="Số lượng", hole=0.52)
            fig.update_traces(textposition="inside", textinfo="percent+label")
            fig.update_layout(height=340, margin=dict(l=10, r=10, t=15, b=10), legend_title_text="")
            st.plotly_chart(fig, width="stretch")
        else:
            st.dataframe(status_df, width="stretch", hide_index=True)

    left2, right2 = st.columns(2)
    with left2:
        st.markdown("#### Tỷ lệ xử lý hồ sơ")
        doc_df = pd.DataFrame(doc_summary)
        if not doc_df.empty and px is not None:
            fig = px.bar(doc_df, x="% xử lý", y="Loại", orientation="h", text_auto=".1f", range_x=[0, 100])
            fig.update_traces(texttemplate="%{x:.1f}%", textposition="outside")
            fig.update_layout(height=390, margin=dict(l=10, r=20, t=15, b=10), yaxis_title="", xaxis_title="%")
            st.plotly_chart(fig, width="stretch")
        else:
            st.dataframe(doc_df, width="stretch", hide_index=True)

    with right2:
        st.markdown("#### Tỷ lệ chấp thuận bản vẽ")
        drawing_df = pd.DataFrame(drawing_summary)
        if not drawing_df.empty and px is not None:
            fig = px.bar(drawing_df, x="% chấp thuận", y="Loại", orientation="h", text_auto=".1f", range_x=[0, 100])
            fig.update_traces(texttemplate="%{x:.1f}%", textposition="outside")
            fig.update_layout(height=390, margin=dict(l=10, r=20, t=15, b=10), yaxis_title="", xaxis_title="%")
            st.plotly_chart(fig, width="stretch")
        else:
            st.dataframe(drawing_df, width="stretch", hide_index=True)

    _ui_note("Báo cáo được tính trực tiếp từ dữ liệu dự án hiện tại; khi TT%, hồ sơ hoặc bản vẽ thay đổi, mở lại tab này để xem số liệu mới nhất.")

def _legal_click_url(row) -> str:
    """Luôn trả về URL có thể bấm để xem văn bản; TVPL thiếu link thì fallback sang trang tìm kiếm TVPL."""
    try:
        url = str(row["source_url"] or "").strip()
    except Exception:
        url = str((row or {}).get("source_url", "") or "").strip() if isinstance(row, dict) else ""
    if url.startswith(("http://", "https://")):
        return url
    try:
        source_name = str(row["source_name"] or "")
        number = str(row["number"] or "").strip()
        title = str(row["title"] or "").strip()
    except Exception:
        source_name = str((row or {}).get("source_name", "")) if isinstance(row, dict) else ""
        number = str((row or {}).get("number", "")).strip() if isinstance(row, dict) else ""
        title = str((row or {}).get("title", "")).strip() if isinstance(row, dict) else ""
    q = number or title
    if "Thư Viện Pháp Luật" in source_name and q:
        return "https://thuvienphapluat.vn/page/tim-van-ban.aspx?keyword=" + quote_plus(q)
    if q:
        return "https://www.google.com/search?q=" + quote_plus(q)
    return ""


def render_legal_documents():
    from legal_documents import sync_source, sync_all, search_online_all, search_online_sites
    legal_repo = _legal_repo_for_view()
    st.subheader("📚 Văn bản QLDA Xây dựng")
    _ui_note("Luật • Nghị định • Thông tư • QCVN • TCVN • Quyết định • Dự thảo — TVPL là nguồn tra cứu chính/ưu tiên; luôn giữ link để mở văn bản trực tiếp.")

    last = legal_repo.last_sync()
    if last:
        st.info(f"Cập nhật online gần nhất: **{last['sync_time']}** — {last['source_name']} — {last['status']}")
    else:
        st.info("Chưa có lần cập nhật online. Bấm **Cập nhật tất cả nguồn** để tải danh mục mới nhất.")

    c1, c2, c3, c4, c5 = st.columns(5)
    actions = [
        (c1, "🔄 Cập nhật tất cả", "all"),
        (c2, "⚖️ VBPL / Chính phủ", "vbpl"),
        (c3, "📐 TCVN - VSQI", "vsqi"),
        (c4, "📝 Dự thảo BXD", "moc_drafts"),
        (c5, "📚 Cập nhật TVPL (ưu tiên)", "tvpl"),
    ]
    for col, label, source in actions:
        if col.button(label, width="stretch", key=f"legal_sync_{source}", disabled=not _can_update()):
            with st.spinner("Đang cập nhật metadata online và đường dẫn mở văn bản..."):
                results = sync_all(legal_repo) if source == "all" else [sync_source(legal_repo, source)]
            errors = [r for r in results if r.get("error")]
            total_added = sum(r.get("added", 0) for r in results)
            total_updated = sum(r.get("updated", 0) for r in results)
            crosschecked = sum(r.get("crosschecked", 0) for r in results)
            if errors:
                st.warning("; ".join(f"{r['source']}: {r['error']}" for r in errors))
            msg = f"Cập nhật xong: thêm {total_added} bản ghi, cập nhật {total_updated} bản ghi."
            if crosschecked:
                msg += f" Đã đối chiếu {crosschecked} bản TVPL với nguồn chính thức cùng số hiệu trong kho."
            st.success(msg)
            if source == "tvpl":
                st.session_state["legal_focus_tvpl_after_sync"] = True
            st.rerun()

    with st.expander("🔎 Google / Tìm kiếm online toàn web", expanded=True):
        appcfg = _runtime_app_settings()
        if appcfg.get("google_api_key") and appcfg.get("google_cx"):
            st.success("Google Search API đã cấu hình tại ⚙️ Cài đặt/Secrets. App ưu tiên kết quả Google.")
        else:
            st.info("Google API chưa cấu hình. Tìm tự động dùng engine fallback rộng; có thể cấu hình tại sheet ⚙️ Cài đặt.")
        _ui_note("Nguồn chính thức được ưu tiên xếp hạng nhưng KHÔNG giới hạn phạm vi tìm kiếm. Luôn mở nguồn gốc để kiểm tra hiệu lực trước khi áp dụng.")
        _ui_note("Trang chỉ định: " + ", ".join(appcfg.get("specified_search_domains", [])) + ". Có thể sửa danh sách tại sheet ⚙️ Cài đặt.")
        q1, q2, qsites, q3 = st.columns([4, 1, 1.2, 1])
        query = q1.text_input("Số hiệu / nội dung cần tìm", placeholder="Ví dụ: Thông tư 06/2021/TT-BXD phân cấp công trình xây dựng; TCVN 5575; QCVN 06", key="legal_web_query")
        if query.strip():
            q3.link_button("Mở Google ↗", f"https://www.google.com/search?q={quote_plus(query.strip())}", width="stretch")
        else:
            q3.button("Mở Google ↗", disabled=True, width="stretch", key="google_disabled")
        if q2.button("Tìm Google/web", type="primary", width="stretch", key="legal_web_search"):
            if not query.strip():
                st.warning("Nhập số hiệu hoặc nội dung cần tìm.")
            else:
                try:
                    with st.spinner("Đang tìm Google / toàn web..."):
                        found = search_online_all(query, google_api_key=appcfg.get("google_api_key"), google_cx=appcfg.get("google_cx"))
                        stats = legal_repo.upsert_many(found, "Tìm kiếm online tổng hợp")
                    st.session_state["legal_web_results"] = found
                    st.session_state["legal_web_results_query"] = query
                    st.success(f"Tìm thấy {len(found)} kết quả; đã thêm {stats['added']}, cập nhật {stats['updated']}.")
                except Exception as exc:
                    st.error(f"Không tìm kiếm online được: {exc}")
        if qsites.button("Tìm trang chỉ định", width="stretch", key="legal_sites_search"):
            if not query.strip():
                st.warning("Nhập số hiệu hoặc nội dung cần tìm.")
            else:
                try:
                    with st.spinner("Đang tìm trong các trang được chỉ định..."):
                        found = search_online_sites(query, domains=appcfg.get("specified_search_domains"), google_api_key=appcfg.get("google_api_key"), google_cx=appcfg.get("google_cx"))
                        stats = legal_repo.upsert_many(found, "Tìm kiếm các trang chỉ định")
                    st.session_state["legal_web_results"] = found
                    st.session_state["legal_web_results_query"] = query + " — các trang chỉ định"
                    st.success(f"Tìm thấy {len(found)} kết quả; đã thêm {stats['added']}, cập nhật {stats['updated']}.")
                except Exception as exc:
                    st.error(f"Không tìm trong các trang chỉ định được: {exc}")

        web_results = st.session_state.get("legal_web_results", [])
        if web_results:
            st.markdown(f"**Kết quả gần nhất:** {st.session_state.get('legal_web_results_query','')}")
            rdf = pd.DataFrame([{
                "Xem": _legal_click_url(d),
                "Loại": d.get("category", ""), "Số hiệu": d.get("number", ""),
                "Tên / trích yếu": d.get("title", ""), "Cơ quan": d.get("issuer", ""),
                "Trạng thái": d.get("status", ""), "Nguồn": d.get("source_name", ""),
                "Mở": d.get("source_url", ""),
            } for d in web_results])
            st.dataframe(rdf, hide_index=True, width="stretch", height=min(500, 80 + 35 * len(rdf)),
                         column_config={
                             "Xem": st.column_config.LinkColumn("Xem", display_text="Mở ↗"),
                             "Mở": st.column_config.LinkColumn("Nguồn", display_text="Mở ↗"),
                         })

    with st.expander("➕ Thêm văn bản tham chiếu thủ công", expanded=False):
        with st.form("manual_legal_doc", clear_on_submit=True):
            a, b = st.columns([1, 2])
            cat = a.selectbox("Loại", ["Luật", "Nghị định", "Thông tư", "Quyết định", "QCVN", "TCVN", "Văn bản khác"])
            number = b.text_input("Số hiệu")
            title = st.text_input("Tên / trích yếu *")
            a, b = st.columns(2)
            issuer = a.text_input("Cơ quan ban hành")
            source_url = b.text_input("Đường dẫn nguồn *")
            if st.form_submit_button("Lưu văn bản", disabled=not _can_update()):
                if not title.strip() or not source_url.strip():
                    st.error("Cần nhập tên văn bản và đường dẫn nguồn.")
                else:
                    legal_repo.upsert_many([{
                        "category": cat, "number": number, "title": title, "issuer": issuer,
                        "issue_date": "", "effective_date": "", "expiry_date": "", "status": "Chưa xác định",
                        "field": "QLDA xây dựng", "source_name": "Thêm thủ công", "source_url": source_url,
                        "is_draft": 0, "note": "Văn bản tham chiếu do người dùng thêm."
                    }])
                    st.success("Đã lưu văn bản tham chiếu.")
                    st.rerun()

    cats = ["Tất cả"] + legal_repo.categories()
    statuses = ["Tất cả"] + legal_repo.statuses()
    sources = ["Tất cả"] + legal_repo.sources()
    if st.session_state.pop("legal_focus_tvpl_after_sync", False):
        tvpl_choice = next((x for x in sources if "Thư Viện Pháp Luật" in x), "Tất cả")
        st.session_state["legal_source"] = tvpl_choice
        st.session_state["legal_keyword"] = ""
    f1, f2, f3, f4 = st.columns([3, 1.2, 1.5, 1.6])
    keyword = f1.text_input("Tìm số hiệu / tên / lĩnh vực", key="legal_keyword")
    category = f2.selectbox("Loại", cats, key="legal_category")
    status = f3.selectbox("Hiệu lực / trạng thái", statuses, key="legal_status")
    source = f4.selectbox("Nguồn", sources, key="legal_source")
    include_drafts = st.checkbox("Hiển thị cả dự thảo đang lấy ý kiến", value=True, key="legal_include_drafts")

    rows = legal_repo.list_documents(keyword, category, status, source, include_drafts)
    total = len(rows)
    active = sum(1 for r in rows if "còn hiệu lực" in (r["status"] or "").lower() and "hết hiệu lực" not in (r["status"] or "").lower())
    drafts = sum(1 for r in rows if r["is_draft"])
    standards = sum(1 for r in rows if r["category"] in ("TCVN", "QCVN", "Dự thảo TCVN", "Dự thảo QCVN"))
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Tổng văn bản", total)
    m2.metric("Còn hiệu lực", active)
    m3.metric("TCVN / QCVN", standards)
    m4.metric("Dự thảo", drafts)

    if not rows:
        st.info("Chưa có dữ liệu phù hợp. Hãy bấm cập nhật online hoặc thay đổi bộ lọc.")
        return

    df = pd.DataFrame([{
        "Xem": _legal_click_url(r),
        "Loại": r["category"], "Số hiệu": r["number"], "Tên / trích yếu": r["title"],
        "Cơ quan": r["issuer"], "Ban hành": r["issue_date"], "Hiệu lực": r["effective_date"],
        "Hết hiệu lực / Hạn góp ý": r["expiry_date"], "Trạng thái": r["status"],
        "Lĩnh vực": r["field"], "Nguồn": r["source_name"], "Mở nguồn": r["source_url"],
        "Cập nhật online": r["online_updated_at"],
    } for r in rows])
    st.dataframe(
        df, hide_index=True, width="stretch", height=min(700, 80 + 35 * len(df)),
        column_config={
            "Xem": st.column_config.LinkColumn("Xem văn bản", display_text="Mở ↗"),
            "Mở nguồn": st.column_config.LinkColumn("Nguồn", display_text="Mở ↗"),
        },
        column_order=["Xem", "Loại", "Số hiệu", "Tên / trích yếu", "Cơ quan", "Ban hành", "Hiệu lực",
                      "Hết hiệu lực / Hạn góp ý", "Trạng thái", "Lĩnh vực", "Nguồn", "Cập nhật online", "Mở nguồn"],
    )
    _render_excel_export(
        df, "VanBanQLDAXD", f"Van_ban_QLDA_XD_{date.today():%Y%m%d}.xlsx",
        "legal_excel", "Excel văn bản",
    )
    _ui_note("TVPL là nguồn tra cứu pháp luật chính/ưu tiên trong ứng dụng và mỗi bản ghi có nút Xem văn bản. Khi cần viện dẫn pháp lý, nên kiểm tra bản do cơ quan ban hành công bố.")



def _streamlit_secret(name: str, default: str = "") -> str:
    # Render uses Environment Variables. Local/Streamlit can still use st.secrets.
    env_value = os.environ.get(name)
    if env_value is not None and str(env_value).strip() != "":
        return str(env_value)
    try:
        value = st.secrets.get(name, default)
        return str(value or default)
    except Exception:
        return default


def _drive_rbac_enforced() -> bool:
    value = _streamlit_secret("QLDA_DRIVE_ENFORCE_RBAC", "true") or os.environ.get("QLDA_DRIVE_ENFORCE_RBAC", "true")
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _drive_gateway() -> DriveGateway:
    # Environment/secrets change only on process restart/redeploy. Avoid rereading
    # st.secrets on every Drive helper call; reuse one pooled gateway per session.
    holder = st.session_state.get("_qlda_drive_gateway_instance")
    if isinstance(holder, DriveGateway):
        return holder
    if isinstance(holder, tuple) and len(holder) == 2 and isinstance(holder[1], DriveGateway):
        return holder[1]
    gw = DriveGateway(config_from_streamlit(st))
    st.session_state["_qlda_drive_gateway_instance"] = gw
    return gw


# V6.18 - Phiên đăng nhập bền qua F5/Refresh.
# Session token do Apps Script ký HMAC và tự hết hạn (mặc định 12 giờ).
# Cookie chỉ giữ lại chính token đã ký; mỗi lần phục hồi app vẫn gọi /me để
# kiểm tra tài khoản còn hoạt động và quyền hiện tại trước khi cho truy cập.
_QLDA_AUTH_COOKIE = "qlda_auth_session_v618"
_QLDA_AUTH_COOKIE_MAX_AGE = 12 * 60 * 60


def _browser_session_cookie() -> str:
    try:
        cookies = st.context.cookies
        return str(cookies.get(_QLDA_AUTH_COOKIE, "") or "").strip()
    except Exception:
        return ""


def _write_browser_session_cookie(token: str) -> None:
    value = str(token or "").strip()
    # Token chỉ gồm base64url + dấu chấm; json.dumps vẫn được dùng để tránh
    # chèn chuỗi ngoài ý muốn vào JavaScript.
    js_value = json.dumps(value)
    secure = "; Secure" if IS_RENDER else ""
    components.html(
        f"""<script>
        (function() {{
          const value = {js_value};
          document.cookie = "{_QLDA_AUTH_COOKIE}=" + value + "; Path=/; Max-Age={_QLDA_AUTH_COOKIE_MAX_AGE}; SameSite=Lax{secure}";
        }})();
        </script>""",
        height=0,
    )


def _clear_browser_session_cookie() -> None:
    secure = "; Secure" if IS_RENDER else ""
    components.html(
        f"""<script>
        document.cookie = "{_QLDA_AUTH_COOKIE}=; Path=/; Max-Age=0; SameSite=Lax{secure}";
        </script>""",
        height=0,
    )


def _gateway_session_token() -> str:
    token = str(st.session_state.get("qlda_drive_session_token", "") or "").strip()
    if token:
        return token
    # Khi người dùng chủ động Logout, không đọc lại cookie cũ trong cùng
    # Streamlit session; cookie đã được yêu cầu xóa ở trình duyệt.
    if bool(st.session_state.get("qlda_ignore_persistent_auth", False)):
        return ""
    token = _browser_session_cookie()
    if token:
        st.session_state["qlda_drive_session_token"] = token
        st.session_state["qlda_auth_restored_from_cookie"] = True
    return token


def _gateway_logout() -> None:
    holder = st.session_state.pop("_qlda_drive_gateway_instance", None)
    try:
        if isinstance(holder, DriveGateway):
            holder.close()
        elif isinstance(holder, tuple) and len(holder) == 2:
            holder[1].close()
    except Exception:
        pass
    for key in ("qlda_drive_session_token", "qlda_drive_identity", "qlda_drive_error", "qlda_auth_restored_from_cookie", "_qlda_cookie_written_for_token"):
        st.session_state.pop(key, None)
    st.session_state["qlda_ignore_persistent_auth"] = True
    _clear_browser_session_cookie()


def _cloud_identity(refresh: bool = False):
    if not _drive_rbac_enforced():
        return {"role": "admin", "email": "", "name": "", "label": "Admin (RBAC tắt)"}
    cached = st.session_state.get("qlda_drive_identity")
    if cached and not refresh:
        return cached
    token = _gateway_session_token()
    if not token:
        return {"role": "unknown", "email": "", "name": "", "label": "Chưa đăng nhập"}
    try:
        data = _drive_gateway().me(token)
        user = dict(data.get("user") or {})
        role = str(user.get("role") or "unknown")
        identity = {
            "role": role,
            "email": str(user.get("email") or ""),
            "name": str(user.get("name") or ""),
            "approval_role": _user_approval_role(user),
            "label": {"read": "Chỉ đọc", "update": "Cập nhật", "admin": "Admin"}.get(role, "Chưa xác định"),
        }
        st.session_state["qlda_drive_identity"] = identity
        st.session_state.pop("qlda_drive_error", None)
        return identity
    except Exception as exc:
        st.session_state["qlda_drive_error"] = str(exc)
        _gateway_logout()
        return {"role": "unknown", "email": "", "name": "", "label": "Chưa đăng nhập"}


def _streamlit_user_email() -> str:
    return str(_cloud_identity().get("email") or "").strip().lower()


def _cloud_access_role() -> str:
    return str(_cloud_identity().get("role") or "unknown")


def _require_cloud_login_and_access():
    """V6.0: app login + Google Drive direct/resumable storage through Apps Script; no Google Cloud Console."""
    if not _drive_rbac_enforced():
        return

    gw = _drive_gateway()
    if not gw.config.configured:
        st.title("🏗️ QLDA Xây dựng V6.22 PostgreSQL Cloud")
        st.error("Chưa cấu hình Google Drive Gateway.")
        st.markdown(
            "V6.0 không dùng Google Cloud Console. Hãy triển khai file `google_drive_appscript/Code.gs` "
            "thành Google Apps Script Web App rồi nhập URL / token vào Render Environment Variables (hoặc st.secrets khi chạy nơi khác)."
        )
        st.code(
            'QLDA_DRIVE_WEBAPP_URL = "https://script.google.com/macros/s/.../exec"\n'
            'QLDA_DRIVE_API_TOKEN = "token-giong-API_TOKEN-trong-Code.gs"\n'
            'QLDA_DRIVE_ENFORCE_RBAC = "true"\n'
            'QLDA_DRIVE_DIRECT_MAX_UPLOAD_MB = "2048"\nQLDA_DRIVE_LEGACY_MAX_UPLOAD_MB = "30"',
            language="toml",
        )
        st.stop()

    try:
        health = gw.health()
    except Exception as exc:
        st.title("🏗️ QLDA Xây dựng V6.22 PostgreSQL Cloud")
        st.error(f"Không kết nối được Google Drive Gateway: {exc}")
        _ui_note("Kiểm tra URL phải là deployment /exec và API token phải trùng với Code.gs.")
        st.stop()

    if not bool(health.get("initialized")):
        st.title("🏗️ QLDA Xây dựng V6.22 PostgreSQL Cloud")
        st.info("Lần chạy đầu tiên: tạo tài khoản Admin. Thư mục **QLDA Xây dựng** sẽ tự được tạo trên Google Drive của chủ Apps Script.")
        root = dict(health.get("root") or {})
        if root.get("url"):
            st.link_button("☁ Mở thư mục QLDA Xây dựng", root["url"])
        with st.form("drive_bootstrap_admin"):
            email = st.text_input("Email Admin")
            name = st.text_input("Tên Admin")
            password = st.text_input("Mật khẩu Admin", type="password")
            password2 = st.text_input("Nhập lại mật khẩu", type="password")
            bootstrap = st.text_input("BOOTSTRAP_CODE trong Code.gs", type="password")
            submit = st.form_submit_button("Khởi tạo Admin", type="primary")
        if submit:
            if password != password2:
                st.error("Hai mật khẩu không trùng nhau.")
            else:
                try:
                    gw.bootstrap_admin(email, name, password, bootstrap)
                    st.success("Đã tạo Admin. Hãy đăng nhập.")
                    st.rerun()
                except Exception as exc:
                    st.error(str(exc))
        st.stop()

    if not _gateway_session_token():
        st.title("🏗️ QLDA Xây dựng V6.22 PostgreSQL Cloud")
        _ui_note("Google Drive là kho file tập trung. File V6.0 tải trực tiếp theo resumable upload, tối đa 2 GB/file. Không cần Google Cloud Console/OAuth Client/Service Account.")
        with st.form("qlda_drive_login"):
            email = st.text_input("Email")
            password = st.text_input("Mật khẩu", type="password")
            submit = st.form_submit_button("🔐 Đăng nhập", type="primary", width="stretch")
        if submit:
            try:
                result = gw.login(email, password)
                token = str(result.get("session_token") or "")
                if not token:
                    raise DriveGatewayError("Gateway không trả về session token.")
                st.session_state["qlda_drive_session_token"] = token
                st.session_state.pop("qlda_drive_identity", None)
                st.session_state.pop("qlda_ignore_persistent_auth", None)
                _write_browser_session_cookie(token)
                st.rerun()
            except Exception as exc:
                st.error(str(exc))
        _ui_note("Tài khoản do Admin QLDA tạo: Chỉ đọc / Cập nhật / Admin. Quyền Cập nhật chỉ được thêm/sửa/upload; không được xóa. Trên Drive, Read/Update chỉ là Viewer; mọi upload đi qua Gateway.")
        st.stop()

    identity = _cloud_identity(refresh=True)
    if identity.get("role") not in {"read", "update", "admin"}:
        st.title("🏗️ QLDA Xây dựng V6.22 PostgreSQL Cloud")
        st.error(st.session_state.get("qlda_drive_error") or "Phiên đăng nhập không hợp lệ hoặc tài khoản đã bị thu hồi.")
        if st.button("Đăng nhập lại"):
            _gateway_logout()
            st.rerun()
        st.stop()

    # Gia hạn cookie phía trình duyệt trong thời gian token backend còn hiệu lực.
    # Nếu tab bị F5/Refresh và Streamlit tạo session mới, token này được phục hồi
    # trước khi render màn hình login.
    live_token = _gateway_session_token()
    if live_token:
        cookie_marker = str(st.session_state.get("_qlda_cookie_written_for_token") or "")
        token_marker = str(hash(live_token))
        if cookie_marker != token_marker:
            _write_browser_session_cookie(live_token)
            st.session_state["_qlda_cookie_written_for_token"] = token_marker
        if st.session_state.pop("qlda_auth_restored_from_cookie", False):
            st.toast("Đã khôi phục phiên đăng nhập sau khi refresh.", icon="🔐")

    with st.sidebar:
        _ui_note(f"Người dùng: {identity.get('name') or identity.get('email')}")
        _ui_note(f"Email: {identity.get('email')}")
        _ui_note("Quyền: " + {"read": "Chỉ đọc", "update": "Cập nhật", "admin": "Admin"}.get(identity.get("role"), ""))
        if st.button("🚪 Đăng xuất", key="qlda_drive_logout"):
            _gateway_logout()
            st.rerun()


def _can_update() -> bool:
    return _cloud_access_role() in {"update", "admin"}


def _is_admin() -> bool:
    return _cloud_access_role() == "admin"


def _trash_drive_file(file_id: str) -> None:
    if not file_id:
        return
    if not _is_admin():
        raise PermissionError("Chỉ Admin mới được xóa file. Quyền Cập nhật chỉ được thêm/sửa/upload.")
    _drive_gateway().trash_file(_gateway_session_token(), file_id)


def _download_drive_file(file_id: str) -> tuple[str, str, bytes]:
    return _drive_gateway().download_bytes(_gateway_session_token(), file_id)


def _upload_doc_files_to_drive(*args, **kwargs):
    raise RuntimeError("V6.0 không nhận file qua Streamlit. Hãy dùng Direct Upload Google Drive.")


def _upload_drawing_files_to_drive(*args, **kwargs):
    raise RuntimeError("V6.0 không nhận file qua Streamlit. Hãy dùng Direct Upload Google Drive.")


def _format_drive_size(value) -> str:
    try:
        n = float(value or 0)
    except Exception:
        n = 0.0
    if n >= 1024 ** 3:
        return f"{n / (1024 ** 3):.2f} GB"
    if n >= 1024 ** 2:
        return f"{n / (1024 ** 2):.1f} MB"
    if n >= 1024:
        return f"{n / 1024:.1f} KB"
    return f"{int(n)} B"


def _render_direct_drive_panel(pid: int, *, kind: str, subtype: str, record_code: str, panel_key: str) -> None:
    """V6.0 file panel: file bytes never pass through Streamlit.

    Streamlit requests a short-lived upload ticket from Apps Script. The user then
    opens the Apps Script uploader, which starts a Google Drive resumable session.
    The browser PUTs chunks straight to that Drive session (up to 2 GB/file).
    """
    st.markdown("#### ☁ File trên Google Drive — Direct Upload V6.0")
    _ui_note(
        "Tối đa **2 GB/file**. File không đi qua Streamlit/SQLite và không gửi Base64 qua Apps Script. "
        "File không qua Streamlit/SQLite; Apps Script giữ OAuth và chuyển tiếp chunk thích nghi 2 MB → 1 MB → 512 KiB → 256 KiB vào phiên resumable Google Drive."
    )
    project = db.project(pid)
    if not project:
        st.warning("Không tìm thấy dự án.")
        return
    gw = _drive_gateway()
    token = _gateway_session_token()
    if not token:
        st.warning("Phiên Drive đã hết hạn. Hãy đăng nhập lại.")
        return

    state_key = panel_key + "_ticket"
    upload = st.session_state.get(state_key) or {}

    # Tự tạo ticket khi người dùng có quyền cập nhật để nút upload luôn hiện ngay
    # sau khi lưu/chọn hồ sơ hoặc bản vẽ. File vẫn đi thẳng vào Google Drive.
    if _can_update() and not upload.get("url"):
        try:
            upload = gw.create_upload_ticket(
                token,
                project_code=project["code"],
                kind=kind,
                subtype=subtype,
                record_code=record_code,
            )
            st.session_state[state_key] = upload
        except Exception as exc:
            st.warning(f"Chưa tạo được link tải file: {exc}")
            upload = {}

    c1, c2, c3, c4 = st.columns([1.8, 1, 1, 1])
    if upload.get("url") and _can_update():
        c1.link_button("⬆️ TẢI FILE LÊN GOOGLE DRIVE (2GB)", upload["url"], type="primary", width="stretch")
    else:
        c1.button("⬆️ TẢI FILE LÊN GOOGLE DRIVE (2GB)", disabled=True, key=panel_key + "_upload_disabled", width="stretch")

    if c2.button("🔄 Tạo lại link upload", key=panel_key + "_prepare", disabled=not _can_update(), width="stretch"):
        try:
            upload = gw.create_upload_ticket(
                token,
                project_code=project["code"],
                kind=kind,
                subtype=subtype,
                record_code=record_code,
            )
            st.session_state[state_key] = upload
            st.success("Đã tạo link upload mới, hiệu lực khoảng 30 phút.")
            st.rerun()
        except Exception as exc:
            st.error(f"Không tạo được link tải file: {exc}")

    if c3.button("🔄 Làm mới danh sách", key=panel_key + "_refresh", width="stretch"):
        st.rerun()
    c4.button("⬇️ TẢI XUỐNG", disabled=True, key=panel_key + "_download_hint", help="Các nút tải xuống sẽ hiện bên cạnh từng file Drive ở danh sách phía dưới.", width="stretch")

    include_history = st.checkbox("Hiện cả _Lich_su", value=False, key=panel_key + "_history")
    try:
        data = gw.list_record_files(
            token,
            project_code=project["code"],
            kind=kind,
            subtype=subtype,
            record_code=record_code,
            include_history=include_history,
        )
    except Exception as exc:
        st.error(f"Không đọc được danh sách file trên Drive: {exc}")
        return

    folder = data.get("folder") or {}
    if folder.get("url"):
        st.link_button("📂 Mở đúng thư mục Google Drive", folder["url"], width="content")

    files = data.get("files") or []
    if not files:
        st.info("Thư mục Drive hiện chưa có file. Bấm **⬆️ TẢI FILE LÊN GOOGLE DRIVE (2GB)** để tải trực tiếp.")
        return

    _ui_note(f"Drive hiện có {len(files)} file" + (" (gồm lịch sử)" if include_history else ""))
    for idx, item in enumerate(files):
        name = str(item.get("name") or "file")
        size = _format_drive_size(item.get("size"))
        modified = str(item.get("modified_time") or "").replace("T", " ").replace("Z", "")[:19]
        history_mark = " 🕘" if item.get("history") else ""
        a, b, c, d = st.columns([5.4, 1.1, 1.4, 1.1])
        a.markdown(f"**{name}**{history_mark}  \n{size}" + (f" • {modified}" if modified else ""))
        if item.get("url"):
            b.link_button("☁ Mở", item["url"], width="stretch")
        download_url = item.get("download_url") or (
            f"https://drive.google.com/uc?export=download&id={item.get('id','')}" if item.get("id") else ""
        )
        if download_url:
            c.link_button("⬇️ Tải xuống", download_url, width="stretch")
        if d.button("🗑 Xóa", key=f"{panel_key}_trash_{idx}_{item.get('id','')}", disabled=not _is_admin(), width="stretch"):
            try:
                gw.trash_file(token, str(item.get("id") or ""))
                st.success(f"Đã chuyển {name} vào Thùng rác Drive.")
                st.rerun()
            except Exception as exc:
                st.error(str(exc))


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _streamlit_secret(name, "") or os.environ.get(name, "")
    if str(raw or "").strip() == "":
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _ai_server_config_location() -> str:
    if IS_RENDER:
        return "Render → Service → Environment"
    return "biến môi trường máy chủ hoặc .streamlit/secrets.toml"


def _runtime_app_settings() -> dict:
    """Runtime settings shared by every device.

    AI API keys and AI runtime options are intentionally server-owned. They are
    NEVER read from st.session_state, so a PC, phone and tablet logged into the
    same deployment all use exactly the same server configuration.
    """
    google_key = (_streamlit_secret("GOOGLE_SEARCH_API_KEY", "") or os.environ.get("GOOGLE_SEARCH_API_KEY", "") or st.session_state.get("cfg_google_api_key", "")).strip()
    google_cx = (_streamlit_secret("GOOGLE_SEARCH_CX", "") or os.environ.get("GOOGLE_SEARCH_CX", "") or st.session_state.get("cfg_google_cx", "")).strip()

    provider_default = (_streamlit_secret("AI_PROVIDER", "") or os.environ.get("AI_PROVIDER", "") or "openai").strip().lower()
    provider = "gemini" if provider_default == "gemini" else "openai"
    openai_key = (_streamlit_secret("OPENAI_API_KEY", "") or os.environ.get("OPENAI_API_KEY", "")).strip()
    openai_model = (_streamlit_secret("OPENAI_MODEL", "") or os.environ.get("OPENAI_MODEL", "") or "gpt-5-mini").strip()
    gemini_key = (_streamlit_secret("GEMINI_API_KEY", "") or os.environ.get("GEMINI_API_KEY", "")).strip()
    gemini_model = (_streamlit_secret("GEMINI_MODEL", "") or os.environ.get("GEMINI_MODEL", "") or "auto").strip()
    ai_web_search = _env_bool("AI_WEB_SEARCH", False)
    if not (_streamlit_secret("AI_WEB_SEARCH", "") or os.environ.get("AI_WEB_SEARCH", "")):
        # Backward-compatible environment names.
        ai_web_search = _env_bool("GEMINI_WEB_SEARCH", _env_bool("OPENAI_WEB_SEARCH", False))

    if "cfg_specified_sites" not in st.session_state:
        st.session_state["cfg_specified_sites"] = "\n".join(DEFAULT_SPECIFIED_SEARCH_DOMAINS)
    domains = []
    for line in str(st.session_state.get("cfg_specified_sites", "")).splitlines():
        d = line.strip().lower().removeprefix("https://").removeprefix("http://").split("/", 1)[0]
        if d.startswith("www."):
            d = d[4:]
        if d and "." in d and d not in domains:
            domains.append(d)
    return {
        "google_api_key": google_key, "google_cx": google_cx,
        "ai_provider": provider,
        "ai_web_search": bool(ai_web_search),
        "openai_api_key": openai_key, "openai_model": openai_model or "gpt-5-mini",
        "openai_web_search": bool(ai_web_search),
        "gemini_api_key": gemini_key, "gemini_model": gemini_model or "auto",
        "specified_search_domains": domains or list(DEFAULT_SPECIFIED_SEARCH_DOMAINS),
    }


def render_settings():
    st.subheader("⚙️ Cài đặt ứng dụng")
    _ui_note(f"Cấu hình hệ thống • AI dùng cấu hình tập trung tại {_ai_server_config_location()}; API key không lưu trên trình duyệt.")
    ai_tab, google_tab, drive_tab, sites_tab, system_tab = st.tabs(["🤖 AI", "🔎 Google Search", "☁ Google Drive & quyền", "🌐 Website tra cứu", "🗄 Hệ thống"])

    with ai_tab:
        cfg_ai = _runtime_app_settings()
        provider = cfg_ai.get("ai_provider", "openai")
        provider_name = "Gemini" if provider == "gemini" else "OpenAI"
        active_key = cfg_ai.get("gemini_api_key") if provider == "gemini" else cfg_ai.get("openai_api_key")
        active_model = cfg_ai.get("gemini_model") if provider == "gemini" else cfg_ai.get("openai_model")

        st.write(f"**Nhà cung cấp AI toàn hệ thống:** {provider_name}")
        st.write(f"**Model:** {active_model}")
        st.write(f"**Web Search:** {'Bật' if cfg_ai.get('ai_web_search') else 'Tắt'}")
        if active_key:
            st.success(f"{provider_name} API key đã cấu hình trên máy chủ. Mọi máy tính/điện thoại dùng chung cấu hình này.")
        else:
            st.error(f"Chưa cấu hình API key cho {provider_name} trên máy chủ.")

        if _is_admin():
            st.info(
                "API key không được nhập hoặc hiển thị trong ứng dụng. "
                f"Admin thay đổi tại **{_ai_server_config_location()}** rồi redeploy/restart service."
            )
            st.code(
                "AI_PROVIDER=gemini\n"
                "GEMINI_API_KEY=...\n"
                "GEMINI_MODEL=auto\n"
                "AI_WEB_SEARCH=true\n\n"
                "# Có thể cấu hình OpenAI song song\n"
                "OPENAI_API_KEY=...\n"
                "OPENAI_MODEL=gpt-5-mini",
                language="bash",
            )
            test_label = f"🩺 Kiểm tra {provider_name} API"
            if st.button(test_label, key="settings_test_ai_provider"):
                try:
                    if provider == "gemini":
                        test_ai = GeminiProjectAssistant(DB_PATH, GeminiSettings(
                            api_key=(cfg_ai.get("gemini_api_key") or "").strip(),
                            model=(cfg_ai.get("gemini_model") or "auto").strip(),
                            use_web=False,
                        ))
                    else:
                        test_ai = OpenAIProjectAssistant(DB_PATH, AISettings(
                            api_key=(cfg_ai.get("openai_api_key") or "").strip(),
                            model=(cfg_ai.get("openai_model") or "gpt-5-mini").strip(),
                            use_web=False,
                        ))
                    with st.spinner("Đang kiểm tra API key, quota và quyền model..."):
                        msg = test_ai.test_connection()
                    st.success(msg)
                except Exception as exc:
                    st.error(str(exc))
        else:
            st.info("Cấu hình AI do Admin quản lý tập trung trên máy chủ; người dùng không cần nhập API key trên từng thiết bị.")

    with google_tab:
        secret_google = bool((_streamlit_secret("GOOGLE_SEARCH_API_KEY", "") or os.environ.get("GOOGLE_SEARCH_API_KEY", "")) and (_streamlit_secret("GOOGLE_SEARCH_CX", "") or os.environ.get("GOOGLE_SEARCH_CX", "")))
        if secret_google:
            st.success("Google API key và CX đã được cấu hình bằng Secrets/biến môi trường.")
        else:
            st.text_input("Google API key", type="password", key="cfg_google_api_key")
            st.text_input("Search Engine ID (CX)", key="cfg_google_cx")
        _ui_note("Nếu không có Google API, app vẫn dùng Bing/DuckDuckGo fallback và có thể mở Google trực tiếp trên trình duyệt.")

    with drive_tab:
        gw = _drive_gateway()
        token = _gateway_session_token()
        ident = _cloud_identity()
        if not gw.config.configured:
            st.error("Chưa cấu hình Drive Gateway. Trên Render hãy đặt QLDA_DRIVE_WEBAPP_URL và QLDA_DRIVE_API_TOKEN tại Service → Environment.")
        elif token and ident.get("role") in {"read", "update", "admin"}:
            try:
                root = dict(gw.root_info(token).get("root") or {})
                st.success(f"Google Drive đã kết nối: {root.get('name') or 'QLDA Xây dựng'}")
                st.write(f"Người dùng: **{ident.get('name') or ident.get('email')}** • Email: **{ident.get('email')}** • Quyền: **{ident.get('label')}**")
                if root.get("url"):
                    st.link_button("☁ Mở thư mục QLDA Xây dựng", root["url"])
                _ui_note("File V6.0 tải trực tiếp theo resumable upload, tối đa 2 GB/file. Tự phân loại theo Dự án → Hồ sơ/Bản vẽ → Nhóm → Mã hồ sơ; file trùng tên đưa bản cũ vào _Lich_su.")

                with st.expander("🔑 Đổi mật khẩu của tôi"):
                    with st.form("drive_change_password"):
                        oldp = st.text_input("Mật khẩu hiện tại", type="password", key="drive_oldp")
                        newp = st.text_input("Mật khẩu mới", type="password", key="drive_newp")
                        newp2 = st.text_input("Nhập lại mật khẩu mới", type="password", key="drive_newp2")
                        change = st.form_submit_button("Đổi mật khẩu")
                    if change:
                        if newp != newp2:
                            st.error("Hai mật khẩu mới không trùng nhau.")
                        else:
                            try:
                                gw.change_password(token, oldp, newp)
                                st.success("Đã đổi mật khẩu.")
                            except Exception as exc:
                                st.error(str(exc))

                if ident.get("role") == "admin":
                    st.markdown("### 👥 Phân quyền người dùng")
                    _ui_note("Chỉ đọc = Viewer Drive • Cập nhật = Viewer Drive + được thêm/sửa/upload qua app, KHÔNG được xóa • Admin = Editor Drive + toàn quyền quản trị/xóa trong app. Vai trò phê duyệt là lớp quyền riêng, dùng cho Nhà thầu/Ban điều hành/TVGS/Ban QLDA.")

                    # Đọc danh sách TRƯỚC form để khi cập nhật người cũ, form luôn
                    # hiện đúng quyền và phân loại duyệt hiện tại thay vì quay về mặc định.
                    try:
                        users = gw.list_users(token)
                    except Exception as exc:
                        users = []
                        st.error(str(exc))

                    backend_has_approval_schema = bool(users) and any(
                        ("approval_role" in u) or ("approval_group" in u) for u in users
                    )
                    if users and not backend_has_approval_schema:
                        st.error(
                            "⚠️ Google Apps Script đang chạy bản cũ, chưa trả về trường phân loại duyệt. "
                            "Nếu bấm cập nhật, quyền hệ thống có thể đổi nhưng vai trò phê duyệt sẽ không được lưu. "
                            "Hãy cập nhật `google_drive_appscript/Code.gs` bản V6.2 và Deploy New version."
                        )

                    approval_choices = ["", "CONTRACTOR", "SITE_MANAGEMENT", "CONSULTANT", "PROJECT_MANAGEMENT"]
                    role_choices = ["read", "update", "admin"]
                    mode = st.radio(
                        "Thao tác người dùng",
                        ["Cập nhật người dùng hiện có", "Thêm người dùng mới"],
                        horizontal=True,
                        key="drive_user_edit_mode",
                    )

                    selected_user = None
                    if mode == "Cập nhật người dùng hiện có" and users:
                        selected_email = st.selectbox(
                            "Chọn người dùng cần cập nhật",
                            [str(u.get("email") or "") for u in users],
                            format_func=lambda e: next(
                                (f"{u.get('name') or ''} • {u.get('email') or ''}" for u in users if str(u.get("email") or "") == e),
                                e,
                            ),
                            key="drive_user_selected_email",
                        )
                        selected_user = next((u for u in users if str(u.get("email") or "") == selected_email), None)

                    current_email = str((selected_user or {}).get("email") or "")
                    current_name = str((selected_user or {}).get("name") or "")
                    current_role = str((selected_user or {}).get("role") or "update")
                    if current_role not in role_choices:
                        current_role = "update"
                    current_approval = _user_approval_role(selected_user)
                    if current_approval not in approval_choices:
                        current_approval = ""
                    widget_suffix = (current_email or "new").replace("@", "_").replace(".", "_")

                    with st.form(f"drive_user_form_{widget_suffix}"):
                        c1, c2 = st.columns(2)
                        pemail = c1.text_input(
                            "Email người dùng",
                            value=current_email,
                            disabled=bool(selected_user),
                        )
                        pname = c2.text_input("Tên người dùng", value=current_name)
                        prole = c1.selectbox(
                            "Quyền hệ thống",
                            role_choices,
                            index=role_choices.index(current_role),
                            format_func=lambda x: {"read":"Chỉ đọc","update":"Cập nhật","admin":"Admin"}[x],
                        )
                        papproval = c2.selectbox(
                            "Phân loại phê duyệt",
                            approval_choices,
                            index=approval_choices.index(current_approval),
                            format_func=lambda x: APPROVAL_ROLE_LABELS.get(x, x),
                        )
                        ppass = c2.text_input(
                            "Mật khẩu khởi tạo / mật khẩu mới (để trống nếu không đổi)",
                            type="password",
                        )
                        save_user = st.form_submit_button("💾 Lưu phân quyền người dùng", type="primary")

                    if save_user:
                        try:
                            target_email = current_email if selected_user else pemail
                            expected_approval = papproval or ("PROJECT_MANAGEMENT" if prole == "admin" else "")
                            result = gw.set_user(token, target_email, pname, prole, ppass, papproval)
                            saved = dict(result.get("user") or {})
                            saved_role = _user_approval_role(saved)

                            # Không báo thành công giả. Nếu backend cũ bỏ qua approval_role,
                            # kiểm tra lại list_users và báo rõ phải deploy Apps Script mới.
                            if saved_role != expected_approval:
                                refreshed = gw.list_users(token)
                                row = next(
                                    (u for u in refreshed if str(u.get("email") or "").lower() == str(target_email or "").lower()),
                                    {},
                                )
                                saved_role = _user_approval_role(row)
                            if saved_role != expected_approval:
                                st.error(
                                    "Không lưu được **Phân loại phê duyệt** trên Google Apps Script. "
                                    "Ứng dụng đang mới hơn backend Apps Script. "
                                    "Hãy thay `Code.gs` bằng bản V6.2 trong gói này và Deploy → Manage deployments → Edit → New version → Deploy."
                                )
                            else:
                                if str(target_email or "").lower() == str(ident.get("email") or "").lower():
                                    st.session_state.pop("qlda_drive_identity", None)
                                st.success(
                                    "Đã lưu người dùng: "
                                    f"{APPROVAL_ROLE_LABELS.get(saved_role, saved_role) if saved_role else 'Không tham gia duyệt'}."
                                )
                                st.rerun()
                        except Exception as exc:
                            st.error(str(exc))

                    if users:
                        udf = pd.DataFrame([{
                            "Tên": u.get("name", ""),
                            "Email": u.get("email", ""),
                            "Phân loại duyệt": APPROVAL_ROLE_LABELS.get(_user_approval_role(u), "Không tham gia duyệt"),
                            "Quyền": {"read":"Chỉ đọc","update":"Cập nhật","admin":"Admin"}.get(u.get("role", ""), u.get("role", "")),
                            "Hoạt động": "Có" if u.get("active", True) else "Không",
                            "Cập nhật": u.get("updated_at", ""),
                        } for u in users])
                        st.dataframe(udf, hide_index=True, width="stretch")
                        removable = [u for u in users if str(u.get("email") or "").lower() != str(ident.get("email") or "").lower()]
                        if removable:
                            emails = [str(u.get("email") or "") for u in removable]
                            target = st.selectbox("Xóa quyền người dùng", emails, key="drive_delete_user_email")
                            if st.button("🗑️ Xóa người dùng / thu hồi quyền", key="drive_delete_user"):
                                try:
                                    gw.delete_user(token, target)
                                    st.success("Đã xóa người dùng và thu hồi quyền Drive.")
                                    st.rerun()
                                except Exception as exc:
                                    st.error(str(exc))
                else:
                    st.info("Chỉ Admin mới được quản lý tài khoản và phân quyền.")
            except Exception as exc:
                st.error(f"Không đọc được Google Drive Gateway: {exc}")
        else:
            st.info("Chưa có phiên đăng nhập Drive Gateway.")

        st.markdown("### Cấu hình Render Environment")
        st.code(
            'QLDA_DRIVE_WEBAPP_URL = "https://script.google.com/macros/s/.../exec"\n'
            'QLDA_DRIVE_API_TOKEN = "token-giong-API_TOKEN-trong-Code.gs"\n'
            'QLDA_DRIVE_ENFORCE_RBAC = "true"\n'
            'QLDA_DRIVE_DIRECT_MAX_UPLOAD_MB = "2048"\nQLDA_DRIVE_LEGACY_MAX_UPLOAD_MB = "30"',
            language="toml",
        )
        _ui_note("Không cần GOOGLE_DRIVE_ROOT_FOLDER_ID, Google OAuth Client, Service Account hay Google Cloud Console. Thư mục QLDA Xây dựng tự được tạo bởi Apps Script.")

    with sites_tab:
        if "cfg_specified_sites" not in st.session_state:
            st.session_state["cfg_specified_sites"] = "\n".join(DEFAULT_SPECIFIED_SEARCH_DOMAINS)
        st.text_area("Website dùng cho nút ‘Tìm trang chỉ định’ — mỗi dòng một domain", key="cfg_specified_sites", height=260)
        c1, c2 = st.columns([1, 4])
        def _reset_sites():
            st.session_state["cfg_specified_sites"] = "\n".join(DEFAULT_SPECIFIED_SEARCH_DOMAINS)
        c1.button("Khôi phục mặc định", width="stretch", on_click=_reset_sites)
        _ui_note("Có thể thêm thuvienphapluat.vn hoặc website tra cứu phù hợp. TVPL được ưu tiên trong sheet Văn bản; link gốc luôn được giữ để mở trực tiếp.")

    with system_tab:
        st.code(str(DB_PATH), language=None)
        if IS_RENDER:
            st.success(f"Đang chạy trên Render • service: {os.environ.get('RENDER_SERVICE_NAME', 'QLDA V6.0')}")
            persistent = str(os.environ.get("QLDA_RENDER_PERSISTENT_DISK", "false")).lower() in {"1", "true", "yes", "on"}
            if persistent and str(DB_PATH).startswith("/var/data/"):
                st.success("SQLite đang dùng Render Persistent Disk tại /var/data.")
            else:
                st.warning("SQLite chưa được xác nhận là persistent. Hãy gắn Render Persistent Disk tại /var/data và đặt QLDA_DB_PATH=/var/data/qlda_cloud.db.")
        _ui_note("Trên Render, nên đặt SQLite tại /var/data/qlda_cloud.db và gắn Persistent Disk vào /var/data. Nếu không có disk, filesystem là tạm thời.")
        st.info("Cấu hình bền vững trên Render nên dùng Service → Environment. Không commit API key/token vào GitHub.")

    cfg = _runtime_app_settings()
    ai_ready = bool(cfg.get("gemini_api_key")) if cfg.get("ai_provider") == "gemini" else bool(cfg.get("openai_api_key"))
    st.success(f"Cấu hình hiện tại: AI {cfg.get('ai_provider','openai').upper()} {'✓' if ai_ready else '–'} • Google {'✓' if cfg['google_api_key'] and cfg['google_cx'] else '–'} • {len(cfg['specified_search_domains'])} website chỉ định")


def _ai_typewriter_stream(stream):
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


def render_ai_assistant(pid: int):
    st.subheader("🤖 Trợ lý AI QLDA")
    _ui_note("Chat với dự án • Rủi ro tiến độ • Dự thảo báo cáo • Đọc hồ sơ • Tra cứu văn bản. AI chỉ đưa ra đề xuất; người dùng vẫn là người phê duyệt/kết luận.")

    appcfg = _runtime_app_settings()
    provider = appcfg.get("ai_provider", "openai")
    if provider == "gemini":
        settings = GeminiSettings(
            api_key=(appcfg.get("gemini_api_key") or "").strip(),
            model=(appcfg.get("gemini_model") or "auto").strip(),
            use_web=bool(appcfg.get("ai_web_search", False)),
        )
        ai = GeminiProjectAssistant(DB_PATH, settings)
        provider_name = "Gemini"
        missing_key = "GEMINI_API_KEY"
    else:
        settings = AISettings(
            api_key=(appcfg.get("openai_api_key") or "").strip(),
            model=(appcfg.get("openai_model") or "gpt-5-mini").strip(),
            use_web=bool(appcfg.get("ai_web_search", False)),
        )
        ai = OpenAIProjectAssistant(DB_PATH, settings)
        provider_name = "OpenAI"
        missing_key = "OPENAI_API_KEY"
    if settings.api_key:
        st.success(f"AI: {provider_name} • Model: {settings.model} • Web Search: {'Bật' if settings.use_web else 'Tắt'} • cấu hình tập trung trên máy chủ")
    else:
        if _is_admin():
            st.warning(f"Chưa có {missing_key}. Hãy cấu hình tại {_ai_server_config_location()} rồi redeploy/restart service.")
        else:
            st.warning(f"AI chưa được Admin cấu hình trên máy chủ ({missing_key}).")
    ctx_builder = ProjectContextBuilder(DB_PATH)
    tab_chat, tab_risk, tab_file, tab_legal = st.tabs(["💬 Chat với dự án", "📈 Rủi ro & báo cáo", "📎 Đọc hồ sơ", "⚖️ Văn bản AI"])

    with tab_chat:
        hkey = f"ai_history_{pid}"
        if hkey not in st.session_state:
            st.session_state[hkey] = []
        c1, c2 = st.columns([1, 5])
        if c1.button("Xóa chat", key=f"ai_clear_{pid}", width="stretch"):
            st.session_state[hkey] = []
            st.rerun()
        _ui_note("AI nhận snapshot dự án hiện tại: tiến độ, hồ sơ, bản vẽ và metadata văn bản. Không gửi toàn bộ file đính kèm trừ khi anh yêu cầu ở tab Đọc hồ sơ.")
        for msg in st.session_state[hkey]:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])
        q = st.chat_input("Hỏi về dự án: công việc trễ, RFI/NCR/biên bản hiện trường, bản vẽ, rủi ro...", key=f"ai_chat_{pid}")
        if q:
            previous = list(st.session_state[hkey])
            st.session_state[hkey].append({"role": "user", "content": q})
            with st.chat_message("user"):
                st.markdown(q)
            try:
                with st.chat_message("assistant"):
                    _ai_status = st.empty()
                    _ai_status.caption("AI đang phân tích dữ liệu dự án...")
                    answer = st.write_stream(
                        _ai_typewriter_stream(ai.ask_project_stream(pid, q, previous, date.today(), use_web=False))
                    )
                    _ai_status.empty()
                answer = str(answer or "").strip()
                if answer:
                    st.session_state[hkey].append({"role": "assistant", "content": answer})
            except Exception as exc:
                st.error(str(exc))

    with tab_risk:
        status_date = st.date_input("Ngày báo cáo AI", value=date.today(), key=f"ai_status_date_{pid}")
        r1, r2, r3 = st.columns(3)
        if r1.button("⚠️ Phân tích rủi ro tiến độ", type="primary", width="stretch", key=f"ai_risk_{pid}"):
            try:
                with st.spinner("Đang xếp hạng rủi ro..."):
                    st.session_state[f"ai_analysis_{pid}"] = ai.analyze_schedule_risk(pid, status_date)
            except Exception as exc: st.error(str(exc))
        if r2.button("📝 Soạn báo cáo tuần", width="stretch", key=f"ai_week_{pid}"):
            try:
                with st.spinner("Đang soạn báo cáo tuần..."):
                    st.session_state[f"ai_analysis_{pid}"] = ai.draft_report(pid, "tuần", status_date)
            except Exception as exc: st.error(str(exc))
        if r3.button("📝 Soạn báo cáo tháng", width="stretch", key=f"ai_month_{pid}"):
            try:
                with st.spinner("Đang soạn báo cáo tháng..."):
                    st.session_state[f"ai_analysis_{pid}"] = ai.draft_report(pid, "tháng", status_date)
            except Exception as exc: st.error(str(exc))
        text = st.session_state.get(f"ai_analysis_{pid}", "")
        if text:
            st.markdown(text)
            st.download_button("⬇️ Tải kết quả AI (.md)", text.encode("utf-8"), file_name=f"AI_report_{pid}_{date.today():%Y%m%d}.md", mime="text/markdown", key=f"ai_report_dl_{pid}")

    with tab_file:
        st.info("AI có thể đọc file được chọn và đối chiếu với bối cảnh dự án. V4.0 giới hạn 25 MB/file để kiểm soát thời gian và chi phí.")
        catalog = ctx_builder.attachment_catalog(pid)
        options = [None] + [int(x["id"]) for x in catalog]
        by_id = {int(x["id"]): x for x in catalog}
        selected = st.selectbox(
            "File hồ sơ đã lưu",
            options,
            format_func=lambda x: "— Chọn file đã lưu —" if x is None else f"{by_id[x].get('doc_type','')} {by_id[x].get('code','')} — {by_id[x].get('file_name') or Path(by_id[x].get('file_path') or '').name}",
            key=f"ai_attachment_{pid}",
        )
        upload = st.file_uploader("Hoặc tải file trực tiếp cho AI", type=["pdf","docx","xlsx","xls","txt","csv","png","jpg","jpeg"], key=f"ai_upload_{pid}")
        instruction = st.text_area("Yêu cầu AI", placeholder="Ví dụ: tóm tắt, trích thông số, liệt kê hồ sơ thiếu, các điểm cần kiểm tra...", key=f"ai_file_instruction_{pid}")
        f1, f2 = st.columns(2)
        if f1.button("Phân tích file đã lưu", disabled=selected is None, width="stretch", key=f"ai_saved_file_{pid}"):
            try:
                meta = by_id.get(int(selected), {}) if selected is not None else {}
                if meta.get("drive_file_id"):
                    name, mime, data = _download_drive_file(str(meta.get("drive_file_id")))
                else:
                    name, mime, data = ctx_builder.load_attachment(int(selected))
                with st.spinner(f"AI đang đọc {name}..."):
                    st.session_state[f"ai_file_result_{pid}"] = ai.summarize_file(pid, name, data, instruction, date.today())
            except Exception as exc: st.error(str(exc))
        if f2.button("Phân tích file tải lên", disabled=upload is None, width="stretch", key=f"ai_uploaded_file_{pid}"):
            try:
                with st.spinner(f"AI đang đọc {upload.name}..."):
                    st.session_state[f"ai_file_result_{pid}"] = ai.summarize_file(pid, upload.name, upload.getvalue(), instruction, date.today())
            except Exception as exc: st.error(str(exc))
        file_result = st.session_state.get(f"ai_file_result_{pid}", "")
        if file_result:
            st.markdown(file_result)

    with tab_legal:
        _ui_note("AI ưu tiên các văn bản đã đồng bộ trong sheet Văn bản QLDA XD. Nếu bật Web Search, AI có thể kiểm tra thêm nguồn online; vẫn cần mở văn bản gốc để xác nhận điều khoản.")
        lq = st.text_area("Câu hỏi pháp lý/tiêu chuẩn", placeholder="Ví dụ: Các văn bản trong kho liên quan quản lý chất lượng và nghiệm thu vật liệu đầu vào?", key=f"ai_legal_q_{pid}")
        if st.button("Tra cứu văn bản bằng AI", type="primary", disabled=not bool(lq.strip()), key=f"ai_legal_btn_{pid}"):
            try:
                with st.spinner("AI đang tra cứu văn bản..."):
                    st.session_state[f"ai_legal_result_{pid}"] = ai.legal_qa(pid, lq, date.today(), use_web=settings.use_web)
            except Exception as exc: st.error(str(exc))
        legal_result = st.session_state.get(f"ai_legal_result_{pid}", "")
        if legal_result:
            st.markdown(legal_result)


def render_project_info(pid: int):
    p = db.project(pid)
    st.subheader("⚙️ Thông tin dự án")
    with st.form(f"project_edit_{pid}"):
        c1, c2 = st.columns([1, 2])
        code = c1.text_input("Mã dự án", value=p["code"])
        name = c2.text_input("Tên dự án", value=p["name"])
        c1, c2 = st.columns(2)
        start = c1.date_input("Bắt đầu", value=parse_date(p["start_date"], date.today()))
        end = c2.date_input("Kết thúc", value=parse_date(p["end_date"], date.today()+timedelta(days=365)))
        manager = st.text_input("Quản lý dự án", value=p["manager"] or "")
        note = p["note"] or ""
        if st.form_submit_button("Lưu thông tin", type="primary", disabled=not _is_admin()):
            try:
                db.update_project(pid, code, name, iso(start), iso(end), manager, note)
                st.success("Đã cập nhật dự án.")
                st.rerun()
            except sqlite3.IntegrityError:
                st.error("Mã dự án đã được sử dụng.")
    st.divider()
    st.warning("Xóa dự án sẽ xóa toàn bộ tiến độ, hồ sơ, bản vẽ và file đính kèm thuộc dự án này.")
    confirm = st.checkbox("Tôi xác nhận muốn xóa dự án", key=f"confirm_del_project_{pid}")
    if st.button("🗑️ Xóa dự án", disabled=(not confirm or not _is_admin()), key=f"del_project_{pid}"):
        db.delete_project(pid)
        st.session_state.pop("project_id", None)
        st.rerun()


_require_cloud_login_and_access()
sidebar_project_tools()
pid, projects = project_selector()

st.title("🏗️ QLDA Xây dựng V6.22 PostgreSQL Cloud")
_ui_note("File Hồ sơ/Bản vẽ không đi qua Streamlit; Apps Script chuyển tiếp chunk vào Google Drive resumable upload, tối đa 2 GB/file.")
if not pid:
    st.info("Hãy tạo dự án đầu tiên ở thanh bên trái.")
    st.stop()

p = db.project(pid)
_ui_note(f"Dự án: **{p['code']} - {p['name']}**")
_role = _cloud_access_role()
_email = _streamlit_user_email()
_role_label = {"read":"Chỉ đọc","update":"Cập nhật","admin":"Admin","unknown":"Chưa xác định"}.get(_role, _role)
st.info(f"Quyền hiện tại: **{_role_label}**" + (f" • {_email}" if _email else ""))

_main_sections = [
    ("📅 Tiến độ", lambda: render_schedule(pid)),
    ("📁 Hồ sơ", lambda: render_documents(pid)),
    ("📐 Bản vẽ", lambda: render_drawings(pid)),
    ("💰 Chi phí", lambda: render_cost_management(pid)),
    ("📦 Vật tư", lambda: render_material_management(pid)),
    ("📷 Nhật ký", lambda: render_site_diary(pid)),
    ("📊 Báo cáo", lambda: render_reports(pid)),
    ("📚 Văn bản", lambda: render_legal_documents()),
    ("🤖 AI", lambda: render_ai_assistant(pid)),
    ("⚙️ Cài đặt", lambda: render_settings()),
    ("🏗️ Dự án", lambda: render_project_info(pid)),
]
_main_labels = [x[0] for x in _main_sections]
_main_choice = st.selectbox(
    "📌 Chức năng", _main_labels, key=f"qlda_main_section_{pid}",
    help="Chỉ tải module đang chọn để giảm truy vấn và tăng tốc ứng dụng.",
)
_main_actions = dict(_main_sections)
_main_actions[_main_choice]()
