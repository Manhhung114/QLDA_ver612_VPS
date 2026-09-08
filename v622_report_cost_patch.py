from __future__ import annotations

import ast


PATCH_MARKER = "V6.22 REPORT COST V1"


def patch_report_cost(source: str) -> str:
    """Append live cost reporting to render_reports without rewriting legacy reports."""
    if PATCH_MARKER in source:
        return source

    tree = ast.parse(source)
    fn = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "render_reports"), None)
    if fn is None or not fn.body:
        raise RuntimeError("V6.22 report-cost patch: render_reports not found")

    lines = source.splitlines(keepends=True)
    end = fn.body[-1].end_lineno
    indent = " " * 4
    call = (
        f"{indent}# {PATCH_MARKER}\n"
        f"{indent}_v622_render_cost_report(db, pid)\n"
    )
    lines.insert(end, call)

    insert_at = fn.lineno - 1
    helper = (
        f"# {PATCH_MARKER} HELPER\n"
        "from report_cost_v622 import render_cost_report as _v622_render_cost_report\n\n"
    )
    lines.insert(insert_at, helper)
    patched = "".join(lines)

    if PATCH_MARKER not in patched or "_v622_render_cost_report(db, pid)" not in patched:
        raise RuntimeError("V6.22 report-cost patch marker missing after injection")
    compile(patched, "streamlit_app_v622_report_cost.py", "exec")
    return patched
