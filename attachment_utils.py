from __future__ import annotations

from pathlib import Path
from urllib.parse import quote, unquote

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices

MARKER = "#qlda_name="


def make_attachment_ref(path_or_url: str, name: str = "") -> str:
    value = str(path_or_url or "")
    if value.startswith("http") and name:
        return value.split(MARKER, 1)[0] + MARKER + quote(name)
    return value


def split_attachment_ref(ref: str) -> tuple[str, str]:
    ref = str(ref or "")
    if MARKER in ref:
        base, enc = ref.split(MARKER, 1)
        return base, unquote(enc)
    if ref.startswith("http"):
        return ref, "Google Drive file"
    return ref, Path(ref).name


def attachment_name(ref: str) -> str:
    return split_attachment_ref(ref)[1] or "Tệp đính kèm"


def open_attachment(ref: str) -> bool:
    target, _name = split_attachment_ref(ref)
    if target.startswith("http://") or target.startswith("https://"):
        return QDesktopServices.openUrl(QUrl(target))
    p = Path(target)
    if p.exists():
        return QDesktopServices.openUrl(QUrl.fromLocalFile(str(p)))
    return False
