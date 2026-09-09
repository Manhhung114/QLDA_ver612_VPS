from __future__ import annotations

import ast
import inspect
import threading
import unicodedata
from datetime import datetime


PATCH_MARKER = "V6.22 IPC CLAIM PAYMENT V1"
DUE_DATE_PATCH_MARKER = "V6.22 IPC CLAIM DUE DATE V2"
_DUE_DATE_LOCK = threading.RLock()


def _norm(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return text.lower().replace("đ", "d")


def _claim_payment_delay_days(payment_due_date, disbursement_date):
    """Return late-payment days for one Claim; None until both dates are available."""
    def parse(value):
        text = str(value or "").strip()
        if not text:
            return None
        for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%Y/%m/%d", "%d-%m-%Y"):
            try:
                return datetime.strptime(text, fmt).date()
            except Exception:
                pass
        return None

    due = parse(payment_due_date)
    paid = parse(disbursement_date)
    if due is None or paid is None:
        return None
    return max(0, (paid - due).days)


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


def _replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"{DUE_DATE_PATCH_MARKER}: không tìm thấy đúng 1 vị trí {label} (found={count})")
    return source.replace(old, new, 1)


def _redefine(ipc, function_name: str, source: str) -> None:
    compile(source, f"ipc_claim_v622_{function_name}_due_date.py", "exec")
    exec(source, ipc.__dict__, ipc.__dict__)


def _column_names(connection, table: str) -> set[str]:
    cursor = connection.execute(f"SELECT * FROM {table} LIMIT 0")
    description = getattr(cursor, "description", None) or []
    names: set[str] = set()
    for item in description:
        name = getattr(item, "name", None)
        if name is None:
            try:
                name = item[0]
            except Exception:
                name = None
        if name:
            names.add(str(name))
    return names


def install_ipc_claim_due_date() -> None:
    """Add due date and calculated late-payment days to every IPC Claim."""
    import ipc_claim_v622 as ipc

    if getattr(ipc, "_qlda_ipc_claim_due_date_installed", False):
        return

    with _DUE_DATE_LOCK:
        if getattr(ipc, "_qlda_ipc_claim_due_date_installed", False):
            return

        ipc._claim_payment_delay_days = _claim_payment_delay_days
        original_ensure_tables = ipc._ensure_tables

        def ensure_tables_with_due_date(connection) -> None:
            original_ensure_tables(connection)
            columns = _column_names(connection, ipc.CLAIMS_TABLE)
            if "payment_due_date" not in columns:
                connection.execute(
                    f"ALTER TABLE {ipc.CLAIMS_TABLE} ADD COLUMN payment_due_date TEXT DEFAULT ''"
                )

        ensure_tables_with_due_date.__name__ = "_ensure_tables"
        ensure_tables_with_due_date.__module__ = ipc.__name__
        ipc._ensure_tables = ensure_tables_with_due_date

        save_source = inspect.getsource(ipc.save_ipc_claim)
        if '"payment_due_date"' not in save_source:
            save_source = _replace_once(
                save_source,
                '            "payment_status": str(old.get("payment_status") or "Nháp"),\n'
                '            "disbursement_date": str(old.get("disbursement_date") or ""),\n',
                '            "payment_status": str(old.get("payment_status") or "Nháp"),\n'
                '            "payment_due_date": str(old.get("payment_due_date") or ""),\n'
                '            "disbursement_date": str(old.get("disbursement_date") or ""),\n',
                "save_ipc_claim.payment_due_date",
            )
            _redefine(ipc, "save_ipc_claim", save_source)

        update_source = inspect.getsource(ipc.update_ipc_claim_finance)
        if "payment_due_date: str" not in update_source:
            update_source = _replace_once(
                update_source,
                '    payment_status: str,\n    disbursement_date: str = "",\n',
                '    payment_status: str,\n    payment_due_date: str = "",\n    disbursement_date: str = "",\n',
                "update_ipc_claim_finance.signature",
            )
            update_source = _replace_once(
                update_source,
                '                   SET approved_amount=?,disbursed_amount=?,payment_status=?,disbursement_date=?,note=?,updated_at=?\n',
                '                   SET approved_amount=?,disbursed_amount=?,payment_status=?,payment_due_date=?,disbursement_date=?,note=?,updated_at=?\n',
                "update_ipc_claim_finance.sql",
            )
            update_source = _replace_once(
                update_source,
                '                float(approved_amount or 0), float(disbursed_amount or 0), str(payment_status or ""),\n'
                '                str(disbursement_date or ""), str(note or ""), now, str(claim_id),\n',
                '                float(approved_amount or 0), float(disbursed_amount or 0), str(payment_status or ""),\n'
                '                str(payment_due_date or ""), str(disbursement_date or ""), str(note or ""), now, str(claim_id),\n',
                "update_ipc_claim_finance.params",
            )
            _redefine(ipc, "update_ipc_claim_finance", update_source)

        render_source = inspect.getsource(ipc.render_ipc_claim_ui)
        if "Ngày tới hạn thanh toán" not in render_source:
            render_source = _replace_once(
                render_source,
                '                        "Giải ngân (VND)": float(c.get("disbursed_amount") or 0),\n'
                '                        "Revision": int(c.get("latest_revision") or 0),\n',
                '                        "Giải ngân (VND)": float(c.get("disbursed_amount") or 0),\n'
                '                        "Tới hạn thanh toán": c.get("payment_due_date") or "",\n'
                '                        "Ngày giải ngân": c.get("disbursement_date") or "",\n'
                '                        "Trễ thanh toán (ngày)": _claim_payment_delay_days(c.get("payment_due_date"), c.get("disbursement_date")),\n'
                '                        "Revision": int(c.get("latest_revision") or 0),\n',
                "render.summary",
            )
            render_source = _replace_once(
                render_source,
                '                    f"**Kỳ:** {claim.get(\'from_date\',\'\')} → {claim.get(\'to_date\',\'\')}  \\n"\n'
                '                    f"**File:** {claim.get(\'filename\',\'\')} · Revision {int(claim.get(\'latest_revision\') or 0)}  \\n"\n',
                '                    f"**Kỳ:** {claim.get(\'from_date\',\'\')} → {claim.get(\'to_date\',\'\')}  \\n"\n'
                '                    f"**Tới hạn thanh toán:** {claim.get(\'payment_due_date\',\'\')}  \\n"\n'
                '                    f"**Ngày giải ngân:** {claim.get(\'disbursement_date\',\'\')}  \\n"\n'
                '                    f"**Trễ hạn thanh toán:** {_claim_payment_delay_days(claim.get(\'payment_due_date\'), claim.get(\'disbursement_date\')) if _claim_payment_delay_days(claim.get(\'payment_due_date\'), claim.get(\'disbursement_date\')) is not None else \'Chưa xác định\'}{\' ngày\' if _claim_payment_delay_days(claim.get(\'payment_due_date\'), claim.get(\'disbursement_date\')) is not None else \'\'}  \\n"\n'
                '                    f"**File:** {claim.get(\'filename\',\'\')} · Revision {int(claim.get(\'latest_revision\') or 0)}  \\n"\n',
                "render.overview",
            )
            render_source = _replace_once(
                render_source,
                '                status_index = statuses.index(current_status) if current_status in statuses else 0\n'
                '                with st.form(f"ipc_finance_{claim_id}"):\n',
                '                status_index = statuses.index(current_status) if current_status in statuses else 0\n'
                '                _delay_days = _claim_payment_delay_days(claim.get("payment_due_date"), claim.get("disbursement_date"))\n'
                '                if _delay_days is None:\n'
                '                    st.metric("Trễ hạn thanh toán", "Chưa xác định")\n'
                '                else:\n'
                '                    st.metric("Trễ hạn thanh toán", f"{_delay_days} ngày")\n'
                '                with st.form(f"ipc_finance_{claim_id}"):\n',
                "render.delay_metric",
            )
            render_source = _replace_once(
                render_source,
                '                    status = st.selectbox("Trạng thái Claim", statuses, index=status_index)\n'
                '                    disbursement_date = st.date_input(\n',
                '                    status = st.selectbox("Trạng thái Claim", statuses, index=status_index)\n'
                '                    payment_due_date = st.date_input(\n'
                '                        "Ngày tới hạn thanh toán", value=_parse_ui_date(claim.get("payment_due_date")),\n'
                '                    )\n'
                '                    disbursement_date = st.date_input(\n',
                "render.form_due_date",
            )
            render_source = _replace_once(
                render_source,
                '                            payment_status=status,\n'
                '                            disbursement_date=disbursement_date.strftime("%Y-%m-%d"),\n',
                '                            payment_status=status,\n'
                '                            payment_due_date=payment_due_date.strftime("%Y-%m-%d"),\n'
                '                            disbursement_date=disbursement_date.strftime("%Y-%m-%d"),\n',
                "render.save_due_date",
            )
            render_source = _replace_once(
                render_source,
                '                    submitted = st.form_submit_button("💾 Cập nhật duyệt / giải ngân", disabled=not bool(can_update), use_container_width=True)\n',
                '                    submitted = st.form_submit_button("💾 Cập nhật duyệt / tới hạn / giải ngân", disabled=not bool(can_update), use_container_width=True)\n',
                "render.submit_label",
            )
            _redefine(ipc, "render_ipc_claim_ui", render_source)

        ipc._qlda_ipc_claim_due_date_installed = True
        ipc._qlda_ipc_claim_due_date_marker = DUE_DATE_PATCH_MARKER


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
        f"{indent}_v622_render_ipc_claim_period_ui(db, pid, can_update=bool(_can_update()))\n"
        f"{indent}_v622_render_ipc_claim_delete_ui(db, pid, can_update=bool(_can_update()))\n"
    )
    lines[start:end] = [replacement]

    insert_at = fn.lineno - 1
    helper = (
        f"# {PATCH_MARKER} HELPER\n"
        "from v622_ipc_claim_patch import install_ipc_claim_due_date as _v622_install_ipc_claim_due_date\n"
        "_v622_install_ipc_claim_due_date()\n"
        "from ipc_claim_v622 import render_ipc_claim_ui as _v622_render_ipc_claim_ui\n"
        "from ipc_claim_period_v622 import render_ipc_claim_period_ui as _v622_render_ipc_claim_period_ui\n"
        "from ipc_claim_delete_v622 import render_ipc_claim_delete_ui as _v622_render_ipc_claim_delete_ui\n\n"
    )
    lines.insert(insert_at, helper)
    patched = "".join(lines)

    if (
        PATCH_MARKER not in patched
        or "_v622_render_ipc_claim_ui" not in patched
        or "_v622_render_ipc_claim_period_ui" not in patched
        or "_v622_render_ipc_claim_delete_ui" not in patched
        or "_v622_install_ipc_claim_due_date()" not in patched
    ):
        raise RuntimeError("V6.22 IPC patch marker missing after injection")
    compile(patched, "streamlit_app_v622_ipc_claim.py", "exec")
    return patched
