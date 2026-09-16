from __future__ import annotations

"""Global display policy for monetary columns in QLDA tables.

Only presentation is changed. Database values and calculation data stay numeric.
Money-like columns shown through ``st.dataframe``/``st.table`` are rendered with
comma thousands separators, e.g. 510000000000 -> 510,000,000,000.
"""

from functools import wraps
import math
import re
import unicodedata
from typing import Any

PATCH_MARKER = "V7.6 MONEY DISPLAY THOUSANDS COMMA V1"


def _norm(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("đ", "d")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# Deliberately conservative: format monetary/value columns, not generic IDs,
# quantities, dates, percentages or progress columns.
_MONEY_TERMS = (
    "gia tri",
    "thanh tien",
    "don gia",
    "chi phi",
    "ngan sach",
    "baseline",
    "budget",
    "amount",
    "cost",
    "committed",
    "thanh toan",
    "giai ngan",
    "con phai",
    "de nghi",
    "duoc duyet",
    "chung nhan",
    "nghiem thu",
    "tam ung",
    "thu hoi",
    "giu lai",
    "khau tru",
    "du phong",
    "doanh thu",
    "forecast",
    "expected",
    "actual cost",
    "planned amount",
    "approved amount",
    "paid amount",
    "outstanding",
    "certified",
    "proposed amount",
)
_EXCLUDE_TERMS = (
    "%",
    "phan tram",
    "ty le",
    "cpi",
    "spi",
    "tcpi",
    "so ngay",
    "ngay",
    "so luong",
    "quantity",
    "progress",
)


def _is_money_column(name: Any) -> bool:
    raw = str(name or "")
    n = _norm(raw)
    if not n:
        return False
    if any(term in raw.lower() for term in _EXCLUDE_TERMS if term == "%"):
        return False
    if any(term in n for term in _EXCLUDE_TERMS if term != "%"):
        return False
    return any(term in n for term in _MONEY_TERMS)


def _format_number(value: Any) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return value
        # Keep values already carrying units/currency or already formatted.
        low = text.lower()
        if any(unit in low for unit in (" vnd", " usd", " eur", " tỷ", " ty", " triệu", " trieu", " đ")):
            return value
        cleaned = text.replace(",", "")
        if not re.fullmatch(r"[-+]?\d+(?:\.\d+)?", cleaned):
            return value
        try:
            number = float(cleaned)
        except Exception:
            return value
    elif isinstance(value, (int, float)):
        number = float(value)
    else:
        try:
            number = float(value)
        except Exception:
            return value

    if not math.isfinite(number):
        return value
    if abs(number - round(number)) < 1e-9:
        return f"{number:,.0f}"
    # Preserve useful cents/decimal values while still inserting separators.
    return f"{number:,.2f}".rstrip("0").rstrip(".")


def format_money_columns(data: Any) -> Any:
    """Return a display copy with money-like columns formatted as strings."""
    try:
        import pandas as pd
    except Exception:
        return data

    # Do not rewrite pandas Styler objects; callers may have explicit formatting.
    if data.__class__.__name__ == "Styler":
        return data

    frame = None
    original_was_df = isinstance(data, pd.DataFrame)
    if original_was_df:
        frame = data.copy()
    elif isinstance(data, (list, tuple)) and data and all(isinstance(row, dict) for row in data):
        try:
            frame = pd.DataFrame(data)
        except Exception:
            frame = None
    elif isinstance(data, dict):
        try:
            frame = pd.DataFrame(data)
        except Exception:
            frame = None

    if frame is None or frame.empty:
        return data

    changed = False
    for col in frame.columns:
        if not _is_money_column(col):
            continue
        try:
            frame[col] = frame[col].map(_format_number)
            changed = True
        except Exception:
            continue
    return frame if changed else data


def install_money_display_format() -> None:
    import streamlit as st

    if getattr(st, "_qlda_money_display_format_installed", False):
        return

    original_dataframe = st.dataframe
    original_table = st.table

    @wraps(original_dataframe)
    def dataframe_with_money_format(data=None, *args, **kwargs):
        return original_dataframe(format_money_columns(data), *args, **kwargs)

    @wraps(original_table)
    def table_with_money_format(data=None, *args, **kwargs):
        return original_table(format_money_columns(data), *args, **kwargs)

    st.dataframe = dataframe_with_money_format
    st.table = table_with_money_format
    st._qlda_money_display_format_installed = True
    st._qlda_money_display_format_marker = PATCH_MARKER


__all__ = ["format_money_columns", "install_money_display_format"]
