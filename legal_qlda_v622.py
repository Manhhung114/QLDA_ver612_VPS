from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Callable


PATCH_MARKER = "V6.22 LEGAL QLXD V3 TT-BXD INDEX"

EXTRA_CONSTRUCTION_KEYWORDS = (
    "quản lý dự án", "chủ đầu tư", "ban quản lý dự án", "giấy phép xây dựng",
    "khảo sát xây dựng", "thiết kế xây dựng", "thiết kế cơ sở", "thiết kế kỹ thuật",
    "thẩm định", "thẩm tra", "lựa chọn nhà thầu", "đấu thầu", "chỉ dẫn kỹ thuật",
    "hồ sơ mời thầu", "hợp đồng", "tạm ứng", "thanh toán", "quyết toán",
    "bảo lãnh thực hiện hợp đồng", "điều chỉnh giá", "phát sinh", "khối lượng",
    "quản lý tiến độ", "quản lý chi phí", "suất vốn đầu tư", "giá xây dựng",
    "chỉ số giá xây dựng", "định mức dự toán", "đơn giá xây dựng",
    "quản lý chất lượng", "nghiệm thu công việc", "nghiệm thu vật liệu",
    "nghiệm thu hoàn thành", "hồ sơ hoàn công", "nhật ký thi công", "kiểm định",
    "quan trắc", "thí nghiệm", "thử nghiệm", "chứng nhận hợp quy", "sự cố công trình",
    "bảo hành công trình", "bảo trì", "an toàn lao động", "an toàn xây dựng",
    "phòng cháy chữa cháy", "pccc", "bảo vệ môi trường", "hiệu quả năng lượng",
    "bim", "mô hình thông tin công trình", "hạ tầng kỹ thuật", "cơ điện",
    "mep", "hệ thống điện", "cấp thoát nước", "điều hòa không khí", "thông gió",
    "thang máy", "chống thấm", "kết cấu", "nền móng", "vật liệu xây dựng",
    "nhà chung cư", "nhà cao tầng", "công trình dân dụng", "công trình công nghiệp",
)

EXTRA_TVPL_SYNC_QUERIES = (
    "Luật Xây dựng và văn bản hướng dẫn thi hành",
    "nghị định quản lý dự án đầu tư xây dựng",
    "thông tư quản lý dự án đầu tư xây dựng Bộ Xây dựng",
    "thông tư Bộ Xây dựng TT-BXD",
    "phân cấp công trình xây dựng TT-BXD",
    "thẩm định thiết kế xây dựng giấy phép xây dựng",
    "khảo sát thiết kế thẩm tra công trình xây dựng",
    "quản lý chất lượng thi công xây dựng nghiệm thu",
    "nghiệm thu hoàn thành đưa công trình vào sử dụng",
    "hồ sơ hoàn công nhật ký thi công xây dựng",
    "kiểm định quan trắc thí nghiệm công trình xây dựng",
    "sự cố công trình xây dựng quản lý chất lượng",
    "bảo hành bảo trì công trình xây dựng",
    "an toàn lao động an toàn trong thi công xây dựng",
    "quản lý chi phí đầu tư xây dựng nghị định thông tư",
    "định mức dự toán giá xây dựng công trình",
    "chỉ số giá xây dựng suất vốn đầu tư",
    "hợp đồng xây dựng tạm ứng thanh toán quyết toán",
    "điều chỉnh giá hợp đồng xây dựng phát sinh khối lượng",
    "đấu thầu lựa chọn nhà thầu xây dựng",
    "quản lý vật liệu xây dựng chứng nhận hợp quy",
    "quy chuẩn kỹ thuật quốc gia công trình xây dựng QCVN",
    "tiêu chuẩn quốc gia TCVN xây dựng kết cấu",
    "PCCC công trình xây dựng nghiệm thu phòng cháy chữa cháy",
    "nhà chung cư nhà cao tầng quy chuẩn xây dựng",
    "hạ tầng kỹ thuật cấp nước thoát nước xây dựng",
    "hệ thống điện công trình xây dựng quy chuẩn tiêu chuẩn",
    "thông gió điều hòa không khí công trình xây dựng",
    "thang máy công trình xây dựng quy chuẩn tiêu chuẩn",
    "BIM mô hình thông tin công trình xây dựng",
    "bảo vệ môi trường dự án đầu tư xây dựng",
    "hiệu quả năng lượng công trình xây dựng",
    "quản lý tiến độ dự án đầu tư xây dựng",
    "phân cấp phân loại công trình xây dựng",
    "quản lý nhà nước về hoạt động xây dựng Bộ Xây dựng",
)

EXTRA_VSQI_ICS = (
    "91.010", "91.020", "91.090", "91.190", "93.030", "93.080",
    "13.100", "27.010", "29.020", "29.140", "29.240",
)

# Quét riêng họ Thông tư Bộ Xây dựng để các văn bản cũ không bị rơi khỏi top
# kết quả của các truy vấn nghiệp vụ rộng. 2010 bao phủ phần lớn khung pháp lý
# hiện đại mà dự án xây dựng đang phải tra cứu; năm hiện tại được lấy động.
BXD_CIRCULAR_START_YEAR = 2010
BXD_CIRCULAR_PER_YEAR = 35
BXD_CIRCULAR_RE = re.compile(r"\b\d{1,3}\s*/\s*\d{4}\s*/\s*TT\s*-\s*BXD\b", re.I)


def _merge_unique(existing, extra) -> tuple[str, ...]:
    out: list[str] = []
    seen: set[str] = set()
    for value in tuple(existing or ()) + tuple(extra or ()):
        text = str(value or "").strip()
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        out.append(text)
    return tuple(out)


def _is_draft_doc(doc: Any) -> bool:
    if not isinstance(doc, dict):
        try:
            doc = dict(doc)
        except Exception:
            return False
    if int(doc.get("is_draft", 0) or 0) != 0:
        return True
    text = " ".join(str(doc.get(k, "") or "") for k in ("category", "status", "title", "note"))
    return "dự thảo" in text.casefold()


def _non_drafts(docs) -> list[dict]:
    out: list[dict] = []
    for doc in docs or []:
        try:
            item = dict(doc)
        except Exception:
            continue
        if not _is_draft_doc(item):
            out.append(item)
    return out


def _is_bxd_circular(doc: Any) -> bool:
    try:
        item = dict(doc)
    except Exception:
        return False
    if _is_draft_doc(item):
        return False
    text = " ".join(str(item.get(k, "") or "") for k in ("number", "title", "note"))
    return bool(BXD_CIRCULAR_RE.search(text))


def _doc_key(doc: dict) -> str:
    number = re.sub(r"\s+", "", str(doc.get("number", "") or "")).upper()
    if BXD_CIRCULAR_RE.search(str(doc.get("number", "") or "")):
        return "n:" + number
    url = str(doc.get("source_url", "") or "").split("#", 1)[0].rstrip("/").lower()
    if url:
        return "u:" + url
    title = re.sub(r"\s+", " ", str(doc.get("title", "") or "")).strip().casefold()
    return "t:" + title[:250]


def _dedupe_priority(primary, secondary, limit: int) -> list[dict]:
    """Giữ nhóm ưu tiên (TT-BXD) trước rồi mới lấp bằng kết quả QLXD rộng."""
    out: list[dict] = []
    seen: set[str] = set()
    for raw in list(primary or []) + list(secondary or []):
        try:
            doc = dict(raw)
        except Exception:
            continue
        if _is_draft_doc(doc):
            continue
        key = _doc_key(doc)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(doc)
        if len(out) >= max(1, int(limit)):
            break
    return out


def _collect_bxd_circulars(
    search_fn: Callable[..., list[dict]],
    *,
    start_year: int = BXD_CIRCULAR_START_YEAR,
    end_year: int | None = None,
    per_year: int = BXD_CIRCULAR_PER_YEAR,
) -> list[dict]:
    """Lập chỉ mục TT-BXD theo năm bằng tìm kiếm trực tiếp TVPL.

    Đây là lớp recall bổ sung cho bulk sync: truy vấn nghiệp vụ rộng thường ưu
    tiên văn bản mới và có thể bỏ sót Thông tư BXD cũ. Truy vấn theo năm giữ số
    request hữu hạn và không cần đoán từng số Thông tư.
    """
    current_year = datetime.now().year
    end = min(int(end_year or current_year), current_year)
    start = min(max(2000, int(start_year)), end)
    years = list(range(end, start - 1, -1))
    found: list[dict] = []

    def one(year: int) -> list[dict]:
        query = f"Thông tư Bộ Xây dựng {year} TT-BXD"
        try:
            docs = search_fn(query, limit=max(20, int(per_year))) or []
        except Exception:
            return []
        return [dict(doc) for doc in docs if _is_bxd_circular(doc)]

    # Nguồn TVPL có thể chậm/giới hạn kết nối; 4 luồng đủ tăng tốc nhưng không
    # tạo tải quá lớn. Lỗi một năm không làm hỏng toàn bộ lần đồng bộ.
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="tvpl-bxd-year") as pool:
        futures = {pool.submit(one, year): year for year in years}
        for future in as_completed(futures):
            try:
                found.extend(future.result())
            except Exception:
                pass

    return _dedupe_priority(found, [], limit=max(1, len(found) or 1))


def purge_drafts(repo: Any) -> int:
    """Permanently remove draft documents/logs from the legal repository."""
    deleted = 0
    try:
        with repo.connect() as connection:
            cur = connection.execute(
                """DELETE FROM legal_documents
                   WHERE COALESCE(is_draft,0)<>0
                      OR LOWER(COALESCE(category,'')) LIKE '%dự thảo%'
                      OR LOWER(COALESCE(status,'')) LIKE '%dự thảo%'"""
            )
            deleted = max(0, int(getattr(cur, "rowcount", 0) or 0))
            try:
                connection.execute(
                    "DELETE FROM legal_sync_log WHERE LOWER(COALESCE(source_name,'')) LIKE '%dự thảo%'"
                )
            except Exception:
                pass
    except Exception:
        return 0
    return deleted


def install_legal_qlda() -> None:
    """Install expanded QLXD synchronisation lazily when the Legal sheet opens."""
    import legal_documents as ld

    if getattr(ld, "_v622_legal_qlda_installed", False):
        return

    ld.CONSTRUCTION_KEYWORDS = list(
        _merge_unique(getattr(ld, "CONSTRUCTION_KEYWORDS", ()), EXTRA_CONSTRUCTION_KEYWORDS)
    )
    ld.TVPL_SYNC_QUERIES = _merge_unique(
        getattr(ld, "TVPL_SYNC_QUERIES", ()), EXTRA_TVPL_SYNC_QUERIES
    )
    ld.VSQI_CONSTRUCTION_ICS = _merge_unique(
        getattr(ld, "VSQI_CONSTRUCTION_ICS", ()), EXTRA_VSQI_ICS
    )

    original_vbpl = ld.fetch_vbpl_moc
    original_vsqi = ld.fetch_vsqi_recent
    original_tvpl = ld.fetch_thuvienphapluat_qlda
    original_tvpl_search = ld.search_thuvienphapluat
    original_search_all = ld.search_online_all
    original_search_sites = ld.search_online_sites

    def fetch_vbpl_qlda(max_each_type: int = 60, only_construction: bool = True):
        return _non_drafts(original_vbpl(max_each_type=max_each_type, only_construction=only_construction))

    def fetch_vsqi_qlda(
        pages: int = 2,
        only_construction: bool = True,
        enrich_limit: int = 12,
        max_results: int = 220,
    ):
        return _non_drafts(original_vsqi(
            pages=pages,
            only_construction=only_construction,
            enrich_limit=enrich_limit,
            max_results=max_results,
        ))

    def fetch_tvpl_qlda(
        limit: int = 700,
        per_query: int = 18,
        detail_limit: int = 50,
    ):
        # Chạy chỉ mục TT-BXD độc lập với truy vấn QLXD chung. Đặt TT-BXD trước
        # khi cắt limit để một văn bản cũ không bị loại chỉ vì có nhiều kết quả mới.
        bxd_docs = _collect_bxd_circulars(original_tvpl_search)
        general_docs = _non_drafts(
            original_tvpl(limit=limit, per_query=per_query, detail_limit=detail_limit)
        )
        return _dedupe_priority(bxd_docs, general_docs, limit=limit)

    def search_all_no_drafts(*args, **kwargs):
        return _non_drafts(original_search_all(*args, **kwargs))

    def search_sites_no_drafts(*args, **kwargs):
        return _non_drafts(original_search_sites(*args, **kwargs))

    ld.fetch_vbpl_moc = fetch_vbpl_qlda
    ld.fetch_vsqi_recent = fetch_vsqi_qlda
    ld.fetch_thuvienphapluat_qlda = fetch_tvpl_qlda
    ld.search_online_all = search_all_no_drafts
    ld.search_online_sites = search_sites_no_drafts

    # Keep sync_source backward-compatible for old integrations, but the
    # user-facing 'all' action never calls the retired draft source.
    def sync_all_no_drafts(repo):
        return [ld.sync_source(repo, source) for source in ("vbpl", "vsqi", "tvpl")]

    ld.sync_all = sync_all_no_drafts
    ld._v622_legal_qlda_installed = True
    ld._v622_legal_qlda_marker = PATCH_MARKER
