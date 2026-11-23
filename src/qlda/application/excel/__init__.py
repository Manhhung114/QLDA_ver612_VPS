"""Shared, read-only Excel template recognition for QLDA import pipelines."""

from .template_recognition import analyze_workbook_path, recognize_sheet

__all__ = ["analyze_workbook_path", "recognize_sheet"]
