from __future__ import annotations

from pathlib import Path
from typing import Any

from qlda.shared.legacy import resolve


class FileService:
    """Stable file/storage service used by workers and future HTTP adapters."""

    IMPLEMENTATION = "local_vps_backend_v622"

    @classmethod
    def _call(cls, name: str, *args: Any, **kwargs: Any):
        return resolve(cls.IMPLEMENTATION, name)(*args, **kwargs)

    @classmethod
    def local_path(cls, file_id: str) -> tuple[dict[str, Any], Path]:
        row, path = cls._call("local_file_path", str(file_id or ""))
        return dict(row), Path(path)

    @classmethod
    def info(cls, token: str, file_id: str) -> dict[str, Any]:
        return cls._call("file_info", token, file_id)

    @classmethod
    def list_record_files(cls, token: str, **kwargs: Any) -> dict[str, Any]:
        return cls._call("list_record_files", token, **kwargs)

    @classmethod
    def record_file_counts(cls, token: str, **kwargs: Any) -> dict[str, Any]:
        return cls._call("record_file_counts", token, **kwargs)

    @classmethod
    def save_bytes(cls, token: str, **kwargs: Any) -> dict[str, Any]:
        return cls._call("save_bytes", token, **kwargs)

    @classmethod
    def make_upload_ticket(cls, token: str, **kwargs: Any) -> dict[str, Any]:
        return cls._call("make_upload_ticket", token, **kwargs)

    @classmethod
    def trash(cls, token: str, file_id: str) -> dict[str, Any]:
        return cls._call("trash_file", token, file_id)
