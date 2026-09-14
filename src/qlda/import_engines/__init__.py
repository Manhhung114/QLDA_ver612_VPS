from __future__ import annotations

"""V7.5 packaged Excel import engines.

The worker/application path imports these modules from ``src/qlda`` instead of
loading root V6.24 engine modules through ``legacy_import``.  The copied engine
implementations intentionally retain proven V6.22 semantic helpers used by the
Streamlit application; those helpers are isolated below this package boundary.
"""

from .loader import ENGINE_MODULES, load_engine

__all__ = ["ENGINE_MODULES", "load_engine"]
