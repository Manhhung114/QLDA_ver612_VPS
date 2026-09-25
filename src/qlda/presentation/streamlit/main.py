from __future__ import annotations

# Small production Streamlit entrypoint.
#
# The materialized V7.6 screen shell remains in ``app.py`` as a frozen
# compatibility surface while screens are extracted incrementally into
# presentation modules. Production starts here so deployment, tests and
# architecture guards no longer need to treat the 300+ KB legacy shell as the
# application entrypoint.
#
# Streamlit re-executes this entrypoint for every widget rerun while Python keeps
# imported modules cached in ``sys.modules``. A plain ``import_module(app)``
# therefore renders only on the first execution and later reruns can become a
# blank page. ``run_module`` executes the known, source-controlled compatibility
# shell once on every Streamlit rerun without generating or evaluating source.
#
# Keep explanatory text as comments instead of a module docstring: Streamlit
# "magic" can render a top-level string expression into the application UI.

from runpy import run_module


APP_MODULE = "qlda.presentation.streamlit.app"


def main() -> None:
    run_module(APP_MODULE, run_name="__main__")


main()
