from __future__ import annotations

import ast
import unicodedata


PATCH_MARKER = "V6.22 IPC CLAIM PAYMENT V1"


def _norm(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return text.lower().replace("đ", "d")


def _is_tabs_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    fn = node.func
    return isinstance(fn, ast.Attribute) and fn.attr == "tabs"


def _labels_from_tabs_call(call: ast.Call) -> list[str]:
    if not call.args:
        return []
    arg = call.args[0]
    if not isinstance(arg, (ast.List, ast.Tuple)):
        return []
    out = []
    for elt in arg.elts:
        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
            out.append(elt.value)
        else:
            return []
    return out


def _target_for_index(target: ast.AST, index: int):
    if isinstance(target, (ast.Tuple, ast.List)) and index < len(target.elts):
        elt = target.elts[index]
        if isinstance(elt, ast.Name):
            return ("name", elt.id, index)
    if isinstance(target, ast.Name):
        return ("subscript", target.id, index)
    return None


def _with_matches(ctx: ast.AST, target) -> bool:
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


def _find_payment_with(fn: ast.FunctionDef) -> ast.With | None:
    tab_targets = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.Assign) or not _is_tabs_call(node.value):
            continue
        labels = _labels_from_tabs_call(node.value)
        if not labels:
            continue
        payment_index = None
        for idx, label in enumerate(labels):
            n = _norm(label)
            if "thanh toan" in n or "giai ngan" in n or "payment" in n:
                payment_index = idx
                break
        if payment_index is None:
            continue
        for target in node.targets:
            resolved = _target_for_index(target, payment_index)
            if resolved is not None:
                tab_targets.append(resolved)

    if not tab_targets:
        return None

    candidates: list[ast.With] = []
    for node in ast.walk(fn):
        if not isinstance(node, ast.With) or not node.items:
            continue
        ctx = node.items[0].context_expr
        if any(_with_matches(ctx, target) for target in tab_targets):
            candidates.append(node)
    if not candidates:
        return None
    return sorted(candidates, key=lambda n: n.lineno)[0]


def patch_ipc_claims(source: str) -> str:
    """Replace legacy manual payment body with persistent IPC Claim UI."""
    if PATCH_MARKER in source:
        return source

    tree = ast.parse(source)
    fn = next((n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "render_cost_management"), None)
    if fn is None:
        raise RuntimeError("V6.22 IPC patch: render_cost_management not found")

    payment_with = _find_payment_with(fn)
    if payment_with is None or not payment_with.body:
        raise RuntimeError("V6.22 IPC patch: payment/disbursement tab not found")

    lines = source.splitlines(keepends=True)
    start = payment_with.body[0].lineno - 1
    end = payment_with.body[-1].end_lineno
    indent = " " * (payment_with.col_offset + 4)
    replacement = (
        f"{indent}# {PATCH_MARKER}\n"
        f"{indent}_v622_render_ipc_claim_ui(db, pid, can_update=bool(_can_update()))\n"
    )
    lines[start:end] = [replacement]

    # Inject helper import immediately before the cost function. The payment body
    # replacement above does not change any lines before the function definition.
    insert_at = fn.lineno - 1
    helper = (
        f"# {PATCH_MARKER} HELPER\n"
        "from ipc_claim_v622 import render_ipc_claim_ui as _v622_render_ipc_claim_ui\n\n"
    )
    lines.insert(insert_at, helper)
    patched = "".join(lines)

    if PATCH_MARKER not in patched or "_v622_render_ipc_claim_ui" not in patched:
        raise RuntimeError("V6.22 IPC patch marker missing after injection")
    compile(patched, "streamlit_app_v622_ipc_claim.py", "exec")
    return patched
