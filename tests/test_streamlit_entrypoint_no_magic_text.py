from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = ROOT / "src" / "qlda" / "presentation" / "streamlit" / "main.py"


def test_streamlit_entrypoint_has_no_top_level_string_expression() -> None:
    """Prevent Streamlit magic from rendering maintenance text in production UI."""
    source = ENTRYPOINT.read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in tree.body:
        assert not (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ), "Top-level string expressions can be rendered by Streamlit magic; use comments instead."


def test_streamlit_entrypoint_still_loads_compatibility_shell() -> None:
    source = ENTRYPOINT.read_text(encoding="utf-8")
    assert 'import_module("qlda.presentation.streamlit.app")' in source
