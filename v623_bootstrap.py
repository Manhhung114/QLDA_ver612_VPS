from __future__ import annotations


PATCH_MARKER = "V6.23 UI UX BOOTSTRAP V1"
_INSTALLED = False


def install_v623_uiux() -> None:
    """Compose V6.23 UX on top of the existing validated V7 source patch.

    streamlit_app imports performance_v1_v622 before importing the V7 patch
    function. Installing here preserves the existing entrypoint and business
    patch order while ensuring the later import receives this composed function.
    """
    global _INSTALLED
    if _INSTALLED:
        return

    import v622_ui_v7_compact_patch as v7_patch
    from v623_uiux_patch import patch_uiux_v623

    current = v7_patch.patch_ui_v7_compact
    if getattr(current, "_qlda_v623_uiux", False):
        _INSTALLED = True
        return

    def _combined(source: str) -> str:
        return patch_uiux_v623(current(source))

    _combined._qlda_v623_uiux = True
    _combined.__name__ = "patch_ui_v7_compact_v623"
    v7_patch.patch_ui_v7_compact = _combined
    _INSTALLED = True
