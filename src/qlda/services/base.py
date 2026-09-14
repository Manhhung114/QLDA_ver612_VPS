from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Mapping

ProgressFn = Callable[[int, str, str], None]
CancelFn = Callable[[], bool]
DbFactory = Callable[[], Any]


def require_project_id(value: Any, label: str = "project_id") -> int:
    try:
        project_id = int(value or 0)
    except Exception as exc:
        raise ValueError(f"{label} không hợp lệ.") from exc
    if project_id <= 0:
        raise ValueError(f"{label} không hợp lệ.")
    return project_id


def normalized_filename(path: str | Path, filename: str, fallback: str) -> str:
    value = Path(str(filename or "")).name.strip()
    if value:
        return value
    source = Path(path)
    return source.name or fallback


def declared_file_size(path: str | Path, value: Any = 0) -> int:
    try:
        size = int(value or 0)
    except Exception:
        size = 0
    if size > 0:
        return size
    try:
        return int(Path(path).stat().st_size)
    except Exception:
        return 0


def options_dict(options: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict(options or {})


def ensure_not_cancelled(cancelled: CancelFn | None, message: str) -> None:
    if cancelled and cancelled():
        raise InterruptedError(message)
