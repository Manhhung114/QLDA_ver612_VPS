from __future__ import annotations

"""Native presentation entry for the production overview.

The detailed implementation is kept in ``production_progress_ui``. This module is
only the stable owner boundary used by the composition root while the legacy
runtime compatibility module is retired.
"""

from qlda.presentation.streamlit.production_progress_ui import render_contractor_data_hub

__all__ = ["render_contractor_data_hub"]
