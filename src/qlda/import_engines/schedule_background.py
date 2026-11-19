from __future__ import annotations

import hashlib
import math
import os
import unicodedata
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable


PATCH_VERSION = "V6.24.5 SCHEDULE EXCEL BACKGROUND PIPELINE"

ProgressFn = Callable[[int, str, str], None]
CancelFn = Callable[[], bool]


def _sha256_file(path: Path, chunk_size: int = 4 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                return digest.hexdigest()
            digest.update(chunk)


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(text.lower().replace("đ", "d").strip().split())


ALIASES = {
    "wbs": "wbs", "stt": "wbs",
    "cong viec": "name", "hang muc cong viec": "name", "ten cong viec": "name",
    "name": "name", "task name": "name",
    "bat dau": "start", "ngay bat dau": "start", "start": "start",
    "ket thuc": "end", "ngay ket thuc": "end", "finish": "end",
    "so ngay": "duration", "thoi luong": "duration", "duration": "duration",
    "kh %": "planned", "tt %": "actual",
    "phu trach": "responsible", "nguoi phu trach": "responsible",
    "predecessor": "predecessor", "ghi chu": "note",
}


def _date_text(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value)):
        try:
            from openpyxl.utils.datetime import from_excel

            converted = from_excel(float(value))
            return converted.date().isoformat() if isinstance(converted, datetime) else converted.isoformat()
        except Exception:
            return ""
    text = str(value or "").strip()
    if not text:
        return ""
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(text[:10], fmt).date().isoformat()
        except Exception:
            pass
    try:
        return datetime.fromisoformat(text).date().isoformat()
    except Exception:
        return ""


def _percent(value: Any, default: int) -> int:
    if value in (None, ""):
        return max(0, min(100, int(default)))
    try:
        number = float(value)
        if 0 <= number <= 1 and not float(number).is_integer():
            number *= 100
        return max(0, min(100, int(round(number))))
    except Exception:
        return max(0, min(100, int(default)))


def _wbs_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _duration(value: Any, start: str, end: str) -> float:
    if value not in (None, ""):
        try:
            number = float(value)
            if math.isfinite(number) and number >= 0:
                return number
        except (TypeError, ValueError):
            pass
    if start and end:
        return float((date.fromisoformat(end) - date.fromisoformat(start)).days + 1)
    return 0.0


def _outline(name: str, wbs: str) -> int:
    # Excel author's indentation is a presentation hint, not a scheduling rule.
    spaces = len(name) - len(name.lstrip(" \t"))
    from_indent = 1 + spaces // 3
    from_wbs = len(wbs.split(".")) if wbs else 1
    return min(8, max(from_indent, from_wbs))


def _header_columns(row: tuple[Any, ...]) -> dict[str, int]:
    columns: dict[str, int] = {}
    for index, value in enumerate(row):
        field = ALIASES.get(_norm(value))
        if field and field not in columns:
            columns[field] = index
    return columns


def _summary_date_conflicts(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Flag explicit WBS parents whose dated descendants exceed the parent's range.

    Only WBS with at least two separators are used for a group-without-child-WBS
    scan, because indentation alone is not reliable in contractor spreadsheets.
    These warnings do NOT rewrite dates.
    """
    conflicts: list[dict[str, Any]] = []
    for i, parent in enumerate(tasks):
        wbs = str(parent.get("wbs") or "")
        if wbs.count(".") < 2 or not parent["start_date"] or not parent["end_date"]:
            continue
        for child in tasks[i + 1:]:
            child_wbs = str(child.get("wbs") or "")
            if child_wbs and not child_wbs.startswith(wbs + "."):
                break
            if not child["start_date"] or not child["end_date"]:
                continue
            if child["start_date"] < parent["start_date"] or child["end_date"] > parent["end_date"]:
                conflicts.append({
                    "parent_row": parent["source_row_no"], "parent": parent["name"],
                    "parent_start": parent["start_date"], "parent_end": parent["end_date"],
                    "child_row": child["source_row_no"], "child": child["name"],
                    "child_start": child["start_date"], "child_end": child["end_date"],
                })
                if len(conflicts) >= 100:
                    return conflicts
    return conflicts


def parse_schedule_excel_path(
    path: str | Path,
    filename: str = "TienDo.xlsx",
    *,
    status_date: date | str | None = None,
    progress: ProgressFn | None = None,
    cancelled: CancelFn | None = None,
) -> dict[str, Any]:
    """Read standard or multi-heading contractor Excel without rescheduling dates.

    Empty-date section headings are retained. Rows with only one valid date, or
    invalid/reversed dates, are rejected with the original Excel row number.
    """
    from openpyxl import load_workbook
    from qlda.runtime_core.project_store import planned_progress

    source = Path(path)
    if not source.exists() or not source.is_file():
        raise FileNotFoundError(f"Không tìm thấy file tiến độ trên VPS: {source}")
    if source.suffix.lower() not in {".xlsx", ".xlsm"}:
        raise ValueError("Excel tiến độ hỗ trợ .xlsx/.xlsm. Hãy chuyển file .xls sang .xlsx.")
    size = source.stat().st_size
    if not size:
        raise ValueError("File Excel tiến độ đang trống.")
    try:
        max_mb = max(25, min(int(os.environ.get("QLDA_SCHEDULE_WORKER_MAX_FILE_MB", "512") or 512), 2048))
    except Exception:
        max_mb = 512
    if size > max_mb * 1024 * 1024:
        raise ValueError(f"File tiến độ vượt giới hạn worker {max_mb} MB.")

    report_date = _date_text(status_date) or date.today().isoformat()
    report_day = date.fromisoformat(report_date)
    if progress:
        progress(8, "Đang kiểm tra SHA file tiến độ", "")
    digest = _sha256_file(source)
    if cancelled and cancelled():
        raise InterruptedError("Job tiến độ đã được yêu cầu hủy.")
    if progress:
        progress(15, "Đang nhận diện tiêu đề và ngày Excel", "")
    try:
        workbook = load_workbook(str(source), data_only=True, read_only=True, keep_links=False)
    except Exception as exc:
        raise ValueError(f"Không đọc được Excel tiến độ: {exc}") from exc

    try:
        sheet = None
        columns: dict[str, int] = {}
        header_row = 0
        for candidate in workbook.worksheets:
            if getattr(candidate, "sheet_state", "visible") != "visible":
                continue
            # Many construction schedules have 5-15 metadata/title rows.
            for row_no, row in enumerate(candidate.iter_rows(min_row=1, max_row=min(80, candidate.max_row or 80), values_only=True), start=1):
                fields = _header_columns(tuple(row))
                if {"name", "start", "end"}.issubset(fields):
                    sheet, columns, header_row = candidate, fields, row_no
                    break
            if sheet is not None:
                break
        if sheet is None:
            raise ValueError("Không nhận diện được tiêu đề Công việc/Hạng mục công việc, Bắt đầu, Kết thúc ở 80 dòng đầu.")

        tasks: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        source_rows = 0
        undated = 0
        duration_differences = 0

        for row_no, row in enumerate(sheet.iter_rows(min_row=header_row + 1, values_only=True), start=header_row + 1):
            if cancelled and row_no % 250 == 0 and cancelled():
                raise InterruptedError("Job tiến độ đã được yêu cầu hủy.")
            values = tuple(row)
            if not any(value not in (None, "") for value in values):
                continue
            def cell(field: str, default: Any = None) -> Any:
                index = columns.get(field)
                return default if index is None or index >= len(values) else values[index]

            raw_name = str(cell("name") or "")
            name = raw_name.strip()
            # Contractor sheets place a long multi-line "Ghi chú:" footer and
            # signature line after the last task. They are not invalid tasks.
            wbs_or_note = _wbs_text(cell("wbs"))
            if not name and (
                wbs_or_note.casefold().startswith("ghi chú:")
                or wbs_or_note.casefold().startswith("đại diện")
            ):
                break
            source_rows += 1
            if not name:
                skipped.append({"row_no": row_no, "reason": "Dòng không có tên công việc"})
                continue
            raw_start, raw_end = cell("start"), cell("end")
            start, end = _date_text(raw_start), _date_text(raw_end)
            if (raw_start not in (None, "") and not start) or (raw_end not in (None, "") and not end):
                skipped.append({"row_no": row_no, "reason": "Ngày không hợp lệ"})
                continue
            if bool(start) != bool(end) or (start and end and end < start):
                skipped.append({"row_no": row_no, "reason": "Thiếu một mốc ngày hoặc kết thúc trước bắt đầu"})
                continue
            if not start:
                undated += 1
            wbs = _wbs_text(cell("wbs"))
            duration = _duration(cell("duration"), start, end)
            if start and end and cell("duration") not in (None, ""):
                # Do not force Excel's elapsed-day duration into inclusive dates.
                inclusive = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
                if abs(duration - inclusive) > 0.000001:
                    duration_differences += 1
            tasks.append({
                "source_row_no": row_no, "wbs": wbs, "name": name,
                "responsible": str(cell("responsible") or "").strip(),
                "start_date": start, "end_date": end, "duration": duration,
                "planned_progress": _percent(cell("planned"), planned_progress(start, end, report_day) if start else 0),
                "actual_progress": _percent(cell("actual"), 0),
                "predecessor": str(cell("predecessor") or "").strip(),
                "note": str(cell("note") or "").strip(),
                "outline_level": _outline(raw_name, wbs),
                "is_summary": 0, "is_milestone": int(bool(start and end and start == end)),
            })
            if progress and row_no % 500 == 0:
                progress(min(68, 20 + int(row_no * 48 / max(1, sheet.max_row or row_no))), "Đang quét công việc", sheet.title)

        # In an outline, a row followed by a deeper-indented task is a group.
        for i in range(len(tasks) - 1):
            if tasks[i + 1]["outline_level"] > tasks[i]["outline_level"]:
                tasks[i]["is_summary"] = 1
        warnings = []
        if skipped:
            warnings.append(f"Đã bỏ qua {len(skipped):,} dòng không hợp lệ, có số dòng Excel để đối chiếu.")
        if undated:
            warnings.append(f"{undated:,} hạng mục chưa có đủ hai mốc ngày được giữ trống, không tạo ngày giả.")
        if duration_differences:
            warnings.append(f"{duration_differences:,} hạng mục có Số ngày khác số ngày tính bao gồm cả ngày đầu; giữ nguyên số Excel.")
        conflicts = _summary_date_conflicts(tasks)
        if conflicts:
            warnings.append(f"{len(conflicts):,} mốc nhóm WBS chưa bao trùm mốc công việc bên trong; không tự điều chỉnh.")
        sheet_name = str(sheet.title)
    finally:
        workbook.close()

    if not tasks:
        raise ValueError("Không có công việc hợp lệ để ghi cơ sở dữ liệu.")
    result = {
        "pipeline": PATCH_VERSION,
        "filename": Path(str(filename or source.name)).name,
        "batch_id": digest, "source_sha256": digest, "source_file_size": size,
        "sheet_name": sheet_name, "header_row": header_row, "status_date": report_date,
        "tasks": tasks, "task_count": len(tasks), "detail_line_count": len(tasks),
        "source_row_count": source_rows, "skipped_rows": len(skipped),
        "skipped_details": skipped[:100], "undated_count": undated,
        "dated_count": len(tasks) - undated,
        "duration_inclusive_mismatch_count": duration_differences,
        "summary_date_conflicts": conflicts, "warnings": warnings,
    }
    if progress:
        progress(72, f"Đã quét {len(tasks):,} hạng mục · {undated:,} chưa có ngày", sheet_name)
    return result
