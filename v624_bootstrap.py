from __future__ import annotations

PATCH_MARKER = "V6.24 EXCEL BACKGROUND BOOTSTRAP V1"


def install_v624_excel_background() -> None:
    """Compose V6.24 background Excel UI after the proven V6.22 BOQ patch."""
    import v622_boq_multisheet_patch as boq_patch
    from v624_excel_background_patch import patch_excel_background_v624

    current = boq_patch.patch_boq_multisheet
    if getattr(current, "_qlda_v624_excel_background", False):
        return

    def wrapped(source: str) -> str:
        return patch_excel_background_v624(current(source))

    wrapped._qlda_v624_excel_background = True
    wrapped._qlda_v624_base = current
    boq_patch.patch_boq_multisheet = wrapped
