from __future__ import annotations

"""Small production Streamlit entrypoint.

The materialized V7.6 screen shell remains in ``app.py`` as a frozen compatibility
surface while screens are extracted incrementally into presentation modules.
Production starts here so deployment, tests and architecture guards no longer
need to treat the 300+ KB legacy shell as the application entrypoint.

New pages/features must be implemented outside ``app.py`` and wired through the
explicit composition root. Importing the compatibility shell executes the normal
Streamlit script exactly once for the current rerun.
"""

from importlib import import_module


def main() -> None:
    import_module("qlda.presentation.streamlit.app")


main()
