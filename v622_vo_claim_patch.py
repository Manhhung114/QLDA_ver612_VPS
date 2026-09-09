from __future__ import annotations

import ast
import unicodedata

PATCH_MARKER = "V6.22 VO EXCEL V1"


def _norm(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return text.lower().replace("đ", "d")


def _is_tabs_call(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "tabs"


def _labels(call: ast.Call) -> list[str]:
    if not call.args or not isinstance(call.args[0], (ast.List, ast.Tuple)):
        return []
    out = []
    for elt in call.args[0].elts:
        if not isinstance(elt, ast.Constant) or not isinstance(elt.value, str):
            return []
        out.append(elt.value)
    return out


def _target(target: ast.AST, index: int):
    if isinstance(target, (ast.Tuple, ast.List)) and index < len(target.elts):
        elt = target.elts[index]
        if isinstance(elt, ast.Name):
            return ("name", elt.id, index)
    if isinstance(target, ast.Name):
        return ("subscript", target.id, index)
    return None


def _matches(ctx: ast.AST, target) -> bool:
    if target is None:
        return False
    kind, name, index = target
    if kind == "name":
        return isinstance(ctx, ast.Name) and ctx.id == name
    if kind == "subscript" and isinstance(ctx, ast.Subscript) and isinstance(ctx.value, ast.Name) and ctx.value.id == name:
        sl = ctx.slice
        if isinstance(sl, ast.Constant):
            return sl.value == index
        if hasattr(ast, "Index") and isinstance(sl, ast.Index):
            val = sl.value
            return isinstance(val, ast.Constant) and val.value == index
    return False


def _find_vo_with(fn: ast.FunctionDef) -> ast.With | None:
    targets = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Assign) or not _is_tabs_call(node.value):
            continue
        labels = _labels(node.value)
        if not labels:
            continue
        index = None
        for i, label in enumerate(labels):
            n = _norm(label)
            if "phat sinh" in n and ("vo" in n or "variation" in n):
                index = i
                break
        if index is None:
            continue
        for assigned in node.targets:
            resolved = _target(assigned, index)
            if resolved is not None:
                targets.append(resolved)
    if not targets:
        return None
    candidates = []
    for node in ast.walk(fn):
        if isinstance(node, ast.With) and node.items and any(_matches(node.items[0].context_expr, target) for target in targets):
            candidates.append(node)
    return sorted(candidates, key=lambda n: n.lineno)[0] if candidates else None


def patch_vo_claims(source: str) -> str:
    """Replace the legacy VO body with the independent signed increase/decrease UI."""
    if PATCH_MARKER in source:
        return source
    tree = ast.parse(source)
    fn = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "render_cost_management"), None)
    if fn is None:
        raise RuntimeError("V6.22 VO patch: render_cost_management not found")
    vo_with = _find_vo_with(fn)
    if vo_with is None or not vo_with.body:
        raise RuntimeError("V6.22 VO patch: Chi phí phát sinh (VO) tab not found")

    lines = source.splitlines(keepends=True)
    start = vo_with.body[0].lineno - 1
    end = vo_with.body[-1].end_lineno
    indent = " " * (vo_with.col_offset + 4)
    replacement = (
        f"{indent}# {PATCH_MARKER}\n"
        f"{indent}_v622_render_vo_ui(db, pid, can_update=bool(_can_update()))\n"
    )
    lines[start:end] = [replacement]

    insert_at = fn.lineno - 1
    helper = (
        f"# {PATCH_MARKER} HELPER\n"
        "from vo_independent_v622 import render_vo_ui as _v622_render_vo_ui\n\n"
    )
    lines.insert(insert_at, helper)
    patched = "".join(lines)
    if PATCH_MARKER not in patched or "_v622_render_vo_ui" not in patched:
        raise RuntimeError("V6.22 VO patch marker missing after injection")
    compile(patched, "streamlit_app_v622_vo.py", "exec")
    return patched
