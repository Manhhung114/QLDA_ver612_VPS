from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = ROOT / "src" / "qlda" / "presentation" / "streamlit" / "main.py"


def test_streamlit_entrypoint_executes_app_on_every_rerun() -> None:
    source = ENTRYPOINT.read_text(encoding="utf-8")
    assert "from runpy import run_module" in source
    assert 'APP_MODULE = "qlda.presentation.streamlit.app"' in source
    assert 'run_module(APP_MODULE, run_name="__main__")' in source
    assert "import_module(" not in source


def test_streamlit_entrypoint_has_no_renderable_top_level_string() -> None:
    tree = ast.parse(ENTRYPOINT.read_text(encoding="utf-8"))
    strings = [
        node
        for node in tree.body
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    ]
    assert strings == []
