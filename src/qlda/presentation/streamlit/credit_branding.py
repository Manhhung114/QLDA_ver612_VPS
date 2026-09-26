from __future__ import annotations

"""Presentation-only branding override for the persistent app credit."""


def install_credit_branding_v7() -> None:
    import streamlit as st

    if getattr(st, "_qlda_v7_credit_branding_installed", False):
        return

    # The base V7 theme renders the credit element. This late CSS override keeps
    # the existing placement but makes the byline clearly visible on desktop and
    # mobile without competing with the working area of the app.
    st.markdown(
        """
<style>
.qlda-v7-credit{
  color:#000000!important;
  font-size:14px!important;
  font-weight:800!important;
  letter-spacing:.12px!important;
  background:rgba(255,255,255,.98)!important;
  border:1.5px solid #111111!important;
  border-radius:999px!important;
  padding:5px 10px!important;
  box-shadow:0 3px 12px rgba(0,0,0,.18)!important;
  opacity:1!important;
}
@media(max-width:760px){
  .qlda-v7-credit{
    right:8px!important;
    bottom:6px!important;
    font-size:12.5px!important;
    font-weight:800!important;
    padding:4px 9px!important;
  }
}
</style>
        """,
        unsafe_allow_html=True,
    )
    st._qlda_v7_credit_branding_installed = True


__all__ = ["install_credit_branding_v7"]
