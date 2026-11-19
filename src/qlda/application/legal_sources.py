"""Official-source legal catalog for Construction/QLDA and MEPF-PCCC.

Additive service boundary: does not change legacy runtime or install monkey patches.
Links prove *where to verify*, never whether a rule is mandatory for a project.
The official-site check is a provenance filter, not a claim of current validity.
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit

SCHEMA = "qlda-official-legal-v1"
CONSTRUCTION = "Xây dựng – Quản lý dự án"
MEPF = "MEPF – Phòng cháy chữa cháy"
OFFICIAL_HOSTS = frozenset({
    "vbpl.vn", "congbao.chinhphu.vn", "vanban.chinhphu.vn", "chinhphu.vn",
    "bxd.gov.vn", "moc.gov.vn", "tieuchuan.vsqi.gov.vn",
    "tieuchuanxaydung.vsqi.gov.vn", "bocongan.gov.vn", "moj.gov.vn",
})

# Exact-number backfill is a safeguard when broad search misses essential documents.
# These are *discovery* anchors, NOT automatic legal-effectiveness declarations.
PRIORITY_NUMBERS: dict[str, tuple[str, ...]] = {
    CONSTRUCTION: (
        "135/2025/QH15", "206/2026/NĐ-CP", "207/2026/NĐ-CP",
        "210/2026/NĐ-CP", "32/2026/TT-BXD", "TCVN 2737:2023",
        "TCVN 5574:2018", "TCVN 5575:2024", "TCVN 9362:2026",
    ),
    MEPF: (
        "55/2024/QH15", "105/2025/NĐ-CP", "QCVN 06:2022/BXD",
        "TCVN 5687:2024", "TCVN 3890:2023", "TCVN 7568-14:2025",
    ),
}
TOPIC_QUERIES: dict[str, tuple[str, ...]] = {
    CONSTRUCTION: (
        "luật xây dựng quản lý dự án", "quản lý chi phí đầu tư xây dựng",
        "quản lý chất lượng thi công bảo trì công trình", "hợp đồng xây dựng",
        "khảo sát thiết kế kết cấu xây dựng", "bê tông cốt thép nền móng",
        "nghiệm thu xây dựng vật liệu xây dựng", "quản lý an toàn công trường",
    ),
    MEPF: (
        "hệ thống điện công trình", "thông gió điều hòa không khí",
        "cấp nước thoát nước công trình", "phòng cháy chữa cháy cứu nạn",
        "hệ thống báo cháy chữa cháy tự động", "quy chuẩn an toàn cháy nhà",
    ),
}
STANDARD = re.compile(r"^(?:TCVN\s*\d+(?:-\d+)*:\d{4}|QCVN\s*\d+(?:[.-]\d+)?:\d{4}/[A-Z]+)$", re.I)
NUMBER = re.compile(r"^[0-9A-ZÀ-Ỹ._/-]{5,90}$", re.I)


def official_url(value: str) -> bool:
    try:
        url = urlsplit(str(value))
        return (url.scheme == "https" and not url.username and not url.password
                and url.port in (None, 443) and (url.hostname or "").lower() in OFFICIAL_HOSTS)
    except (ValueError, TypeError):
        return False


def _is_draft(row: dict[str, Any]) -> bool:
    if row.get("is_draft"):
        return True
    words = " ".join(str(row.get(k) or "") for k in ("title", "status", "category", "note", "ten", "tinhTrang"))
    return "dự thảo" in words.casefold() or "du thao" in words.casefold()


def normalize_official(row: dict[str, Any], field: str) -> dict[str, Any] | None:
    """Map existing QLDA metadata to the shared app schema; fail closed on source."""
    if field not in (CONSTRUCTION, MEPF) or _is_draft(row):
        return None
    num = str(row.get("soHieu") or row.get("number") or "").strip()
    title = str(row.get("ten") or row.get("title") or "").strip()
    url = str(row.get("duongDan") or row.get("source_url") or "").strip()
    if not num or not title or len(title) > 450 or len(num) > 90 or not official_url(url):
        return None
    if not (STANDARD.fullmatch(num) or NUMBER.fullmatch(num.replace(" ", ""))):
        return None
    if num.upper().startswith("TCVN") and urlsplit(url).hostname != "tieuchuan.vsqi.gov.vn":
        return None
    is_std = num.upper().startswith(("TCVN", "QCVN"))
    # Never automatically copy a source's stale "Còn hiệu lực" into new discovery results.
    status = str(row.get("tinhTrang") or "Cần kiểm tra hiệu lực tại nguồn").strip()
    return {
        "soHieu": num, "ten": title, "tenAnh": str(row.get("tenAnh") or ""),
        "linhVuc": field,
        "nhom": str(row.get("nhom") or ("Tiêu chuẩn / Quy chuẩn" if is_std else "Văn bản QLDA / Công trình")),
        "loai": str(row.get("loai") or ("Tiêu chuẩn TCVN" if num.startswith("TCVN") else "Quy chuẩn QCVN" if num.startswith("QCVN") else row.get("category") or "Văn bản pháp luật")),
        "duongDan": url, "nguon": str(row.get("nguon") or row.get("source_name") or urlsplit(url).hostname),
        "tinhTrang": status,
        "kiemChung": str(row.get("kiemChung") or "Đường dẫn cơ quan chính thống; cần xác minh nội dung và hiệu lực"),
        "ngayRaSoat": str(row.get("ngayRaSoat") or ""),
        "ghiChu": str(row.get("ghiChu") or row.get("note") or "")[:1000],
    }


def reconcile(rows: Iterable[tuple[dict[str, Any], str]], *, reviewed_at: str = "") -> dict[str, Any]:
    """Deduplicate by field + exact document number; preserve curated priority."""
    unique: dict[tuple[str, str], dict[str, Any]] = {}
    for raw, field in rows:
        candidate = normalize_official(raw, field)
        if candidate:
            key = (field, re.sub(r"\s+", "", candidate["soHieu"]).upper())
            unique.setdefault(key, candidate)
    return {
        "schema": SCHEMA, "reviewedAt": reviewed_at,
        "statusPolicy": "Nguồn chính thống không đồng nghĩa văn bản còn hiệu lực; kiểm tra sửa đổi và chuyển tiếp.",
        "vanBan": list(unique.values()),
    }


def collect_live(standards_search: Callable[[str], Iterable[dict[str, Any]]],
                 laws_search: Callable[[str], Iterable[dict[str, Any]]],
                 *, limit_per_query: int = 12) -> list[tuple[dict[str, Any], str]]:
    """Search both branches. Caller injects existing QLDA source adapters."""
    results: list[tuple[dict[str, Any], str]] = []
    for field in (CONSTRUCTION, MEPF):
        queries = PRIORITY_NUMBERS[field] + TOPIC_QUERIES[field]
        for query in queries:
            use_standard = query.startswith(("TCVN", "QCVN")) or any(s in query for s in ("bê tông", "thông gió", "điện công trình", "báo cháy"))
            search_fn = standards_search if use_standard else laws_search
            try:
                found = search_fn(query)
            except (ValueError, RuntimeError, OSError, TimeoutError):
                continue
            for raw in list(found or [])[:limit_per_query]:
                if isinstance(raw, dict):
                    # Explicit sector tagging; ignore third-party and draft records later.
                    results.append(({**raw, "tinhTrang": "Cần kiểm tra hiệu lực tại nguồn"}, field))
    return results


def main(
    argv: list[str] | None = None,
    *,
    standards_search: Callable[[str], Iterable[dict[str, Any]]] | None = None,
    laws_search: Callable[[str], Iterable[dict[str, Any]]] | None = None,
) -> int:
    parser = argparse.ArgumentParser(description="Official catalog metadata; no full-text TCVN distribution")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=Path, help="Last curated catalog to preserve")
    parser.add_argument("--online", action="store_true", help="Search existing official QLDA adapters")
    args = parser.parse_args(argv)
    initial: list[tuple[dict[str, Any], str]] = []
    if args.seed and args.seed.is_file():
        source = json.loads(args.seed.read_text(encoding="utf-8"))
        initial = [(r, r.get("linhVuc", "")) for r in source.get("vanBan", [])]
    fresh = []
    if args.online:
        if standards_search is None or laws_search is None:
            parser.error(
                "Online search requires injected search providers. "
                "Run: python -m qlda.infrastructure.legal_catalog_cli --online ..."
            )
        fresh = collect_live(standards_search, laws_search)
    catalog = reconcile([*initial, *fresh], reviewed_at=(json.loads(args.seed.read_text(encoding="utf-8")).get("reviewedAt", "") if args.seed and args.seed.is_file() else ""))
    if not catalog["vanBan"]:
        raise SystemExit("Không có bản ghi nguồn chính thống, giữ nguyên bản đã lưu.")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Official catalog: {len(catalog['vanBan'])} records, {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
