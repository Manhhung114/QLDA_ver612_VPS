from __future__ import annotations

"""Make the shared assistant reason from source-defined production totals.

A production worksheet is hierarchical: the same label may occur under CĂN HỘ,
HÀNH LANG and TRỤC ĐỨNG. Averaging every normalized point therefore double-counts
summary/detail rows and can materially disagree with the workbook.  The workbook
already contains official summary blocks; this module extracts those source values
and exposes them to the common assistant without inventing a new average.
"""

import re
import unicodedata
from collections import defaultdict
from typing import Any, Iterable

PATCH_MARKER = "V7 CONTRACTOR DATA OFFICIAL AI V1"

_COORD_RE = re.compile(r"(?:^| \| )([A-Z]+)=(.*?)(?= \| [A-Z]+=|$)")


def _rowdict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    try:
        return {str(k): row[k] for k in row.keys()}
    except Exception:
        try:
            return dict(row)
        except Exception:
            return {}


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("đ", "d")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _column_number(label: str) -> int:
    out = 0
    for ch in str(label or "").upper():
        if not ("A" <= ch <= "Z"):
            continue
        out = out * 26 + (ord(ch) - 64)
    return out


def parse_raw_sheet_content(content: str) -> dict[str, str]:
    return {match.group(1): match.group(2).strip() for match in _COORD_RE.finditer(str(content or ""))}


def _pct(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace("\u00a0", " ").replace(",", ".")
    if not text:
        return None
    is_percent = text.endswith("%")
    if is_percent:
        text = text[:-1].strip()
    try:
        number = float(text)
    except Exception:
        return None
    if is_percent:
        return number
    return number * 100.0 if 0.0 <= number <= 1.0 else number


def _contains_marker(cells: dict[str, str], marker: str) -> bool:
    target = _norm(marker)
    return any(_norm(value) == target or _norm(value).startswith(target + " ") for value in cells.values())


def _total_column(cells: dict[str, str]) -> str:
    candidates = [key for key, value in cells.items() if _norm(value) in {"tong", "total"}]
    if not candidates:
        return ""
    return max(candidates, key=_column_number)


def _work_label(cells: dict[str, str], *, total_col: str = "") -> str:
    # B is the work-item column in SME tower summaries. Fall back to the first
    # textual cell left of the total column while excluding code-like A1/A2...
    preferred = str(cells.get("B") or "").strip()
    if preferred:
        return preferred
    limit = _column_number(total_col) if total_col else 10**9
    for key in sorted(cells, key=_column_number):
        if _column_number(key) >= limit:
            continue
        value = str(cells.get(key) or "").strip()
        if not value or re.fullmatch(r"A\d+", value, flags=re.I):
            continue
        if _pct(value) is not None:
            continue
        return value
    return ""


def extract_official_summaries(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extract workbook-authored totals from synchronized raw SHEET_ROW records."""
    grouped: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    for raw in records:
        row = dict(raw or {})
        if str(row.get("record_type") or "").upper() != "SHEET_ROW":
            continue
        worksheet = str(row.get("worksheet") or "").strip()
        if not worksheet:
            continue
        key = (int(row.get("workspace_project_id") or 0), worksheet)
        grouped[key].append(row)

    out: list[dict[str, Any]] = []
    for (workspace_id, worksheet), raw_rows in grouped.items():
        rows = []
        for raw in sorted(raw_rows, key=lambda x: int(x.get("source_row") or 0)):
            cells = parse_raw_sheet_content(str(raw.get("content") or ""))
            if cells:
                rows.append((int(raw.get("source_row") or 0), cells, raw))
        if not rows:
            continue

        summary: dict[str, Any] = {
            "workspace_project_id": workspace_id,
            "worksheet": worksheet,
            "weighted_total": None,
            "weighted_total_row": None,
            "package_total": None,
            "package_total_row": None,
            "discipline_totals": {},
            "package_totals": {},
        }

        # 1) TỔNG SẢN LƯỢNG: source-authored weighted total (70/20/10) and
        # discipline totals. We deliberately do not average normalized records.
        marker_idx = next((i for i, (_, cells, _) in enumerate(rows) if _contains_marker(cells, "TỔNG SẢN LƯỢNG")), None)
        if marker_idx is not None:
            header_idx = None
            total_col = ""
            for i in range(marker_idx + 1, min(len(rows), marker_idx + 5)):
                candidate = rows[i][1]
                tc = _total_column(candidate)
                normalized_values = [_norm(x) for x in candidate.values()]
                if tc and any("thi cong phan tho" in x or "lap dat thiet bi" in x for x in normalized_values):
                    header_idx, total_col = i, tc
                    break
            if header_idx is not None and total_col:
                for i in range(header_idx + 1, len(rows)):
                    source_row, cells, _ = rows[i]
                    if _contains_marker(cells, "Tổng hợp sản lượng theo đầu mục tiến độ"):
                        break
                    value = _pct(cells.get(total_col))
                    if value is None:
                        continue
                    label = _work_label(cells, total_col=total_col)
                    if summary["weighted_total"] is None and not label:
                        summary["weighted_total"] = float(value)
                        summary["weighted_total_row"] = source_row
                    elif label:
                        summary["discipline_totals"][label] = float(value)

        # 2) Tổng hợp sản lượng theo đầu mục tiến độ: official A1..A8 package
        # totals plus the final workbook total on the last summary row.
        package_marker_idx = next(
            (i for i, (_, cells, _) in enumerate(rows) if _contains_marker(cells, "Tổng hợp sản lượng theo đầu mục tiến độ")),
            None,
        )
        if package_marker_idx is not None:
            header_idx = None
            total_col = ""
            for i in range(package_marker_idx + 1, min(len(rows), package_marker_idx + 5)):
                candidate = rows[i][1]
                tc = _total_column(candidate)
                if tc and any("hang muc cong viec" in _norm(x) for x in candidate.values()):
                    header_idx, total_col = i, tc
                    break
            if header_idx is not None and total_col:
                blank_total_candidates: list[tuple[int, float]] = []
                for i in range(header_idx + 1, len(rows)):
                    source_row, cells, _ = rows[i]
                    value = _pct(cells.get(total_col))
                    if value is None:
                        continue
                    label = _work_label(cells, total_col=total_col)
                    code = str(cells.get("A") or "").strip()
                    if label:
                        summary["package_totals"][f"{code} {label}".strip()] = float(value)
                    else:
                        blank_total_candidates.append((source_row, float(value)))
                if blank_total_candidates:
                    source_row, value = blank_total_candidates[-1]
                    summary["package_total"] = value
                    summary["package_total_row"] = source_row

        if summary["weighted_total"] is not None or summary["package_total"] is not None:
            out.append(summary)

    return out


def _question_terms(question: str) -> list[str]:
    stop = {
        "cho", "toi", "hay", "kiem", "tra", "du", "lieu", "cua", "va", "voi", "theo",
        "trong", "tren", "cac", "nhung", "bao", "nhieu", "tong", "hop", "duoc", "hien", "tai",
        "nha", "thau", "cong", "viec", "san", "luong", "tien", "do", "sheet", "worksheet",
    }
    out: list[str] = []
    for token in _norm(question).split():
        if len(token) >= 2 and token not in stop and token not in out:
            out.append(token)
    return out[:12]


def build_contractor_data_official_appendix(builder, project_id: int, question: str = "") -> str:
    """Correct Data Hub context: official totals + source-row-preserving details."""
    try:
        from qlda.runtime_core.contractor_access_control import _AI_WORKSPACE_SCOPE
        restricted_workspace = _AI_WORKSPACE_SCOPE.get()
    except Exception:
        restricted_workspace = None
    try:
        from qlda.runtime_core.contractor_workspace import resolve_master_project_id_connection
    except Exception:
        resolve_master_project_id_connection = None

    try:
        with builder.connect() as connection:
            master_project_id = int(project_id)
            if callable(resolve_master_project_id_connection):
                try:
                    master_project_id = int(resolve_master_project_id_connection(connection, int(project_id)))
                except Exception:
                    pass

            where = "r.master_project_id=?"
            params: list[Any] = [master_project_id]
            if restricted_workspace:
                where += " AND r.workspace_project_id=?"
                params.append(int(restricted_workspace))

            summary_row = _rowdict(connection.execute(
                f"""SELECT COUNT(*) AS record_count, COUNT(DISTINCT r.source_id) AS source_count,
                    COUNT(DISTINCT r.worksheet) AS worksheet_count, MAX(r.synced_at) AS last_sync
                    FROM contractor_data_records r WHERE {where}""",
                params,
            ).fetchone())
            if int(summary_row.get("record_count") or 0) <= 0:
                return ""

            contractor_labels: dict[int, str] = {}
            try:
                rows = connection.execute(
                    "SELECT workspace_project_id,contractor_code,contractor_name FROM contractor_data_spaces WHERE master_project_id=?",
                    (master_project_id,),
                ).fetchall()
                for raw in rows:
                    row = _rowdict(raw)
                    wid = int(row.get("workspace_project_id") or 0)
                    contractor_labels[wid] = f"{row.get('contractor_code','')} - {row.get('contractor_name','')}".strip(" -") or f"Workspace {wid}"
            except Exception:
                pass

            raw_rows = [
                _rowdict(x)
                for x in connection.execute(
                    f"""SELECT r.workspace_project_id,r.source_name,r.worksheet,r.record_type,r.content,
                        r.source_row,r.synced_at FROM contractor_data_records r
                        WHERE {where} AND r.record_type='SHEET_ROW'
                        ORDER BY r.workspace_project_id,r.worksheet,r.source_row LIMIT 30000""",
                    params,
                ).fetchall()
            ]
            official = extract_official_summaries(raw_rows)

            lines = [
                "",
                "## KHO DỮ LIỆU NHÀ THẦU – NGỮ CẢNH CHÍNH THỨC TỪ FILE NGUỒN",
                "QUY TẮC BẮT BUỘC: Không lấy AVG của toàn bộ record PRODUCTION để kết luận sản lượng. "
                "Worksheet có cấu trúc phân cấp và nhiều nhãn lặp ở các dòng nguồn khác nhau; AVG sẽ double-count và sai file. "
                "Khi có số 'TỔNG SẢN LƯỢNG' hoặc 'Tổng hợp sản lượng theo đầu mục tiến độ' dưới đây, phải ưu tiên đúng số đó và nêu rõ loại tổng.",
                f"records={int(summary_row.get('record_count') or 0):,} | nguồn={int(summary_row.get('source_count') or 0):,} | worksheet={int(summary_row.get('worksheet_count') or 0):,} | sync gần nhất={summary_row.get('last_sync') or ''}",
            ]

            if official:
                lines.append("### TỔNG CHÍNH THỨC THEO WORKSHEET")
                for item in official:
                    wid = int(item.get("workspace_project_id") or 0)
                    label = contractor_labels.get(wid, f"Workspace {wid}")
                    weighted = item.get("weighted_total")
                    package = item.get("package_total")
                    parts = [f"[OFFICIAL] {label} | worksheet={item.get('worksheet','')}"]
                    if weighted is not None:
                        parts.append(f"TỔNG SẢN LƯỢNG={float(weighted):.2f}% (dòng {item.get('weighted_total_row')})")
                    if package is not None:
                        parts.append(f"Tổng theo đầu mục tiến độ={float(package):.2f}% (dòng {item.get('package_total_row')})")
                    lines.append(" | ".join(parts))
                    for name, value in (item.get("discipline_totals") or {}).items():
                        lines.append(f"[OFFICIAL-DISCIPLINE] {label} | {item.get('worksheet','')} | {name}={float(value):.2f}%")

            terms = _question_terms(question)
            if terms:
                detail_raw = connection.execute(
                    f"""SELECT r.workspace_project_id,r.source_name,r.category,r.worksheet,r.record_type,
                        r.record_ref,r.work_item,r.zone,r.progress_percent,r.content,r.source_row,r.synced_at
                        FROM contractor_data_records r WHERE {where}
                        ORDER BY r.synced_at DESC,r.source_row DESC LIMIT 8000""",
                    params,
                ).fetchall()
                scored: list[tuple[int, dict[str, Any]]] = []
                for raw in detail_raw:
                    row = _rowdict(raw)
                    haystack = _norm(" ".join(str(row.get(k) or "") for k in (
                        "source_name", "category", "worksheet", "record_type", "work_item", "zone", "content"
                    )))
                    score = sum(1 for term in terms if term in haystack)
                    if score:
                        scored.append((score, row))
                scored.sort(key=lambda item: (item[0], int(item[1].get("source_row") or 0)), reverse=True)
                if scored:
                    lines.append("### RECORDS PHÙ HỢP CÂU HỎI – GIỮ NGUYÊN DÒNG NGUỒN")
                    for _, row in scored[:160]:
                        wid = int(row.get("workspace_project_id") or 0)
                        label = contractor_labels.get(wid, f"Workspace {wid}")
                        content = str(row.get("content") or "").strip().replace("\n", " ")
                        if len(content) > 500:
                            content = content[:497] + "..."
                        progress = row.get("progress_percent")
                        progress_text = "" if progress is None else f" | tiến độ={float(progress):.2f}%"
                        lines.append(
                            f"[DATA-HUB-ROW] {label} | worksheet={row.get('worksheet','')} | dòng={row.get('source_row','')} | "
                            f"công tác={row.get('work_item','')} | chiều={row.get('zone','')}{progress_text} | {content}"
                        )

            return "\n".join(lines)
    except Exception:
        return ""


def install_contractor_data_official_ai() -> None:
    """Replace the shared Data Hub appendix before/after its builder wrapper installs."""
    from qlda.runtime_core import contractor_data_shared_ai as shared_ai

    shared_ai.build_contractor_data_hub_appendix = build_contractor_data_official_appendix
    shared_ai._qlda_official_ai_v1 = True
    shared_ai._qlda_official_ai_marker = PATCH_MARKER


__all__ = [
    "PATCH_MARKER",
    "parse_raw_sheet_content",
    "extract_official_summaries",
    "build_contractor_data_official_appendix",
    "install_contractor_data_official_ai",
]
