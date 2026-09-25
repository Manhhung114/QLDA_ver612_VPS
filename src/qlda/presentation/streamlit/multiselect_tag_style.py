from __future__ import annotations

"""Presentation-owned style for readable Streamlit multiselect chips."""

PATCH_MARKER = "V7 MULTISELECT TAG STYLE V2 PRESENTATION"

_TAG_CSS = r"""
<style>
div[data-baseweb="select"] div[data-baseweb="tag"] {
    background: #f1f5f9 !important;
    background-color: #f1f5f9 !important;
    border: 1px solid #cbd5e1 !important;
    box-shadow: none !important;
    color: #1f2937 !important;
}

div[data-baseweb="select"] div[data-baseweb="tag"] span,
div[data-baseweb="select"] div[data-baseweb="tag"] div {
    background: transparent !important;
    color: #1f2937 !important;
    font-weight: 550 !important;
}

div[data-baseweb="select"] div[data-baseweb="tag"] svg {
    color: #64748b !important;
    fill: #64748b !important;
}

div[data-baseweb="select"] div[data-baseweb="tag"]:hover {
    background: #e8eef5 !important;
    background-color: #e8eef5 !important;
    border-color: #b8c4d3 !important;
}
</style>
"""


def install_multiselect_tag_style_patch() -> None:
    import qlda.runtime_core.ui_v7_compact as ui

    if getattr(ui, "_qlda_multiselect_tag_style_v1", False):
        return

    original_install_theme = ui.install_theme_v7

    def install_theme_v7_with_light_tags(st) -> None:
        original_install_theme(st)
        st.markdown(_TAG_CSS, unsafe_allow_html=True)

    ui.install_theme_v7 = install_theme_v7_with_light_tags
    ui._qlda_multiselect_tag_style_v1 = True
    ui._qlda_multiselect_tag_style_marker = PATCH_MARKER


__all__ = ["install_multiselect_tag_style_patch"]
