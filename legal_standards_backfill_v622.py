from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Callable


PATCH_MARKER = "V6.22 LEGAL STANDARDS V1 QCVN TCVN BACKFILL"

# Quy chuẩn Bộ Xây dựng thường có dạng QCVN 06:2022/BXD. Cho phép phần số có
# dấu '-' hoặc '.' để không loại các họ quy chuẩn có phần/phân nhóm.
QCVN_BXD_RE = re.compile(
    r"\bQCVN\s*[0-9]+(?:[.-][0-9]+)?\s*:\s*(\d{4})\s*/\s*BXD\b",
    re.I,
)
TCVN_RE = re.compile(r"\bTCVN\s*[0-9]+(?:-[0-9]+)?\s*:\s*(\d{4})\b", re.I)

QCVN_START_YEAR = 2010
QCVN_PER_YEAR = 50
TCVN_FAMILY_LIMIT = 45

# Đây chỉ là regression anchors để cứu các chuẩn/quy chuẩn nền tảng khi family
# search bị giới hạn trang kết quả. Metadata/URL vẫn phải lấy online từ nguồn.
QCVN_PRIORITY_EXACT_NUMBERS = (
    "QCVN 01:2021/BXD",
    "QCVN 04:2021/BXD",
    "QCVN 06:2022/BXD",
)
TCVN_PRIORITY_EXACT_NUMBERS = (
    "TCVN 2737:2023",
    "TCVN 3890:2023",
    "TCVN 5687:2024",
)

# Bổ sung theo họ nghiệp vụ xây dựng. Nguồn chính của TCVN vẫn là VSQI/ICS;
# các query này chỉ tăng recall cho tiêu chuẩn cũ hoặc nằm sâu trong kết quả.
TCVN_CONSTRUCTION_FAMILY_QUERIES = (
    "TCVN xây dựng nhà công trình",
    "TCVN kết cấu bê tông thép nền móng",
    "TCVN phòng cháy chữa cháy nhà công trình",
    "TCVN thông gió điều hòa không khí công trình",
    "TCVN cấp nước thoát nước công trình",
    "TCVN hệ thống điện công trình",
    "TCVN thang máy công trình",
    "TCVN vật liệu xây dựng",
)


def _is_draft(doc: Any) -> bool:
    try:
        item = dict(doc)
    except Exception:
        return False
    if int(item.get("is_draft", 0) or 0) != 0:
        return True
    text = " ".join(str(item.get(k, "") or "") for k in ("category", "status", "title", "note"))
    return "dự thảo" in text.casefold()


def _text(doc: Any) -> str:
    try:
        item = dict(doc)
    except Exception:
        return ""
    return " ".join(str(item.get(k, "") or "") for k in ("number", "title", "note", "field"))


def _normalize_number(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).upper()


def _is_qcvn_bxd(doc: Any) -> bool:
    return not _is_draft(doc) and bool(QCVN_BXD_RE.search(_text(doc)))


def _is_tcvn(doc: Any) -> bool:
    return not _is_draft(doc) and bool(TCVN_RE.search(_text(doc)))


def _year_from(regex: re.Pattern[str], doc: Any) -> int | None:
    match = regex.search(_text(doc))
    if not match:
        return None
    try:
        return int(match.group(1))
    except Exception:
        return None


def _doc_key(doc: dict) -> str:
    number = _normalize_number(doc.get("number", ""))
    if number:
        return "n:" + number
    url = str(doc.get("source_url", "") or "").split("#", 1)[0].rstrip("/").lower()
    if url:
        return "u:" + url
    title = re.sub(r"\s+", " ", str(doc.get("title", "") or "")).strip().casefold()
    return "t:" + title[:250]


def _dedupe_priority(primary, secondary, limit: int) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for raw in list(primary or []) + list(secondary or []):
        try:
            doc = dict(raw)
        except Exception:
            continue
        if _is_draft(doc):
            continue
        key = _doc_key(doc)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(doc)
        if len(out) >= max(1, int(limit)):
            break
    return out


def _safe_search(search_fn: Callable[..., list[dict]], query: str, limit: int) -> list[dict]:
    try:
        return [dict(x) for x in (search_fn(query, limit=max(1, int(limit))) or [])]
    except Exception:
        return []


def _exact_backfill(
    search_fn: Callable[..., list[dict]],
    numbers: tuple[str, ...],
    predicate: Callable[[Any], bool],
) -> list[dict]:
    found: list[dict] = []
    for number in numbers:
        wanted = _normalize_number(number)
        for doc in _safe_search(search_fn, number, 20):
            if not predicate(doc):
                continue
            number_value = _normalize_number(doc.get("number", ""))
            title_value = _normalize_number(doc.get("title", ""))
            if number_value == wanted or wanted in title_value:
                found.append(doc)
                break
    return found


def _collect_qcvn_bxd(
    search_fn: Callable[..., list[dict]],
    *,
    start_year: int = QCVN_START_YEAR,
    end_year: int | None = None,
    per_year: int = QCVN_PER_YEAR,
) -> list[dict]:
    """Quét QCVN/BXD theo họ số hiệu + năm và exact-number backfill."""
    current_year = datetime.now().year
    end = min(int(end_year or current_year), current_year)
    start = min(max(2000, int(start_year)), end)
    years = list(range(end, start - 1, -1))
    found: list[dict] = []

    def one(year: int) -> list[dict]:
        queries = (
            f":{year}/BXD",
            f"QCVN {year}/BXD",
            f"Quy chuẩn kỹ thuật quốc gia Bộ Xây dựng {year}",
        )
        year_docs: list[dict] = []
        for idx, query in enumerate(queries):
            docs = _safe_search(search_fn, query, per_year)
            selected = [
                doc for doc in docs
                if _is_qcvn_bxd(doc) and _year_from(QCVN_BXD_RE, doc) == year
            ]
            year_docs.extend(selected)
            # Nếu family suffix đã bắt được nhiều QCVN đúng năm thì tránh query
            # tự nhiên rộng để giảm tải nguồn.
            if idx < 2 and len(_dedupe_priority(year_docs, [], 9999)) >= 3:
                break
        return _dedupe_priority(year_docs, [], limit=max(1, int(per_year)))

    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="qcvn-bxd-family") as pool:
        futures = {pool.submit(one, year): year for year in years}
        for future in as_completed(futures):
            try:
                found.extend(future.result())
            except Exception:
                pass

    exact = _exact_backfill(search_fn, QCVN_PRIORITY_EXACT_NUMBERS, _is_qcvn_bxd)
    return _dedupe_priority(exact, found, limit=max(1, len(exact) + len(found) or 1))


def _collect_tcvn_construction(
    search_fn: Callable[..., list[dict]],
    *,
    family_limit: int = TCVN_FAMILY_LIMIT,
) -> list[dict]:
    """Bổ sung TCVN xây dựng bằng family queries + exact-number backfill.

    Bulk sync VSQI/ICS vẫn là lớp bao phủ chính; lớp này cứu các TCVN cũ hoặc
    tiêu chuẩn quan trọng bị rơi khỏi số trang đang quét.
    """
    found: list[dict] = []

    def one(query: str) -> list[dict]:
        docs = _safe_search(search_fn, query, family_limit)
        return [doc for doc in docs if _is_tcvn(doc)]

    with ThreadPoolExecutor(max_workers=3, thread_name_prefix="tcvn-construction-family") as pool:
        futures = {pool.submit(one, query): query for query in TCVN_CONSTRUCTION_FAMILY_QUERIES}
        for future in as_completed(futures):
            try:
                found.extend(future.result())
            except Exception:
                pass

    # Với các tiêu chuẩn nền tảng, hỏi thêm theo họ số hiệu (không năm) để lấy
    # lịch sử phiên bản, rồi hỏi đúng số hiệu hiện hành để không bị mất silently.
    family_numbers: list[dict] = []
    for number in TCVN_PRIORITY_EXACT_NUMBERS:
        base = re.sub(r":\d{4}\s*$", "", number)
        family_numbers.extend(doc for doc in _safe_search(search_fn, base, 20) if _is_tcvn(doc))

    exact = _exact_backfill(search_fn, TCVN_PRIORITY_EXACT_NUMBERS, _is_tcvn)
    return _dedupe_priority(
        exact,
        family_numbers + found,
        limit=max(1, len(exact) + len(family_numbers) + len(found) or 1),
    )


def install_legal_standard_backfill() -> None:
    """Wrap QLXD sync so QCVN/TCVN get the same anti-miss strategy as TT-BXD."""
    import legal_documents as ld

    if getattr(ld, "_v622_legal_standards_installed", False):
        return

    # Đảm bảo lớp QLXD/TT-BXD được cài trước, sau đó mới bọc hai nguồn standards.
    if not getattr(ld, "_v622_legal_qlda_installed", False):
        from legal_qlda_v622 import install_legal_qlda
        install_legal_qlda()

    original_vsqi = ld.fetch_vsqi_recent
    original_tvpl = ld.fetch_thuvienphapluat_qlda
    search_vsqi = ld.search_vsqi
    search_tvpl = ld.search_thuvienphapluat

    def fetch_vsqi_with_tcvn_backfill(
        pages: int = 2,
        only_construction: bool = True,
        enrich_limit: int = 12,
        max_results: int = 220,
    ):
        general = original_vsqi(
            pages=pages,
            only_construction=only_construction,
            enrich_limit=enrich_limit,
            max_results=max_results,
        )
        if not only_construction:
            return general
        backfill = _collect_tcvn_construction(search_vsqi)
        return _dedupe_priority(backfill, general, limit=max_results)

    def fetch_tvpl_with_qcvn_backfill(
        limit: int = 700,
        per_query: int = 18,
        detail_limit: int = 50,
    ):
        qcvn = _collect_qcvn_bxd(search_tvpl)
        general = original_tvpl(limit=limit, per_query=per_query, detail_limit=detail_limit)
        # QCVN backfill được đặt trước khi cắt limit, giống TT-BXD V4.
        return _dedupe_priority(qcvn, general, limit=limit)

    ld.fetch_vsqi_recent = fetch_vsqi_with_tcvn_backfill
    ld.fetch_thuvienphapluat_qlda = fetch_tvpl_with_qcvn_backfill
    ld._v622_legal_standards_installed = True
    ld._v622_legal_standards_marker = PATCH_MARKER
