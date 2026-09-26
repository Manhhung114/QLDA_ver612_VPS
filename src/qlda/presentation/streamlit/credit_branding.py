from __future__ import annotations

"""Presentation-only branding override for the persistent app credit."""


def install_credit_branding_v7() -> None:
    import streamlit as st

    if getattr(st, "_qlda_v7_credit_branding_installed", False):
        return

    st.markdown(
        """
<style>
:root{
  --qlda-nav-font-size:16px;
  --qlda-nav-line-height:1.45;
}

/* Navigation text such as “🏠 Tổng quan”. */
[data-testid="stSidebar"] [data-testid="stRadio"] label p,
[data-testid="stSidebar"] [role="radiogroup"] label p,
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p{
  font-size:var(--qlda-nav-font-size)!important;
  line-height:var(--qlda-nav-line-height)!important;
}

/* Persistent application credit: large, black and bold on desktop. */
.qlda-v7-credit{
  color:#000000!important;
  font-family:inherit!important;
  font-size:30px!important;
  line-height:1.25!important;
  font-weight:800!important;
  letter-spacing:0!important;
  background:rgba(255,255,255,.98)!important;
  border:1.5px solid #111111!important;
  border-radius:999px!important;
  padding:7px 14px!important;
  box-shadow:0 3px 12px rgba(0,0,0,.18)!important;
  opacity:1!important;
}

@media(max-width:760px){
  .qlda-v7-credit{
    right:8px!important;
    bottom:6px!important;
    font-size:16px!important;
    line-height:1.35!important;
    font-weight:800!important;
    padding:5px 10px!important;
  }
}
</style>
        """,
        unsafe_allow_html=True,
    )
    st._qlda_v7_credit_branding_installed = True


__all__ = ["install_credit_branding_v7"]
