from __future__ import annotations

import json
import os
import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path
from settings_store import APP_SETTINGS_FILE, DEFAULT_SPECIFIED_SEARCH_DOMAINS, get_specified_search_domains, load_app_settings, save_app_settings
from typing import Iterable
from urllib.parse import quote_plus, urljoin, urlparse, parse_qs, unquote

import requests
from bs4 import BeautifulSoup

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36 QLDA-XD/3.7"
)
TIMEOUT = 15
DATE_FMT = "%Y-%m-%d"

VBPL_MOC = "https://vbpl.vn/boxaydung"
GOV_DOCS = "https://vanban.chinhphu.vn/he-thong-van-ban"
GOV_TYPEGROUPS = {"Luật": 3, "Nghị định": 4, "Quyết định": 5, "Thông tư": 6}
VSQI_SEARCH = "https://tieuchuan.vsqi.gov.vn/tim-kiem"
VSQI_CONSTRUCTION = "https://tieuchuanxaydung.vsqi.gov.vn/tim-kiem"
MOC_DRAFTS = "https://www.moc.gov.vn/vn/Pages/DanhSachDuThaoVanBan.aspx"
MOC_LEGAL = "https://www.moc.gov.vn/pl/pages/Vanban.aspx"

# V4.0.5 - Google Search integration.
# Google Custom Search JSON API is only available to existing customers in 2026.
# The app therefore supports API mode when credentials exist, and browser mode
# for unrestricted Google Search without scraping Google result pages.
GOOGLE_SEARCH_API = "https://www.googleapis.com/customsearch/v1"
GOOGLE_CONFIG_DIR = Path.home() / ".qlda_xaydung"
GOOGLE_CONFIG_FILE = APP_SETTINGS_FILE
THUVIENPHAPLUAT_SEARCH = "https://thuvienphapluat.vn/page/tim-van-ban.aspx"

# V4.0.9 - Đồng bộ chuyên sâu Thư Viện Pháp Luật (nguồn tham khảo).
# Không sao chép toàn văn; chỉ thu thập metadata + URL và luôn yêu cầu đối chiếu
# với nguồn chính thức trước khi áp dụng. Các truy vấn được chọn theo nghiệp vụ QLDA XD.
TVPL_SYNC_QUERIES = (
    "quản lý dự án đầu tư xây dựng",
    "quản lý chất lượng công trình xây dựng",
    "thi công xây dựng bảo trì công trình",
    "nghiệm thu công trình xây dựng",
    "phân cấp công trình xây dựng",
    "hợp đồng xây dựng",
    "quản lý chi phí đầu tư xây dựng",
    "định mức xây dựng",
    "an toàn lao động trong thi công xây dựng",
    "phòng cháy chữa cháy công trình xây dựng",
    "vật liệu xây dựng",
    "quy chuẩn kỹ thuật quốc gia về xây dựng",
    "nhà ở chung cư xây dựng",
    "quy hoạch xây dựng",
    "Bộ Xây dựng thông tư nghị định xây dựng",
)
TVPL_SOURCE_NAME = "Thư Viện Pháp Luật"


class GoogleSearchConfigError(RuntimeError):
    pass


def load_google_search_config() -> dict:
    key = (os.environ.get("GOOGLE_SEARCH_API_KEY") or os.environ.get("GOOGLE_CSE_API_KEY") or "").strip()
    cx = (os.environ.get("GOOGLE_SEARCH_CX") or os.environ.get("GOOGLE_CSE_CX") or "").strip()
    source = "environment" if key and cx else ""
    cfg = load_app_settings()
    key = key or str(cfg.get("google_api_key", "")).strip()
    cx = cx or str(cfg.get("google_cx", "")).strip()
    if key and cx and not source:
        source = str(APP_SETTINGS_FILE)
    return {"api_key": key, "cx": cx, "configured": bool(key and cx), "source": source}


def save_google_search_config(api_key: str, cx: str) -> Path:
    return save_app_settings({"google_api_key": (api_key or "").strip(), "google_cx": (cx or "").strip()})

def google_browser_url(query: str) -> str:
    return f"https://www.google.com/search?q={quote_plus((query or '').strip())}"

# Các nhóm ICS phục vụ QLDA/thi công xây dựng. Dùng bộ lọc ICS chính thức của VSQI
# thay vì tìm từ khóa "TCVN" rồi lọc tên, vì cách cũ dễ trả về 0 tiêu chuẩn xây dựng.
VSQI_CONSTRUCTION_ICS = (
    "91.040",  # Nhà/công trình
    "91.060",  # Thành phần công trình
    "91.080",  # Kết cấu
    "91.100",  # Vật liệu xây dựng
    "91.120",  # Bảo vệ công trình
    "91.140",  # Hệ thống kỹ thuật trong nhà/MEP
    "91.160",  # Chiếu sáng
    "91.200",  # Công nghệ xây dựng
    "93.010",  # Kỹ thuật xây dựng chung
    "93.020",  # Công tác đất/nền móng
    "13.220",  # PCCC
    "23.120",  # Thông gió/quạt/ĐHKK
)

# Nhóm dự thảo thuộc khối xây dựng trên Cổng Bộ Xây dựng. Sau khi Bộ Xây dựng
# mở rộng phạm vi quản lý có thêm nhóm giao thông; V4.0.2 chỉ đồng bộ nhóm QLDA XD.
MOC_DRAFT_CATEGORY_IDS = tuple(range(1, 13))

CONSTRUCTION_KEYWORDS = [
    "xây dựng", "công trình", "nhà ở", "bất động sản", "kiến trúc", "quy hoạch đô thị",
    "quy hoạch xây dựng", "vật liệu xây dựng", "hạ tầng kỹ thuật", "cấp nước", "thoát nước",
    "đầu tư xây dựng", "chi phí đầu tư", "định mức", "hợp đồng xây dựng", "quản lý chất lượng",
    "nghiệm thu", "bảo trì công trình", "an toàn cháy", "phòng cháy", "chữa cháy", "kết cấu",
    "bê tông", "thép", "móng", "cọc", "vữa", "gạch", "cấp thoát nước", "thông gió",
    "điều hòa", "điện trong nhà", "thang máy", "chung cư", "công viên", "cây xanh",
]


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", s or "") if unicodedata.category(c) != "Mn")


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip()).lower()


def construction_relevant(text: str) -> bool:
    t = _norm(text)
    return any(k in t for k in CONSTRUCTION_KEYWORDS)


def _iso_date(value: str) -> str:
    value = (value or "").strip()
    for fmt in ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except Exception:
            pass
    return ""


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "vi-VN,vi;q=0.9,en;q=0.7"})
    return s


def _get(session: requests.Session, url: str, *, allow_moc_tls_fallback: bool = True) -> str:
    """GET trang chính thức với xử lý riêng lỗi chứng thư của moc.gov.vn.

    Requests/certifi trên một số máy Windows báo CERTIFICATE_VERIFY_FAILED với
    moc.gov.vn dù trình duyệt vẫn mở được. Ta luôn thử HTTPS chuẩn trước, thử
    hostname www, và chỉ ở bước cuối mới tắt verify cho đúng host chính thức
    moc.gov.vn. Không tắt kiểm tra TLS cho các nguồn khác.
    """
    candidates = [url]
    if "://moc.gov.vn" in url:
        candidates.insert(0, url.replace("://moc.gov.vn", "://www.moc.gov.vn", 1))
    elif "://www.moc.gov.vn" in url:
        candidates.append(url.replace("://www.moc.gov.vn", "://moc.gov.vn", 1))

    last_exc = None
    for candidate in dict.fromkeys(candidates):
        try:
            r = session.get(candidate, timeout=TIMEOUT)
            r.raise_for_status()
            if not r.encoding or r.encoding.lower() in {"iso-8859-1", "ascii"}:
                r.encoding = r.apparent_encoding or "utf-8"
            return r.text
        except requests.exceptions.SSLError as exc:
            last_exc = exc
            if allow_moc_tls_fallback and re.match(r"^https://(www\.)?moc\.gov\.vn/", candidate, re.I):
                try:
                    # Chỉ fallback cho tên miền chính thức Bộ Xây dựng.
                    import urllib3
                    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
                    r = session.get(candidate, timeout=TIMEOUT, verify=False)
                    r.raise_for_status()
                    if not r.encoding or r.encoding.lower() in {"iso-8859-1", "ascii"}:
                        r.encoding = r.apparent_encoding or "utf-8"
                    return r.text
                except Exception as exc2:
                    last_exc = exc2
        except Exception as exc:
            last_exc = exc
    if last_exc:
        raise last_exc
    raise RuntimeError(f"Không tải được: {url}")


def _infer_doc_type(label: str, title: str = "") -> str:
    x = _norm(label + " " + title)
    if "qcvn" in x or "quy chuẩn kỹ thuật" in x:
        return "QCVN"
    if "tcvn" in x or "tiêu chuẩn quốc gia" in x:
        return "TCVN"
    for kind in ["Luật", "Nghị định", "Thông tư", "Quyết định", "Nghị quyết", "Chỉ thị", "Văn bản hợp nhất"]:
        if _norm(kind) in x:
            return kind
    if "dự thảo" in x:
        return "Dự thảo"
    return "Khác"


def _clean_summary(tokens: list[str], label: str) -> str:
    skips = {
        "bản pdf", "vb liên quan", "thuộc tính", "lược đồ", "tải về", "bản in", "toàn văn",
        "xem nhanh", "trạng thái", "ban hành", "hiệu lực",
    }
    try:
        idx = next(i for i, t in enumerate(tokens) if _norm(t) == _norm(label))
    except StopIteration:
        idx = -1
    for tok in tokens[idx + 1: idx + 12]:
        nt = _norm(tok)
        if not nt or any(nt.startswith(s) for s in skips):
            continue
        if re.fullmatch(r"\d{2}/\d{2}/\d{4}", tok.strip()):
            continue
        if len(tok.strip()) >= 8:
            return tok.strip()
    return ""


def _nearest_meta_container(anchor):
    node = anchor
    for _ in range(8):
        node = getattr(node, "parent", None)
        if node is None:
            break
        text = " ".join(node.stripped_strings)
        if "Ban hành:" in text or "Hiệu lực:" in text or len(text) > 120:
            return node
    return anchor.parent


def _first_date(text: str) -> str:
    m = re.search(r"\b(\d{2}[/-]\d{2}[/-]\d{4})\b", text or "")
    return _iso_date(m.group(1)) if m else ""


def _gov_detail_enrich(session: requests.Session, doc: dict) -> dict:
    """Enrich one Cổng Chính phủ result. Failure is non-fatal."""
    try:
        html = _get(session, doc["source_url"])
        text = " ".join(BeautifulSoup(html, "html.parser").stripped_strings)
        patterns = {
            "number": r"Số ký hiệu\s+(.+?)(?=\s+Ngày ban hành)",
            "issue_date": r"Ngày ban hành\s+(\d{2}[/-]\d{2}[/-]\d{4})",
            "effective_date": r"Ngày có hiệu lực\s+(\d{2}[/-]\d{2}[/-]\d{4})",
            "category": r"Loại văn bản\s+(.+?)(?=\s+Cơ quan ban hành)",
            "issuer": r"Cơ quan ban hành\s+(.+?)(?=\s+Người ký|\s+Trích yếu)",
            "title": r"Trích yếu\s+(.+?)(?=\s+Tài liệu đính kèm|\s+©|$)",
        }
        for key, pat in patterns.items():
            m = re.search(pat, text, re.I)
            if not m:
                continue
            val = re.sub(r"\s+", " ", m.group(1)).strip(" :-")
            if key in {"issue_date", "effective_date"}:
                val = _iso_date(val)
            if val:
                doc[key] = val[:1000] if key == "title" else val[:250]
        eff = doc.get("effective_date", "")
        if eff:
            try:
                eff_d = datetime.strptime(eff, DATE_FMT).date()
                doc["status"] = "Chưa có hiệu lực" if eff_d > date.today() else "Đã có hiệu lực / cần đối chiếu VB thay thế"
            except Exception:
                pass
    except Exception:
        pass
    return doc


def fetch_government_construction(max_each_type: int = 35, pages_per_type: int = 2,
                                  only_construction: bool = True, enrich_limit: int = 45) -> list[dict]:
    """Fallback official source: Hệ thống văn bản Cổng TTĐT Chính phủ.

    The national VBPL site may return HTTP 403 to automated clients. This source
    is official and provides document number, issue/effective dates, issuer and
    attachments. We filter the general lists to construction/project-management
    related documents.
    """
    session = _session()
    docs: list[dict] = []
    seen: set[str] = set()
    number_re = re.compile(r"\b[0-9A-ZĐ.\-]+/\d{4}/[A-ZĐ0-9.\-]+\b", re.I)

    for category, typegroup in GOV_TYPEGROUPS.items():
        accepted = 0
        for page in range(1, max(1, pages_per_type) + 1):
            url = f"{GOV_DOCS}?classid=1&mode=1&typegroupid={typegroup}"
            if page > 1:
                url += f"&page={page}"
            try:
                html = _get(session, url)
            except Exception:
                continue
            soup = BeautifulSoup(html, "html.parser")
            grouped: dict[str, list] = {}
            for a in soup.find_all("a", href=True):
                href = a.get("href", "")
                if "docid=" not in href.lower() or "pageid=27160" not in href.lower():
                    continue
                full_url = urljoin(GOV_DOCS, href)
                grouped.setdefault(full_url, []).append(a)

            for source_url, anchors in grouped.items():
                if source_url in seen:
                    continue
                texts = [" ".join(a.stripped_strings).strip() for a in anchors]
                texts = [x for x in texts if x and "tài liệu đính kèm" not in _norm(x)]
                if not texts:
                    continue
                combined_anchor = " | ".join(texts)
                number_m = number_re.search(combined_anchor)
                number = number_m.group(0) if number_m else ""
                # Prefer the longest anchor text that is not merely number/date.
                title_candidates = []
                for t in texts:
                    cleaned = number_re.sub("", t)
                    cleaned = re.sub(r"\b\d{2}[/-]\d{2}[/-]\d{4}\b", "", cleaned)
                    cleaned = re.sub(r"\s+", " ", cleaned).strip(" -|:")
                    if len(cleaned) >= 8:
                        title_candidates.append(cleaned)
                title = max(title_candidates, key=len) if title_candidates else (number or texts[0])

                # Nearby row/container often has issue date even before detail enrichment.
                container = anchors[0]
                for _ in range(5):
                    parent = getattr(container, "parent", None)
                    if parent is None:
                        break
                    container = parent
                    ct = " ".join(container.stripped_strings)
                    if number and number in ct and len(ct) > len(combined_anchor):
                        break
                container_text = " ".join(getattr(container, "stripped_strings", []))
                issue_date = _first_date(container_text or combined_anchor)

                relevant_text = f"{number} {title}"
                is_bxd = "bxd" in _norm(number)
                explicit_standard = any(x in _norm(title) for x in ["qcvn", "quy chuẩn", "tiêu chuẩn quốc gia"])
                relevant = construction_relevant(relevant_text) or explicit_standard
                # Ministry of Construction now also manages transport; do not include every TT-BXD.
                if only_construction and not relevant:
                    continue

                docs.append({
                    "category": _infer_doc_type(category, title) if category == "Thông tư" else category,
                    "number": number,
                    "title": title,
                    "issuer": "Bộ Xây dựng" if is_bxd else ("Quốc hội" if category == "Luật" else ""),
                    "issue_date": issue_date,
                    "effective_date": "",
                    "expiry_date": "",
                    "status": "Cần kiểm tra hiệu lực",
                    "field": "Quản lý đầu tư xây dựng",
                    "source_name": "Cổng TTĐT Chính phủ - Hệ thống văn bản",
                    "source_url": source_url,
                    "is_draft": 0,
                    "note": "Nguồn chính thức dự phòng khi CSDL VBPL chặn truy cập tự động (HTTP 403).",
                })
                seen.add(source_url)
                accepted += 1
                if accepted >= max_each_type:
                    break
            if accepted >= max_each_type:
                break

    # Enrich recent matching documents with official detail metadata.
    for i in range(min(enrich_limit, len(docs))):
        docs[i] = _gov_detail_enrich(session, docs[i])
    return docs


def _fetch_vbpl_moc_primary(max_each_type: int = 30, only_construction: bool = True) -> list[dict]:
    """Primary national VBPL source. May be blocked with HTTP 403 on some networks."""
    s = _session()
    base_html = _get(s, VBPL_MOC)
    soup = BeautifulSoup(base_html, "html.parser")
    wanted = {"Luật", "Nghị định", "Thông tư", "Quyết định", "Nghị quyết"}
    type_urls: dict[str, str] = {}
    for a in soup.find_all("a", href=True):
        text = " ".join(a.stripped_strings).strip()
        if text in wanted and "idLoaiVanBan=" in a["href"]:
            type_urls[text] = urljoin(VBPL_MOC, a["href"])
    pages = list(type_urls.items()) or [("Văn bản", VBPL_MOC)]
    docs: list[dict] = []
    seen_urls: set[str] = set()
    for category_hint, url in pages:
        try:
            html = _get(s, url)
        except Exception:
            continue
        psoup = BeautifulSoup(html, "html.parser")
        count = 0
        for a in psoup.find_all("a", href=True):
            href = a.get("href", "")
            if "vbpq-toanvan.aspx?ItemID=" not in href and "ivbpq-toanvan.aspx?ItemID=" not in href:
                continue
            label = " ".join(a.stripped_strings).strip()
            if not label or len(label) < 5 or label.lower() in {"bản pdf", "toàn văn"}:
                continue
            source_url = urljoin(url, href)
            if source_url in seen_urls:
                continue
            container = _nearest_meta_container(a)
            tokens = [" ".join(x.split()) for x in container.stripped_strings if x.strip()]
            text = "\n".join(tokens)
            issue = re.search(r"Ban hành\s*:\s*(\d{2}[/-]\d{2}[/-]\d{4})", text, re.I)
            effective = re.search(r"Hiệu lực\s*:\s*(\d{2}[/-]\d{2}[/-]\d{4})", text, re.I)
            status_m = re.search(r"Trạng thái\s*:\s*([^\n]+)", text, re.I)
            title = _clean_summary(tokens, label)
            combined = f"{label} {title}"
            if only_construction and not construction_relevant(combined):
                continue
            doc_type = _infer_doc_type(label, title)
            number = re.sub(r"^(Luật|Nghị định|Thông tư|Quyết định|Nghị quyết|Chỉ thị)\s+", "", label, flags=re.I).strip()
            docs.append({
                "category": doc_type if doc_type != "Khác" else category_hint,
                "number": number,
                "title": title or label,
                "issuer": "",
                "issue_date": _iso_date(issue.group(1)) if issue else "",
                "effective_date": _iso_date(effective.group(1)) if effective else "",
                "expiry_date": "",
                "status": status_m.group(1).strip() if status_m else "Chưa xác định",
                "field": "Quản lý đầu tư xây dựng",
                "source_name": "CSDL Quốc gia VBPL - Bộ Xây dựng",
                "source_url": source_url,
                "is_draft": 0,
                "note": "Đồng bộ metadata từ nguồn chính thức.",
            })
            seen_urls.add(source_url)
            count += 1
            if count >= max_each_type:
                break
    return docs



def fetch_moc_official_documents(limit: int = 60, only_construction: bool = True) -> list[dict]:
    """Nguồn trực tiếp Cổng Bộ Xây dựng - danh mục văn bản mới nhất.

    Dùng làm fallback trước Cổng Chính phủ khi CSDL VBPL chặn HTTP 403.
    """
    session = _session()
    html = _get(session, MOC_LEGAL)
    soup = BeautifulSoup(html, "html.parser")
    docs: list[dict] = []
    seen: set[str] = set()
    date_re = re.compile(r"\b(\d{2}[/-]\d{2}[/-]\d{4})\b")
    number_re = re.compile(r"\b\d+[A-ZĐ0-9.\-]*(?:/\d{4})?/[A-ZĐ0-9.\-]+(?:-[A-ZĐ0-9.\-]+)*\b", re.I)

    # Cổng BXD hiển thị theo bảng; parser cũng chịu được layout div/li.
    containers = soup.find_all("tr") or soup.find_all(["li", "div"])
    for node in containers:
        text = re.sub(r"\s+", " ", " ".join(node.stripped_strings)).strip()
        if len(text) < 15:
            continue
        nm = number_re.search(text)
        dm = date_re.search(text)
        if not nm or not dm:
            continue
        number = nm.group(0).strip()
        issue_date = _iso_date(dm.group(1))
        # Trích yếu thường nằm sau ngày ban hành.
        title = text[dm.end():].strip(" -:|") or text
        title = re.sub(r"^(Nội dung trích yếu|Trích yếu)\s*", "", title, flags=re.I).strip()
        if only_construction and not construction_relevant(f"{number} {title}"):
            continue
        a = node.find("a", href=True)
        source_url = urljoin(MOC_LEGAL, a.get("href", "")) if a else MOC_LEGAL
        source_url = source_url.replace("https://moc.gov.vn/", "https://www.moc.gov.vn/")
        key = source_url if source_url != MOC_LEGAL else f"{number}|{issue_date}|{title[:80]}"
        if key in seen:
            continue
        seen.add(key)
        docs.append({
            "category": _infer_doc_type(number, title),
            "number": number,
            "title": title[:1000],
            "issuer": "Bộ Xây dựng",
            "issue_date": issue_date,
            "effective_date": "",
            "expiry_date": "",
            "status": "Cần kiểm tra hiệu lực",
            "field": "Quản lý đầu tư xây dựng",
            "source_name": "Cổng TTĐT Bộ Xây dựng - Văn bản",
            "source_url": source_url,
            "is_draft": 0,
            "note": "Metadata trực tiếp từ Cổng TTĐT Bộ Xây dựng.",
        })
        if len(docs) >= limit:
            break
    return docs


def fetch_vbpl_moc(max_each_type: int = 30, only_construction: bool = True) -> list[dict]:
    """Đồng bộ văn bản QLDA XD theo chuỗi nguồn chính thức dự phòng.

    1) CSDL Quốc gia VBPL - Bộ Xây dựng.
    2) Cổng TTĐT Bộ Xây dựng - Văn bản.
    3) Hệ thống văn bản Cổng TTĐT Chính phủ.
    """
    errors: list[str] = []
    try:
        docs = _fetch_vbpl_moc_primary(max_each_type=max_each_type, only_construction=only_construction)
        if docs:
            return docs
    except Exception as exc:
        errors.append(f"VBPL: {type(exc).__name__}: {exc}")

    try:
        docs = fetch_moc_official_documents(limit=max_each_type * 2, only_construction=only_construction)
        if docs:
            for d in docs:
                d["note"] = (d.get("note", "") + " Dùng nguồn BXD trực tiếp do VBPL có thể chặn request tự động.").strip()
            return docs
    except Exception as exc:
        errors.append(f"moc.gov.vn: {type(exc).__name__}: {exc}")

    try:
        fallback = fetch_government_construction(max_each_type=max_each_type, only_construction=only_construction)
        if fallback:
            note = " ".join(errors[:2])
            for d in fallback:
                d["note"] = (d.get("note", "") + (f" Nguồn trước không truy cập được: {note}" if note else "")).strip()
            return fallback
    except Exception as exc:
        errors.append(f"Cổng Chính phủ: {type(exc).__name__}: {exc}")

    if errors:
        raise RuntimeError("Không cập nhật được văn bản từ các nguồn chính thức. " + " | ".join(errors[:3]))
    return []


def _vsqi_result_from_anchor(a, page_url: str, field_hint: str = "Tiêu chuẩn xây dựng") -> dict | None:
    label = re.sub(r"\s+", " ", " ".join(a.stripped_strings)).strip().replace("\xa0", " ")
    href = (a.get("href", "") or "").strip()
    if not re.match(r"^TCVN\s+", label, re.I):
        return None
    # Chỉ nhận link chi tiết tiêu chuẩn, tránh menu/anchor trùng tên.
    if "tieuchuan/view" not in href.lower():
        return None

    # Leo lên container kết quả để lấy tên tiếng Việt nằm sau số hiệu.
    container = a
    for _ in range(7):
        parent = getattr(container, "parent", None)
        if parent is None:
            break
        container = parent
        txt = re.sub(r"\s+", " ", " ".join(container.stripped_strings)).strip()
        if ("Xem chi tiết" in txt or "View" in txt) and len(txt) <= 1800:
            break
    tokens = [re.sub(r"\s+", " ", x).strip() for x in container.stripped_strings if x.strip()]
    title = ""
    # Tìm token ngay sau số hiệu, bỏ các nút hành động/tiếng Anh phụ trợ.
    norm_label = _norm(label)
    idx = next((i for i, x in enumerate(tokens) if _norm(x) == norm_label), -1)
    for tok in tokens[idx + 1: idx + 10]:
        nt = _norm(tok)
        if nt in {"đặt mua", "order", "xem chi tiết", "view"}:
            continue
        if re.match(r"^TCVN\s+", tok, re.I):
            continue
        if len(tok) >= 5:
            title = tok
            break

    source_url = urljoin(page_url, href)
    year = re.search(r":(\d{4})\b", label)
    return {
        "category": "TCVN",
        "number": label,
        "title": title or label,
        "issuer": "Bộ Khoa học và Công nghệ",
        "issue_date": f"{year.group(1)}-01-01" if year else "",
        "effective_date": "",
        "expiry_date": "",
        "status": "Cần kiểm tra hiệu lực",
        "field": field_hint,
        "source_name": "VSQI - CSDL Tiêu chuẩn quốc gia",
        "source_url": source_url,
        "is_draft": 0,
        "note": "Metadata tra cứu online từ VSQI; không lưu nội dung tiêu chuẩn có bản quyền.",
    }


def _enrich_vsqi_status(session: requests.Session, doc: dict) -> dict:
    try:
        html = _get(session, doc["source_url"])
        text = " ".join(BeautifulSoup(html, "html.parser").stripped_strings)
        status = re.search(r"Tình trạng\s+(?:Validation Status\s+)?(.{0,100}?)(?:Tên tiếng Việt|Title in Vietnamese)", text, re.I)
        if status:
            raw = re.sub(r"\s+", " ", status.group(1)).strip(" :-")
            if raw:
                # Chuẩn hóa để KPI 'Còn hiệu lực' nhận biết được.
                if raw.upper().startswith("A") and "còn hiệu lực" not in _norm(raw):
                    raw = "A - Còn hiệu lực"
                elif raw.upper().startswith("W") and "hết hiệu lực" not in _norm(raw):
                    raw = "W - Hết hiệu lực"
                doc["status"] = raw[:120]
        year = re.search(r"Năm ban hành\s+(?:Publication date\s+)?(\d{4})", text, re.I)
        if year:
            doc["issue_date"] = f"{year.group(1)}-01-01"
        title = re.search(r"Tên tiếng Việt\s+(?:Title in Vietnamese\s+)?(.{3,500}?)(?:Tên tiếng Anh|Title in English)", text, re.I)
        if title:
            val = re.sub(r"\s+", " ", title.group(1)).strip()
            if val:
                doc["title"] = val[:500]
    except Exception:
        pass
    return doc


def _collect_vsqi_page(session: requests.Session, url: str, field_hint: str) -> list[dict]:
    html = _get(session, url)
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict] = []
    for a in soup.find_all("a", href=True):
        doc = _vsqi_result_from_anchor(a, url, field_hint)
        if doc:
            out.append(doc)
    return out


def fetch_vsqi_recent(pages: int = 1, only_construction: bool = True,
                      enrich_limit: int = 6, max_results: int = 100) -> list[dict]:
    """Đồng bộ TCVN liên quan xây dựng theo các nhóm ICS chính thức của VSQI.

    Bản cũ dùng ?si=TCVN rồi lọc từ khóa trong tiêu đề nên rất dễ trả 0 bản ghi.
    Bản này truy vấn trực tiếp các nhóm ICS 91/93/MEP/PCCC và dedupe theo số hiệu.
    """
    s = _session()
    docs_by_number: dict[str, dict] = {}
    errors: list[str] = []
    successful_pages = 0

    if only_construction:
        queries = [(ics, f"Xây dựng / ICS {ics}") for ics in VSQI_CONSTRUCTION_ICS]
    else:
        queries = [("", "Tiêu chuẩn quốc gia")]

    for ics, field_hint in queries:
        for page in range(1, max(1, pages) + 1):
            if ics:
                url = f"{VSQI_SEARCH}?ic%5B%5D={quote_plus(ics)}&page={page}"
            else:
                url = f"{VSQI_SEARCH}?si=TCVN&page={page}"
            try:
                page_docs = _collect_vsqi_page(s, url, field_hint)
                successful_pages += 1
            except Exception as exc:
                errors.append(f"{ics or 'TCVN'} p{page}: {exc}")
                continue
            for doc in page_docs:
                key = _norm(doc["number"])
                if key and key not in docs_by_number:
                    docs_by_number[key] = doc
                    if len(docs_by_number) >= max_results:
                        break
            if len(docs_by_number) >= max_results:
                break
        if len(docs_by_number) >= max_results:
            break

    # Fallback: cổng chuyên ngành xây dựng của VSQI nếu cổng tổng hợp thay cấu trúc.
    if not docs_by_number:
        for page in range(1, max(1, pages) + 1):
            url = f"{VSQI_CONSTRUCTION}?si=TCVN&page={page}"
            try:
                page_docs = _collect_vsqi_page(s, url, "Tiêu chuẩn ngành Xây dựng")
                successful_pages += 1
            except Exception as exc:
                errors.append(f"VSQI-XD p{page}: {exc}")
                continue
            for doc in page_docs:
                key = _norm(doc["number"])
                if key and key not in docs_by_number:
                    docs_by_number[key] = doc

    docs = list(docs_by_number.values())
    # Ưu tiên tiêu chuẩn năm mới trước khi enrich.
    docs.sort(key=lambda d: (d.get("issue_date", ""), d.get("number", "")), reverse=True)
    for i in range(min(enrich_limit, len(docs))):
        docs[i] = _enrich_vsqi_status(s, docs[i])

    if not docs and not successful_pages and errors:
        raise RuntimeError("Không truy cập được VSQI. " + " | ".join(errors[:3]))
    return docs


def search_vsqi(query: str, limit: int = 30) -> list[dict]:
    """Tra cứu trực tiếp TCVN theo số hiệu/từ khóa trên VSQI."""
    query = (query or "").strip()
    if not query:
        return []
    s = _session()
    url = f"{VSQI_SEARCH}?si={quote_plus(query)}"
    docs = _collect_vsqi_page(s, url, "Tra cứu TCVN")
    # Dedupe theo số hiệu
    unique: dict[str, dict] = {}
    for doc in docs:
        unique.setdefault(_norm(doc["number"]), doc)
        if len(unique) >= limit:
            break
    out = list(unique.values())
    for i in range(min(10, len(out))):
        out[i] = _enrich_vsqi_status(s, out[i])
    return out




# --- V4.0.4: tìm kiếm online tổng hợp -------------------------------------------------
# Tra cứu này không "crawl toàn bộ Internet". Nó truy vấn công cụ tìm kiếm web để
# bao phủ các trang đã được lập chỉ mục, đồng thời chạy tra cứu trực tiếp VSQI khi
# câu hỏi có TCVN/QCVN. Nguồn chính thức được ưu tiên và đánh dấu rõ trong metadata.
OFFICIAL_WEB_DOMAINS = {
    "vanban.chinhphu.vn": "Cổng TTĐT Chính phủ - Hệ thống văn bản",
    "congbao.chinhphu.vn": "Công báo điện tử Chính phủ",
    "vbpl.vn": "CSDL Quốc gia về VBPL",
    "vbpl.moj.gov.vn": "CSDL Quốc gia về VBPL - Bộ Tư pháp",
    "moj.gov.vn": "Bộ Tư pháp",
    "moc.gov.vn": "Cổng TTĐT Bộ Xây dựng",
    "www.moc.gov.vn": "Cổng TTĐT Bộ Xây dựng",
    "tieuchuan.vsqi.gov.vn": "VSQI - CSDL Tiêu chuẩn quốc gia",
    "tieuchuanxaydung.vsqi.gov.vn": "VSQI - Tiêu chuẩn xây dựng",
    "chinhphu.vn": "Cổng TTĐT Chính phủ",
    "xaydungchinhsach.chinhphu.vn": "Cổng TTĐT Chính phủ - Xây dựng chính sách",
}

# Nguồn tra cứu pháp luật chính/ưu tiên trong ứng dụng.
# Đây là nền tảng tra cứu, không phải cơ quan ban hành văn bản nhà nước.
REFERENCE_WEB_DOMAINS = {
    "thuvienphapluat.vn": TVPL_SOURCE_NAME,
    "www.thuvienphapluat.vn": TVPL_SOURCE_NAME,
}

# V4.0.7 - website chỉ định được quản lý ở sheet Cài đặt.
# Hằng số này chỉ giữ để tương thích code/test cũ; runtime đọc settings_store.
SPECIFIED_SEARCH_DOMAINS = tuple(DEFAULT_SPECIFIED_SEARCH_DOMAINS)


def _extract_doc_number(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "")
    patterns = [
        r"\b(?:TCVN|QCVN|TCXDVN)\s*\d[0-9A-Z.\-/]*(?::\d{4})?\b",
        r"\b\d{1,4}/\d{4}/(?:TT|NĐ|QĐ|NQ|CT|VBHN)-[A-ZĐ0-9.-]+\b",
        r"\b\d{1,4}/(?:NĐ-CP|QĐ-TTg|NQ-CP|TT-[A-ZĐ0-9.-]+|QĐ-[A-ZĐ0-9.-]+)\b",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.I)
        if m:
            return re.sub(r"\s+", " ", m.group(0)).strip()
    return ""


def _issuer_from_domain(domain: str) -> str:
    d = (domain or "").lower().lstrip("www.")
    if d == "moc.gov.vn":
        return "Bộ Xây dựng"
    if d in {"vanban.chinhphu.vn", "congbao.chinhphu.vn", "chinhphu.vn", "xaydungchinhsach.chinhphu.vn"}:
        return "Cơ quan nhà nước / Cổng Chính phủ"
    if d in {"vbpl.vn", "vbpl.moj.gov.vn", "moj.gov.vn"}:
        return "Cơ quan nhà nước / Bộ Tư pháp"
    if d.endswith("vsqi.gov.vn"):
        return "Ủy ban Tiêu chuẩn Đo lường Chất lượng Quốc gia"
    return ""


def _decode_ddg_url(href: str) -> str:
    href = href or ""
    if href.startswith("//"):
        href = "https:" + href
    try:
        parsed = urlparse(href)
        q = parse_qs(parsed.query)
        if q.get("uddg"):
            return unquote(q["uddg"][0])
    except Exception:
        pass
    return href


def _normalize_result_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("//"):
        url = "https:" + url
    return url


def search_google_api(query: str, limit: int = 30, api_key: str | None = None, cx: str | None = None) -> list[dict]:
    """Search Google via the official Custom Search JSON API.

    No site: restriction is added by the app. The actual scope is controlled by
    the user's Programmable Search Engine configuration. Existing pre-2026
    engines may still have full-web search; newly created engines are limited by
    Google's current product policy.
    """
    query = re.sub(r"\s+", " ", (query or "")).strip()
    if not query:
        return []
    cfg = load_google_search_config()
    api_key = (api_key or cfg.get("api_key") or "").strip()
    cx = (cx or cfg.get("cx") or "").strip()
    if not api_key or not cx:
        raise GoogleSearchConfigError(
            "Chưa cấu hình Google Search API. Hãy nhập GOOGLE_SEARCH_API_KEY và GOOGLE_SEARCH_CX "
            "hoặc cấu hình tại sheet ⚙ Cài đặt. Có thể dùng nút 'Mở Google toàn web' mà không cần API."
        )
    out: list[dict] = []
    seen: set[str] = set()
    start = 1
    remaining = max(1, min(int(limit), 50))
    s = _session()
    while remaining > 0 and start <= 91:
        num = min(10, remaining)
        r = s.get(GOOGLE_SEARCH_API, params={"key": api_key, "cx": cx, "q": query, "num": num, "start": start, "hl": "vi"}, timeout=TIMEOUT)
        if r.status_code == 400:
            try:
                detail = r.json().get("error", {}).get("message", r.text[:300])
            except Exception:
                detail = r.text[:300]
            raise GoogleSearchConfigError(f"Google Search API từ chối cấu hình: {detail}")
        if r.status_code in {401, 403}:
            try:
                detail = r.json().get("error", {}).get("message", r.text[:300])
            except Exception:
                detail = r.text[:300]
            raise GoogleSearchConfigError(f"Google Search API không được phép: {detail}")
        r.raise_for_status()
        payload = r.json()
        items = payload.get("items") or []
        if not items:
            break
        for item in items:
            href = _normalize_result_url(item.get("link", ""))
            if not href.startswith("http") or href in seen:
                continue
            title = re.sub(r"\s+", " ", item.get("title", "") or "").strip()
            snippet = re.sub(r"\s+", " ", item.get("snippet", "") or "").strip()
            if title:
                out.append({"title": title, "url": href, "snippet": snippet, "engine": "Google"})
                seen.add(href)
            if len(out) >= limit:
                return out
        remaining = limit - len(out)
        next_pages = (payload.get("queries") or {}).get("nextPage") or []
        if not next_pages:
            break
        try:
            start = int(next_pages[0].get("startIndex", start + len(items)))
        except Exception:
            start += len(items)
    return out


def _search_bing_html(query: str, limit: int = 20, pages: int = 1) -> list[dict]:
    s = _session()
    out: list[dict] = []
    seen: set[str] = set()
    for page in range(max(1, pages)):
        first = page * 10 + 1
        url = f"https://www.bing.com/search?q={quote_plus(query)}&count=10&first={first}&setlang=vi"
        try:
            html = _get(s, url, allow_moc_tls_fallback=False)
        except Exception:
            continue
        soup = BeautifulSoup(html, "html.parser")
        for li in soup.select("li.b_algo"):
            a = li.select_one("h2 a[href]")
            if not a:
                continue
            href = _normalize_result_url(a.get("href", ""))
            if not href.startswith("http") or "bing.com/" in href.lower() or href in seen:
                continue
            title = re.sub(r"\s+", " ", " ".join(a.stripped_strings)).strip()
            sn = li.select_one("div.b_caption p") or li.select_one("p")
            snippet = re.sub(r"\s+", " ", " ".join(sn.stripped_strings)).strip() if sn else ""
            if title:
                out.append({"title": title, "url": href, "snippet": snippet, "engine": "Bing"})
                seen.add(href)
            if len(out) >= limit:
                return out
    return out


def _search_duckduckgo_html(query: str, limit: int = 20) -> list[dict]:
    s = _session()
    url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
    try:
        html = _get(s, url, allow_moc_tls_fallback=False)
    except Exception:
        return []
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict] = []
    seen: set[str] = set()
    for node in soup.select("div.result"):
        a = node.select_one("a.result__a[href]")
        if not a:
            continue
        href = _normalize_result_url(_decode_ddg_url(a.get("href", "")))
        if not href.startswith("http") or href in seen:
            continue
        title = re.sub(r"\s+", " ", " ".join(a.stripped_strings)).strip()
        sn = node.select_one("a.result__snippet") or node.select_one("div.result__snippet")
        snippet = re.sub(r"\s+", " ", " ".join(sn.stripped_strings)).strip() if sn else ""
        if title:
            out.append({"title": title, "url": href, "snippet": snippet, "engine": "DuckDuckGo"})
            seen.add(href)
        if len(out) >= limit:
            break
    return out


def _canonical_url(url: str) -> str:
    """Chuẩn hóa URL để chống trùng giữa nhiều truy vấn/công cụ tìm kiếm."""
    url = _normalize_result_url(url)
    try:
        p = urlparse(url)
        scheme = p.scheme or "https"
        host = (p.hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]
        path = re.sub(r"/{2,}", "/", p.path or "/")
        return f"{scheme}://{host}{path}".rstrip("/")
    except Exception:
        return url.split("#", 1)[0].split("?", 1)[0].rstrip("/")


def _tvpl_doc_identity(doc: dict) -> str:
    """Khóa logic chống trùng TVPL: ưu tiên số hiệu, sau đó URL, cuối cùng tiêu đề."""
    number = _norm(_strip_accents(str(doc.get("number", ""))))
    if number:
        return "n:" + re.sub(r"\s+", "", number)
    url = _canonical_url(str(doc.get("source_url", "")))
    if url:
        return "u:" + url.lower()
    title = _norm(_strip_accents(str(doc.get("title", ""))))
    return "t:" + title[:220]


def _tvpl_detail_enrich(session: requests.Session, doc: dict) -> dict:
    """Lấy metadata cơ bản từ trang chi tiết TVPL; lỗi mạng/anti-bot là non-fatal."""
    try:
        html = _get(session, doc.get("source_url", ""), allow_moc_tls_fallback=False)
        soup = BeautifulSoup(html, "html.parser")
        text = re.sub(r"\s+", " ", " ".join(soup.stripped_strings)).strip()
        patterns = {
            "number": r"Số hiệu\s*:\s*(.+?)(?=\s+Loại văn bản\s*:)",
            "category": r"Loại văn bản\s*:\s*(.+?)(?=\s+(?:Nơi ban hành|Cơ quan ban hành)\s*:)",
            "issuer": r"(?:Nơi ban hành|Cơ quan ban hành)\s*:\s*(.+?)(?=\s+Người ký\s*:)",
            "issue_date": r"Ngày ban hành\s*:\s*(\d{1,2}[/-]\d{1,2}[/-]\d{4})",
            "effective_date": r"(?:Ngày có hiệu lực|Ngày hiệu lực)\s*:\s*(\d{1,2}[/-]\d{1,2}[/-]\d{4})",
            "status": r"Tình trạng\s*:\s*(.+?)(?=\s+(?:MỤC LỤC|Mục lục|Thuộc tính|Tải về|Văn bản liên quan|Nội dung|$))",
        }
        for key, pat in patterns.items():
            m = re.search(pat, text, re.I)
            if not m:
                continue
            val = re.sub(r"\s+", " ", m.group(1)).strip(" :-")
            if not val or val.lower() in {"đã biết", "chưa biết"}:
                continue
            if key in {"issue_date", "effective_date"}:
                val = _iso_date(val)
            if key == "category":
                val = _infer_doc_type(val, doc.get("title", ""))
            if val:
                doc[key] = val[:1000] if key == "status" else val[:300]
        tvpl_status = (doc.get("status") or "").strip()
        if tvpl_status and "nguồn" not in tvpl_status.lower():
            doc["note"] = (doc.get("note", "") + f" | TVPL ghi nhận tình trạng: {tvpl_status}").strip(" |")[:1500]
            # Giữ trạng thái/hiệu lực do TVPL hiển thị thay vì ghi đè bằng nhãn nguồn.
            doc["status"] = tvpl_status
        else:
            doc["status"] = "Cần kiểm tra hiệu lực"
    except Exception:
        pass
    return doc


def _tvpl_search_engine_results(query: str, limit: int = 14) -> list[dict]:
    """Fallback site-search TVPL qua Google API/Bing/DDG khi trang tìm trực tiếp ít kết quả."""
    site_query = f"site:thuvienphapluat.vn/van-ban/ {query}"
    raw: list[dict] = []
    try:
        raw.extend(search_google_api(site_query, limit=min(limit, 10)))
    except Exception:
        pass
    if len(raw) < 4:
        raw.extend(_search_bing_html(site_query, limit=limit, pages=1))
    if len(raw) < 4:
        raw.extend(_search_duckduckgo_html(site_query, limit=limit))
    return raw[:limit]


def _dedupe_tvpl_docs(docs: Iterable[dict], limit: int = 300) -> list[dict]:
    best: dict[str, dict] = {}
    for raw in docs:
        d = dict(raw or {})
        url = _canonical_url(d.get("source_url", ""))
        if url:
            d["source_url"] = url
        key = _tvpl_doc_identity(d)
        if not key:
            continue
        old = best.get(key)
        quality = sum(bool(d.get(k)) for k in ("number", "issuer", "issue_date", "effective_date"))
        old_quality = sum(bool((old or {}).get(k)) for k in ("number", "issuer", "issue_date", "effective_date"))
        if old is None or quality > old_quality:
            best[key] = d
    return list(best.values())[:max(1, int(limit))]


def _collect_tvpl_query(query: str, per_query: int) -> list[dict]:
    """Thu thập một nhóm từ khóa TVPL; dùng session độc lập để an toàn khi chạy song song."""
    candidates: list[dict] = []
    direct: list[dict] = []
    try:
        direct = search_thuvienphapluat(query, limit=per_query)
        candidates.extend(direct)
    except Exception:
        direct = []
    if len(direct) < 5:
        for item in _tvpl_search_engine_results(query, limit=min(12, per_query)):
            d = _web_result_doc(item, query)
            if not d:
                continue
            try:
                host = (urlparse(d.get("source_url", "")).hostname or "").lower().lstrip("www.")
            except Exception:
                host = ""
            if host == "thuvienphapluat.vn":
                candidates.append(d)
    return candidates


def _enrich_one_tvpl(doc: dict) -> dict:
    return _tvpl_detail_enrich(_session(), dict(doc))


def fetch_thuvienphapluat_qlda(
    limit: int = 260,
    per_query: int = 18,
    detail_limit: int = 35,
) -> list[dict]:
    """Đồng bộ metadata văn bản QLDA XD từ Thư Viện Pháp Luật.

    Tìm theo nhóm nghiệp vụ, fallback site-search, chống trùng và enrich có giới hạn.
    Mạng/HTML chạy song song tối đa 3-4 luồng trong process cập nhật để giảm thời gian chờ.
    Chỉ lưu metadata/link; TVPL được dùng làm nguồn tra cứu pháp luật chính/ưu tiên trong ứng dụng.
    """
    candidates: list[dict] = []
    # Giới hạn 3 worker để không tạo tải lớn lên nguồn tra cứu.
    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="tvpl-search") as pool:
        futs = {pool.submit(_collect_tvpl_query, q, per_query): q for q in TVPL_SYNC_QUERIES}
        for fut in as_completed(futs):
            try:
                candidates.extend(fut.result())
            except Exception:
                pass
            if len(candidates) >= limit * 2:
                for f in futs:
                    f.cancel()
                break

    relevant: list[dict] = []
    for d in candidates:
        text = " ".join(str(d.get(k, "")) for k in ("number", "title", "note", "field"))
        number = str(d.get("number", "")).upper()
        if construction_relevant(text) or "BXD" in number or any(k in _norm(text) for k in ("luật xây dựng", "phòng cháy", "pccc", "quy chuẩn", "tiêu chuẩn")):
            d = dict(d)
            d["source_name"] = TVPL_SOURCE_NAME
            d["field"] = "QLDA xây dựng / tra cứu pháp luật"
            if not (d.get("status") or "").strip() or "nguồn" in (d.get("status") or "").lower():
                d["status"] = "Cần kiểm tra hiệu lực"
            d["note"] = (d.get("note", "") + " | Đồng bộ TVPL; lưu metadata/link để mở trực tiếp văn bản.").strip(" |")[:1500]
            relevant.append(d)

    docs = _dedupe_tvpl_docs(relevant, limit=limit)
    n_detail = min(max(0, int(detail_limit)), len(docs))
    if n_detail:
        enriched_map: dict[int, dict] = {}
        with ThreadPoolExecutor(max_workers=4, thread_name_prefix="tvpl-detail") as pool:
            futs = {pool.submit(_enrich_one_tvpl, docs[i]): i for i in range(n_detail)}
            for fut in as_completed(futs):
                i = futs[fut]
                try:
                    enriched_map[i] = fut.result()
                except Exception:
                    pass
        for i, d in enriched_map.items():
            docs[i] = d

    for d in docs:
        d["source_name"] = TVPL_SOURCE_NAME
        d["field"] = "QLDA xây dựng / tra cứu pháp luật"
        if not (d.get("status") or "").strip() or "nguồn" in (d.get("status") or "").lower():
            d["status"] = "Cần kiểm tra hiệu lực"
    return _dedupe_tvpl_docs(docs, limit=limit)


def search_thuvienphapluat(query: str, limit: int = 20) -> list[dict]:
    """Tra cứu trực tiếp trang tìm văn bản của Thư Viện Pháp Luật.

    Chỉ lấy metadata/link và giữ đường dẫn trực tiếp để người dùng mở văn bản.
    Khi áp dụng pháp lý, vẫn nên kiểm tra văn bản do cơ quan ban hành công bố.
    """
    query = re.sub(r"\s+", " ", (query or "")).strip()
    if not query:
        return []
    s = _session()
    try:
        html = _get(s, THUVIENPHAPLUAT_SEARCH + "?keyword=" + quote_plus(query), allow_moc_tls_fallback=False)
    except Exception:
        return []
    soup = BeautifulSoup(html, "html.parser")
    out: list[dict] = []
    seen: set[str] = set()
    # Trang TVPL thay đổi markup theo thời gian; lọc anchor văn bản theo URL
    # thay vì phụ thuộc một class CSS duy nhất.
    for a in soup.select('a[href]'):
        href = _normalize_result_url(urljoin("https://thuvienphapluat.vn", a.get("href", "")))
        low = href.lower()
        if "thuvienphapluat.vn/van-ban/" not in low or href in seen:
            continue
        title = re.sub(r"\s+", " ", " ".join(a.stripped_strings)).strip()
        if len(title) < 8:
            continue
        parent = a.parent
        snippet = ""
        if parent is not None:
            try:
                snippet = re.sub(r"\s+", " ", " ".join(parent.stripped_strings)).strip()
            except Exception:
                snippet = ""
        # Giảm các link menu/chuyên mục không phải kết quả văn bản.
        combined = _norm(title + " " + snippet)
        if not (_extract_doc_number(combined) or any(k in combined for k in ["thông tư", "nghị định", "quyết định", "luật", "nghị quyết", "qcvn", "tcvn"])):
            continue
        out.append({"title": title, "url": href, "snippet": snippet[:800], "engine": "Thư Viện Pháp Luật"})
        seen.add(href)
        if len(out) >= max(1, int(limit)):
            break
    docs = []
    for item in out:
        d = _web_result_doc(item, query)
        if d:
            d["source_name"] = TVPL_SOURCE_NAME
            d["field"] = "QLDA xây dựng / tra cứu pháp luật"
            if not (d.get("status") or "").strip() or "nguồn" in (d.get("status") or "").lower():
                d["status"] = "Cần kiểm tra hiệu lực"
            docs.append(d)
    return docs


def _web_result_doc(item: dict, original_query: str) -> dict | None:
    url = _normalize_result_url(item.get("url", ""))
    title = re.sub(r"\s+", " ", item.get("title", "") or "").strip()
    snippet = re.sub(r"\s+", " ", item.get("snippet", "") or "").strip()
    if not url or not title:
        return None
    try:
        domain = (urlparse(url).hostname or "").lower()
    except Exception:
        domain = ""
    source_name = OFFICIAL_WEB_DOMAINS.get(domain)
    official = bool(source_name)
    reference = False
    if not source_name:
        source_name = REFERENCE_WEB_DOMAINS.get(domain)
        reference = bool(source_name)
    if not source_name:
        source_name = f"Tìm kiếm web - {domain or item.get('engine','Web')}"
    number = _extract_doc_number(f"{title} {snippet} {original_query}")
    category = _infer_doc_type(number, title + " " + snippet)
    issue_date = _first_date(snippet)
    if official:
        status = "Nguồn chính thức - cần đối chiếu hiệu lực"
    elif reference:
        status = "Cần kiểm tra hiệu lực"
    else:
        status = "Kết quả web - cần xác minh"
    note = f"Kết quả tra cứu online qua {item.get('engine','Web')}. {snippet}".strip()
    return {
        "category": category,
        "number": number,
        "title": title,
        "issuer": _issuer_from_domain(domain),
        "issue_date": issue_date,
        "effective_date": "",
        "expiry_date": "",
        "status": status,
        "field": "Tra cứu online QLDA xây dựng",
        "source_name": source_name,
        "source_url": url,
        "is_draft": 1 if "dự thảo" in _norm(title + " " + snippet) else 0,
        "note": note[:1500],
        "_official": official,
        "_reference": reference,
    }


def _score_web_doc(doc: dict, query: str) -> int:
    q = _norm(_strip_accents(query))
    number_q = _norm(_extract_doc_number(query))
    hay = _norm(_strip_accents(" ".join([
        str(doc.get("number", "")), str(doc.get("title", "")), str(doc.get("note", "")),
        str(doc.get("source_name", "")), str(doc.get("issuer", "")),
    ])))
    score = 0
    if doc.get("_official"):
        score += 40
    elif doc.get("_reference"):
        score += 20
    if number_q and number_q in _norm(str(doc.get("number", ""))):
        score += 100
    tokens = [t for t in re.findall(r"[0-9a-zđ./-]{3,}", q) if t not in {"thông", "tư", "nghị", "định", "quyết", "định"}]
    score += sum(6 for t in tokens if t in hay)
    if q and q in hay:
        score += 25
    return score


def search_online_all(query: str, limit: int = 45, google_api_key: str | None = None, google_cx: str | None = None) -> list[dict]:
    """Tìm kiếm online tổng hợp cho Luật/NĐ/TT/QCVN/TCVN và tài liệu QLDA XD.

    Ưu tiên Google Search API nếu đã cấu hình; không áp dụng site: restriction.
    Nếu Google API chưa khả dụng, dùng engine fallback rộng. Nguồn chính thức
    chỉ được cộng điểm xếp hạng. Với TCVN/QCVN, VSQI trực tiếp được ghép vào kết quả.
    Hàm chỉ lưu metadata/link, không sao chép toàn văn tiêu chuẩn.
    """
    query = re.sub(r"\s+", " ", (query or "")).strip()
    if not query:
        return []

    docs_by_url: dict[str, dict] = {}

    # Thư Viện Pháp Luật: nguồn tham khảo được tra cứu trực tiếp, không phụ
    # thuộc việc công cụ tìm kiếm bên ngoài có index kết quả hay không.
    try:
        for d in search_thuvienphapluat(query, limit=min(12, limit)):
            url = d.get("source_url", "")
            if url:
                docs_by_url[url] = d
    except Exception:
        pass

    # 1) VSQI trực tiếp nếu truy vấn có dấu hiệu tiêu chuẩn/quy chuẩn.
    if re.search(r"\b(?:TCVN|QCVN|TCXDVN)\b", query, re.I):
        try:
            for d in search_vsqi(query, limit=min(20, limit)):
                d = dict(d)
                d["_official"] = True
                docs_by_url[d.get("source_url", "")] = d
        except Exception:
            pass

    # 2) Google là nguồn ưu tiên khi đã cấu hình API/CX. App KHÔNG thêm
    # site: filter; nguồn chính thức chỉ được ưu tiên ở bước xếp hạng.
    raw_results: list[dict] = []
    google_error = ""
    try:
        raw_results.extend(search_google_api(query, limit=min(30, limit), api_key=google_api_key, cx=google_cx))
    except GoogleSearchConfigError as exc:
        google_error = str(exc)
    except Exception as exc:
        google_error = f"Google: {exc}"

    # 3) Fallback rộng khi Google API chưa được cấu hình/không khả dụng.
    # Các engine fallback cũng dùng đúng query gốc, không giới hạn domain.
    if len(raw_results) < 8:
        raw_results.extend(_search_bing_html(query, limit=min(24, limit), pages=2))
    if len(raw_results) < 8:
        raw_results.extend(_search_duckduckgo_html(query, limit=min(24, limit)))

    for item in raw_results:
        d = _web_result_doc(item, query)
        if not d:
            continue
        url = d.get("source_url", "")
        if url and url not in docs_by_url:
            docs_by_url[url] = d

    docs = list(docs_by_url.values())

    # 3) Enrich một số kết quả chính thức, không để một trang lỗi làm hỏng cả tìm kiếm.
    session = _session()
    enriched = 0
    for i, d in enumerate(docs):
        if enriched >= 8:
            break
        try:
            host = (urlparse(d.get("source_url", "")).hostname or "").lower()
            if host == "vanban.chinhphu.vn":
                docs[i] = _gov_detail_enrich(session, d)
                docs[i]["_official"] = True
                enriched += 1
            elif host.endswith("vsqi.gov.vn"):
                docs[i] = _enrich_vsqi_status(session, d)
                docs[i]["_official"] = True
                enriched += 1
        except Exception:
            pass

    docs.sort(key=lambda d: (_score_web_doc(d, query), bool(d.get("_official"))), reverse=True)
    clean: list[dict] = []
    for d in docs[:max(1, limit)]:
        d = dict(d)
        d.pop("_official", None)
        d.pop("_reference", None)
        clean.append(d)
    return clean


def search_online_sites(query: str, domains: tuple[str, ...] | list[str] | None = None, limit: int = 60, google_api_key: str | None = None, google_cx: str | None = None) -> list[dict]:
    """Tìm chỉ trong các website được cấu hình ở sheet Cài đặt.

    Mỗi domain được tìm bằng truy vấn ``site:domain <query>``. Google API được
    ưu tiên khi đã cấu hình; nếu không, Bing/DuckDuckGo được dùng làm fallback.
    Thư Viện Pháp Luật được đánh dấu là nguồn tham khảo, còn các cổng nhà nước/
    VSQI giữ nhãn nguồn chính thức.
    """
    query = re.sub(r"\s+", " ", (query or "")).strip()
    if not query:
        return []
    domains = tuple(domains or get_specified_search_domains())
    docs_by_url: dict[str, dict] = {}
    per_domain = max(3, min(10, max(1, int(limit)) // max(1, len(domains)) + 1))

    if any((d or "").lower().lstrip("www.") == "thuvienphapluat.vn" for d in domains):
        try:
            for d in search_thuvienphapluat(query, limit=min(15, limit)):
                if d.get("source_url"):
                    docs_by_url[d["source_url"]] = d
        except Exception:
            pass

    for domain in domains:
        domain = (domain or "").strip().lower()
        if not domain:
            continue
        site_query = f"site:{domain} {query}"
        raw: list[dict] = []
        try:
            raw.extend(search_google_api(site_query, limit=per_domain, api_key=google_api_key, cx=google_cx))
        except Exception:
            pass
        if len(raw) < 2:
            raw.extend(_search_bing_html(site_query, limit=per_domain, pages=1))
        if len(raw) < 2:
            raw.extend(_search_duckduckgo_html(site_query, limit=per_domain))
        for item in raw:
            d = _web_result_doc(item, query)
            if not d:
                continue
            try:
                host = (urlparse(d.get("source_url", "")).hostname or "").lower().lstrip("www.")
            except Exception:
                host = ""
            wanted = domain.lstrip("www.")
            if host != wanted and not host.endswith("." + wanted):
                continue
            url = d.get("source_url", "")
            if url:
                docs_by_url[url] = d

    docs = list(docs_by_url.values())
    docs.sort(key=lambda d: (_score_web_doc(d, query), bool(d.get("_official")), bool(d.get("_reference"))), reverse=True)
    clean: list[dict] = []
    for d in docs[:max(1, limit)]:
        d = dict(d)
        d.pop("_official", None)
        d.pop("_reference", None)
        clean.append(d)
    return clean


def fetch_moc_drafts(limit: int = 40, only_construction: bool = True, detail_limit: int = 8) -> list[dict]:
    """Đồng bộ dự thảo QLDA XD từ Cổng TTĐT Bộ Xây dựng.

    - Dùng www.moc.gov.vn trước để tránh lỗi chain chứng thư trên một số Windows.
    - Quét các nhóm chuyên ngành xây dựng (cmID 1..12), không chỉ trang mặc định.
    - Nếu HTTPS chuẩn báo CERTIFICATE_VERIFY_FAILED, _get() chỉ fallback verify=False
      cho đúng tên miền chính thức moc.gov.vn.
    """
    s = _session()
    docs: list[dict] = []
    seen: set[str] = set()
    errors: list[str] = []
    successful_pages = 0
    detail_count = 0

    for cmid in MOC_DRAFT_CATEGORY_IDS:
        list_url = f"{MOC_DRAFTS}?cmID={cmid}"
        try:
            html = _get(s, list_url)
            successful_pages += 1
        except Exception as exc:
            errors.append(f"cmID={cmid}: {exc}")
            continue
        soup = BeautifulSoup(html, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a.get("href", "") or ""
            if "chitietduthao.aspx" not in href.lower() or "iduthao=" not in href.lower():
                continue
            title = re.sub(r"\s+", " ", " ".join(a.stripped_strings)).strip()
            if len(title) < 10:
                continue
            url = urljoin(list_url, href)
            # Chuẩn hóa sang www để giảm lỗi SSL ở Windows.
            url = url.replace("https://moc.gov.vn/", "https://www.moc.gov.vn/")
            if url in seen:
                continue
            # cmID 1..12 vốn là các nhóm xây dựng; vẫn loại các tiêu đề hoàn toàn ngoài XD
            # nếu site thay mapping sau này, nhưng luôn giữ QCVN/TCVN.
            normalized = _norm(title)
            is_standard = any(k in normalized for k in ["qcvn", "tcvn", "quy chuẩn", "tiêu chuẩn"])
            if only_construction and not (construction_relevant(title) or is_standard or cmid in MOC_DRAFT_CATEGORY_IDS):
                continue
            seen.add(url)

            issue_date = ""
            expiry_date = ""
            detail_title = title
            # Bulk update chỉ enrich một số ít bản ghi mới nhất để tránh hàng chục
            # request chi tiết làm CPU/network tăng cao. Các bản còn lại vẫn giữ
            # metadata và URL nguồn chính thức từ trang danh sách.
            if detail_count < max(0, int(detail_limit)):
                try:
                    detail = _get(s, url)
                    detail_soup = BeautifulSoup(detail, "html.parser")
                    text = " ".join(detail_soup.stripped_strings)
                    h = detail_soup.find(["h1", "h2", "h3"])
                    if h:
                        ht = re.sub(r"\s+", " ", " ".join(h.stripped_strings)).strip()
                        if len(ht) >= 10:
                            detail_title = ht
                    start_m = re.search(r"Ngày bắt đầu lấy ý kiến\s*:\s*(\d{2}[/-]\d{2}[/-]\d{4})", text, re.I)
                    end_m = re.search(r"Ngày hết hạn lấy ý kiến\s*:\s*(\d{2}[/-]\d{2}[/-]\d{4})", text, re.I)
                    issue_date = _iso_date(start_m.group(1)) if start_m else ""
                    expiry_date = _iso_date(end_m.group(1)) if end_m else ""
                except Exception:
                    pass
                finally:
                    detail_count += 1

            ntitle = _norm(detail_title)
            if "qcvn" in ntitle or "quy chuẩn" in ntitle:
                category = "Dự thảo QCVN"
            elif "tcvn" in ntitle or "tiêu chuẩn quốc gia" in ntitle:
                category = "Dự thảo TCVN"
            else:
                category = "Dự thảo"

            status = "Dự thảo / lấy ý kiến"
            if expiry_date:
                try:
                    status = "Đang lấy ý kiến" if datetime.strptime(expiry_date, DATE_FMT).date() >= date.today() else "Hết hạn góp ý"
                except Exception:
                    pass

            docs.append({
                "category": category,
                "number": "",
                "title": detail_title,
                "issuer": "Bộ Xây dựng",
                "issue_date": issue_date,
                "effective_date": "",
                "expiry_date": expiry_date,
                "status": status,
                "field": "QLDA xây dựng / Quy chuẩn - Tiêu chuẩn",
                "source_name": "Cổng TTĐT Bộ Xây dựng",
                "source_url": url,
                "is_draft": 1,
                "note": "Metadata dự thảo từ Cổng TTĐT Bộ Xây dựng; không phải văn bản đã có hiệu lực.",
            })
            if len(docs) >= limit:
                return docs

    if not docs and not successful_pages and errors:
        raise RuntimeError("Không truy cập được Cổng Bộ Xây dựng. " + " | ".join(errors[:3]))
    return docs


class LegalRepository:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.ensure_schema()

    def connect(self):
        c = sqlite3.connect(self.db_path, timeout=15)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA busy_timeout=15000")
        # WAL giúp tiến trình cập nhật online và giao diện không khóa nhau lâu.
        try:
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
        except Exception:
            pass
        return c

    def ensure_schema(self):
        with self.connect() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS legal_documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                category TEXT DEFAULT '',
                number TEXT DEFAULT '',
                title TEXT NOT NULL,
                issuer TEXT DEFAULT '',
                issue_date TEXT DEFAULT '',
                effective_date TEXT DEFAULT '',
                expiry_date TEXT DEFAULT '',
                status TEXT DEFAULT '',
                field TEXT DEFAULT '',
                source_name TEXT DEFAULT '',
                source_url TEXT NOT NULL UNIQUE,
                is_draft INTEGER DEFAULT 0,
                note TEXT DEFAULT '',
                online_updated_at TEXT DEFAULT '',
                created_at TEXT DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_legal_category ON legal_documents(category);
            CREATE INDEX IF NOT EXISTS idx_legal_status ON legal_documents(status);
            CREATE TABLE IF NOT EXISTS legal_sync_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_name TEXT NOT NULL,
                sync_time TEXT NOT NULL,
                status TEXT NOT NULL,
                found_count INTEGER DEFAULT 0,
                added_count INTEGER DEFAULT 0,
                updated_count INTEGER DEFAULT 0,
                message TEXT DEFAULT ''
            );
            """)

    def upsert_many(self, docs: Iterable[dict], source_name: str = "") -> dict:
        added = updated = found = 0
        with self.connect() as c:
            for d in docs:
                url = (d.get("source_url") or "").strip()
                title = (d.get("title") or "").strip()
                if not url or not title:
                    continue
                found += 1
                old = c.execute("SELECT id FROM legal_documents WHERE source_url=?", (url,)).fetchone()
                vals = (
                    d.get("category", ""), d.get("number", ""), title, d.get("issuer", ""),
                    d.get("issue_date", ""), d.get("effective_date", ""), d.get("expiry_date", ""),
                    d.get("status", ""), d.get("field", ""), d.get("source_name", source_name), url,
                    int(d.get("is_draft", 0) or 0), d.get("note", ""), _now(),
                )
                if old:
                    c.execute("""
                        UPDATE legal_documents SET category=?,number=?,title=?,issuer=?,issue_date=?,effective_date=?,
                        expiry_date=?,status=?,field=?,source_name=?,source_url=?,is_draft=?,note=?,online_updated_at=?
                        WHERE id=?
                    """, vals + (old["id"],))
                    updated += 1
                else:
                    c.execute("""
                        INSERT INTO legal_documents(category,number,title,issuer,issue_date,effective_date,expiry_date,status,
                        field,source_name,source_url,is_draft,note,online_updated_at,created_at)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """, vals + (_now(),))
                    added += 1
        return {"found": found, "added": added, "updated": updated}

    def crosscheck_tvpl_with_official(self) -> int:
        """Đánh dấu bản TVPL đã có cùng số hiệu trong nguồn chính thức của kho."""
        count = 0
        with self.connect() as c:
            refs = c.execute(
                "SELECT id,number,note FROM legal_documents WHERE source_name LIKE 'Thư Viện Pháp Luật%' AND TRIM(number)<>''"
            ).fetchall()
            for ref in refs:
                official = c.execute(
                    """SELECT source_name,source_url FROM legal_documents
                       WHERE LOWER(REPLACE(number,' ',''))=LOWER(REPLACE(?,' ',''))
                         AND id<>?
                         AND source_name NOT LIKE 'Thư Viện Pháp Luật%'
                         AND (source_name LIKE '%Chính phủ%' OR source_name LIKE '%VBPL%' OR source_name LIKE '%Bộ Xây dựng%' OR source_name LIKE '%VSQI%')
                       ORDER BY id DESC LIMIT 1""",
                    (ref["number"], ref["id"]),
                ).fetchone()
                if not official:
                    continue
                marker = f"Đã đối chiếu số hiệu với nguồn trong kho: {official['source_name']}"
                note = ref["note"] or ""
                if marker not in note:
                    note = (note + " | " + marker).strip(" |")[:1500]
                    c.execute("UPDATE legal_documents SET note=? WHERE id=?", (note, ref["id"]))
                count += 1
        return count

    def log_sync(self, source_name: str, status: str, stats: dict | None = None, message: str = ""):
        stats = stats or {}
        with self.connect() as c:
            c.execute(
                "INSERT INTO legal_sync_log(source_name,sync_time,status,found_count,added_count,updated_count,message) VALUES(?,?,?,?,?,?,?)",
                (source_name, _now(), status, stats.get("found", 0), stats.get("added", 0), stats.get("updated", 0), message[:1000]),
            )

    def last_sync(self, source_name: str | None = None):
        with self.connect() as c:
            if source_name:
                return c.execute("SELECT * FROM legal_sync_log WHERE source_name=? ORDER BY id DESC LIMIT 1", (source_name,)).fetchone()
            return c.execute("SELECT * FROM legal_sync_log ORDER BY id DESC LIMIT 1").fetchone()

    def list_documents(self, keyword: str = "", category: str = "Tất cả", status: str = "Tất cả", source: str = "Tất cả", include_drafts: bool = True):
        sql = "SELECT * FROM legal_documents WHERE 1=1"
        params: list = []
        if keyword:
            k = f"%{keyword}%"
            sql += " AND (number LIKE ? OR title LIKE ? OR issuer LIKE ? OR field LIKE ?)"
            params += [k, k, k, k]
        if category and category != "Tất cả":
            sql += " AND category=?"; params.append(category)
        if status and status != "Tất cả":
            sql += " AND status=?"; params.append(status)
        if source and source != "Tất cả":
            sql += " AND source_name=?"; params.append(source)
        if not include_drafts:
            sql += " AND is_draft=0"
        sql += " ORDER BY COALESCE(NULLIF(issue_date,''),'0000-00-00') DESC, id DESC"
        with self.connect() as c:
            return c.execute(sql, params).fetchall()

    def categories(self):
        with self.connect() as c:
            return [r[0] for r in c.execute("SELECT DISTINCT category FROM legal_documents WHERE category<>'' ORDER BY category")]

    def statuses(self):
        with self.connect() as c:
            return [r[0] for r in c.execute("SELECT DISTINCT status FROM legal_documents WHERE status<>'' ORDER BY status")]

    def sources(self):
        with self.connect() as c:
            return [r[0] for r in c.execute("SELECT DISTINCT source_name FROM legal_documents WHERE source_name<>'' ORDER BY source_name")]

    def delete(self, doc_id: int):
        with self.connect() as c:
            c.execute("DELETE FROM legal_documents WHERE id=?", (int(doc_id),))

    def import_cache(self, path: str | Path) -> dict:
        path = Path(path)
        if not path.exists():
            return {"found": 0, "added": 0, "updated": 0}
        data = json.loads(path.read_text(encoding="utf-8"))
        docs = data.get("documents", data if isinstance(data, list) else [])
        return self.upsert_many(docs, "Cache online")

    def export_cache(self, path: str | Path):
        rows = [dict(r) for r in self.list_documents(include_drafts=True)]
        for r in rows:
            r.pop("id", None); r.pop("created_at", None)
        payload = {"generated_at": _now(), "documents": rows}
        Path(path).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def sync_source(repo: LegalRepository, source: str) -> dict:
    if source == "vbpl":
        label = "VBPL / Cổng Chính phủ"
        fn = fetch_vbpl_moc
    elif source == "vsqi":
        label = "VSQI TCVN xây dựng"
        fn = fetch_vsqi_recent
    elif source == "moc_drafts":
        label = "Dự thảo Bộ Xây dựng"
        fn = fetch_moc_drafts
    elif source == "tvpl":
        label = TVPL_SOURCE_NAME
        fn = fetch_thuvienphapluat_qlda
    else:
        raise ValueError(f"Nguồn không hỗ trợ: {source}")
    try:
        docs = fn()
        stats = repo.upsert_many(docs, label)
        if source == "tvpl":
            stats["crosschecked"] = repo.crosscheck_tvpl_with_official()
        repo.log_sync(label, "OK", stats, f"Đối chiếu nguồn chính thức trong kho: {stats.get('crosschecked', 0)}" if source == "tvpl" else "")
        return {"source": label, **stats, "error": ""}
    except Exception as exc:
        repo.log_sync(label, "ERROR", {}, str(exc))
        return {"source": label, "found": 0, "added": 0, "updated": 0, "error": str(exc)}


def sync_all(repo: LegalRepository) -> list[dict]:
    # TVPL là nguồn tra cứu chính/ưu tiên trong ứng dụng; vẫn đồng bộ sau nguồn nhà nước
    # để có thể đối chiếu số hiệu khi cần.
    return [sync_source(repo, x) for x in ("vbpl", "vsqi", "moc_drafts", "tvpl")]
