from __future__ import annotations

import re
from datetime import date, datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo


PATCH_MARKER = "V6.22 VIETNAM DATETIME PRESENTATION V1"
VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")

# These names are display fields, not business values.  We only rewrite a value
# when both the column name looks temporal and the value itself can be parsed as
# a date/time.  That keeps IDs, codes, quantities and free text untouched.
_TEMPORAL_NAME_PARTS = (
    "created_at",
    "updated_at",
    "uploaded_at",
    "last_seen_at",
    "expires_at",
    "ended_at",
    "completed_at",
    "closed_at",
    "start_at",
    "due_at",
    "timestamp",
    "datetime",
    "date",
    "time",
    "thời gian",
    "ngày",
    "đăng nhập",
    "hoạt động gần nhất",
    "hết hạn",
    "hạn hoàn thành",
    "cập nhật lúc",
    "tạo lúc",
    "ban hành",
    "hiệu lực",
    "kết thúc lúc",
)

_DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$|^\d{1,2}[/-]\d{1,2}[/-]\d{4}$")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _is_temporal_column(name: Any) -> bool:
    text = _text(name).lower().replace("-", "_")
    if not text:
        return False
    return any(part in text for part in _TEMPORAL_NAME_PARTS)


def _parse_string(value: str) -> tuple[datetime | date | None, bool]:
    text = value.strip()
    if not text:
        return None, False

    date_only = bool(_DATE_ONLY_RE.match(text))
    if date_only:
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
            try:
                return datetime.strptime(text, fmt).date(), True
            except ValueError:
                pass

    # Python's ISO parser handles fractional seconds and UTC offsets. Convert Z
    # explicitly for compatibility across Python versions.
    iso = text.replace("Z", "+00:00").replace("z", "+00:00")
    try:
        parsed = datetime.fromisoformat(iso)
        return parsed, False
    except ValueError:
        pass

    for fmt in (
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%d-%m-%Y %H:%M:%S",
        "%d-%m-%Y %H:%M",
    ):
        try:
            return datetime.strptime(text, fmt), False
        except ValueError:
            pass
    return None, False


def to_vn_datetime(value: Any, *, naive_is_vietnam: bool = True) -> datetime | date | None:
    """Return a Vietnam-local date/datetime without changing stored data.

    A timezone-aware value (including ISO strings ending in ``Z`` or an offset)
    is converted to Asia/Ho_Chi_Minh. Legacy app tables often store naive wall
    clock strings; those are treated as Vietnam local time by default so they are
    not shifted a second time. Callers may set ``naive_is_vietnam=False`` only
    for a known legacy UTC-naive source.
    """
    if value is None or value == "":
        return None

    if isinstance(value, datetime):
        parsed: datetime | date = value
    elif isinstance(value, date):
        return value
    else:
        # pandas Timestamp and similar objects generally expose to_pydatetime.
        converter = getattr(value, "to_pydatetime", None)
        if callable(converter):
            try:
                parsed = converter()
            except Exception:
                parsed = None  # type: ignore[assignment]
        else:
            parsed = None  # type: ignore[assignment]
        if parsed is None:
            parsed, _date_only = _parse_string(_text(value))
            if parsed is None:
                return None
            if isinstance(parsed, date) and not isinstance(parsed, datetime):
                return parsed

    if isinstance(parsed, date) and not isinstance(parsed, datetime):
        return parsed

    dt = parsed
    if dt.tzinfo is None:
        if naive_is_vietnam:
            dt = dt.replace(tzinfo=VN_TZ)
        else:
            dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(VN_TZ)


def format_vn_datetime(
    value: Any,
    *,
    seconds: bool = True,
    default: str = "",
    naive_is_vietnam: bool = True,
) -> str:
    parsed = to_vn_datetime(value, naive_is_vietnam=naive_is_vietnam)
    if parsed is None:
        return default if default != "" else _text(value)
    if isinstance(parsed, date) and not isinstance(parsed, datetime):
        return parsed.strftime("%d/%m/%Y")
    return parsed.strftime("%d/%m/%Y %H:%M:%S" if seconds else "%d/%m/%Y %H:%M")


def format_vn_date(value: Any, *, default: str = "", naive_is_vietnam: bool = True) -> str:
    parsed = to_vn_datetime(value, naive_is_vietnam=naive_is_vietnam)
    if parsed is None:
        return default if default != "" else _text(value)
    return parsed.strftime("%d/%m/%Y")


def _format_temporal_value(value: Any) -> Any:
    if value is None or value == "":
        return value
    parsed = to_vn_datetime(value)
    if parsed is None:
        return value
    if isinstance(parsed, date) and not isinstance(parsed, datetime):
        return parsed.strftime("%d/%m/%Y")
    return parsed.strftime("%d/%m/%Y %H:%M:%S")


def format_tabular_vn(data: Any) -> Any:
    """Return a display copy with temporal columns formatted in Vietnam time.

    Supported inputs are pandas DataFrames/Series and list/tuple records. The
    function is deliberately display-only and never mutates the caller's object.
    """
    if data is None:
        return data

    try:
        import pandas as pd
    except Exception:
        pd = None  # type: ignore[assignment]

    if pd is not None and isinstance(data, pd.DataFrame):
        out = data.copy()
        for column in out.columns:
            if _is_temporal_column(column):
                out[column] = out[column].map(_format_temporal_value)
        return out

    if pd is not None and isinstance(data, pd.Series):
        if _is_temporal_column(data.name):
            return data.map(_format_temporal_value)
        return data.copy()

    if isinstance(data, (list, tuple)):
        changed = []
        for item in data:
            if isinstance(item, dict):
                row = dict(item)
                for key, value in list(row.items()):
                    if _is_temporal_column(key):
                        row[key] = _format_temporal_value(value)
                changed.append(row)
            else:
                changed.append(item)
        return type(data)(changed) if isinstance(data, tuple) else changed

    if isinstance(data, dict):
        row = dict(data)
        for key, value in list(row.items()):
            if _is_temporal_column(key):
                row[key] = _format_temporal_value(value)
        return row

    return data


def install_work_task_vn_display() -> None:
    """Format Work Tasks comments/audit rows without altering stored timestamps."""
    try:
        import work_tasks_v1_v622 as work
    except Exception:
        return
    if getattr(work, "_qlda_vn_datetime_display_installed", False):
        return

    original_comments = work.list_work_task_comments
    original_history = work.list_work_task_history

    def comments_vn(*args, **kwargs):
        rows = original_comments(*args, **kwargs)
        out = []
        for raw in rows:
            row = dict(raw)
            row["created_at"] = format_vn_datetime(row.get("created_at"))
            out.append(row)
        return out

    def history_vn(*args, **kwargs):
        rows = original_history(*args, **kwargs)
        out = []
        for raw in rows:
            row = dict(raw)
            row["created_at"] = format_vn_datetime(row.get("created_at"))
            out.append(row)
        return out

    work.list_work_task_comments = comments_vn
    work.list_work_task_history = history_vn
    work._qlda_vn_datetime_display_installed = True
