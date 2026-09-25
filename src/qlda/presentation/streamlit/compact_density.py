from __future__ import annotations


PATCH_MARKER = "COMPACT DENSITY UI V1"


def install_compact_density() -> None:
    """Tighten vertical density without changing the V7 color system."""
    import streamlit as st

    if getattr(st, "_qlda_compact_density_installed", False):
        return

    st.markdown(
        """
<style>
.block-container{
  padding-top:.42rem!important;
  padding-bottom:2rem!important;
}
[data-testid="stSidebar"] [data-testid="stVerticalBlock"]{gap:.42rem!important}
[data-testid="stVerticalBlock"]{gap:.72rem}
[data-baseweb="tab-list"]{padding:3px!important;border-radius:10px!important}
[data-baseweb="tab"]{
  min-height:2.28rem!important;
  padding-left:11px!important;
  padding-right:11px!important;
  border-radius:8px!important;
  font-size:.92rem!important;
}
[data-testid="stMetric"]{
  padding:8px 11px!important;
  border-left-width:3px!important;
  border-radius:12px!important;
  min-height:82px!important;
}
[data-testid="stMetricLabel"]{font-size:.82rem!important}
[data-testid="stMetricValue"]{font-size:1.72rem!important;line-height:1.08!important}
[data-testid="stExpander"]{border-radius:10px!important}
[data-testid="stForm"]{padding:11px 13px 6px!important;border-radius:12px!important}
.stButton>button,.stDownloadButton>button,.stLinkButton>a{
  min-height:2.25rem!important;
  border-radius:9px!important;
}
[data-testid="stTextArea"] textarea{min-height:76px!important}
.qlda-v7-hero{
  padding:12px 16px!important;
  margin:.05rem 0 .65rem 0!important;
  border-radius:15px!important;
}
.qlda-v7-title{font-size:1.28rem!important}
.qlda-v7-project{font-size:.94rem!important;margin-top:3px!important}
.qlda-v7-contractor{font-size:.81rem!important;margin-top:2px!important}
.qlda-v7-section-title{margin:.22rem 0 .38rem!important}
.qlda-v7-credit{
  font-size:8px!important;
  opacity:.42!important;
  background:transparent!important;
  border:0!important;
  box-shadow:none!important;
  padding:1px 4px!important;
  right:8px!important;
  bottom:4px!important;
}
.qlda-ai-page-head{
  display:flex;
  align-items:center;
  gap:10px;
  margin:.05rem 0 0;
}
.qlda-ai-page-title{
  color:var(--qlda-navy,#0f2747);
  font-size:1.52rem;
  font-weight:800;
  letter-spacing:-.015em;
  line-height:1.15;
}
.qlda-ai-tenant-badge{
  display:inline-flex;
  align-items:center;
  padding:3px 9px;
  border-radius:999px;
  background:var(--qlda-blue-soft,#eaf2ff);
  color:var(--qlda-blue-strong,#1746b5);
  border:1px solid #cbdcf8;
  font-size:.76rem;
  font-weight:750;
}
.qlda-ai-page-subtitle{
  color:var(--qlda-muted,#64748b);
  font-size:.84rem;
  margin:.12rem 0 .58rem;
}
@media(max-width:760px){
  [data-testid="stMetric"]{min-height:74px!important;padding:7px 9px!important}
  [data-testid="stMetricValue"]{font-size:1.45rem!important}
  .qlda-ai-page-title{font-size:1.28rem}
  .qlda-ai-page-subtitle{margin-bottom:.45rem}
}
</style>
        """,
        unsafe_allow_html=True,
    )
    st._qlda_compact_density_installed = True
    st._qlda_compact_density_marker = PATCH_MARKER


__all__ = ["PATCH_MARKER", "install_compact_density"]
