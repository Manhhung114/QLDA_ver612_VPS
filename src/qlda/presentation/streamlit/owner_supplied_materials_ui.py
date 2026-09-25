from __future__ import annotations

"""Presentation installer for the owner-supplied material ERP.

Business/persistence semantics remain in the compatibility service while the
Streamlit integration point is owned by the presentation layer.
"""

import inspect

from qlda.runtime_core.owner_supplied_materials import (
    LEDGER_TABLE,
    PATCH_MARKER,
    PLAN_TABLE,
    WAREHOUSE_TABLE,
    _int,
    _text,
    render_owner_supplied_materials,
)


def install_owner_supplied_material_erp() -> None:
    import streamlit as st

    if getattr(st, "_qlda_owner_material_erp_installed", False):
        return
    original_subheader = st.subheader

    def subheader_with_erp(body, *args, **kwargs):
        result = original_subheader(body, *args, **kwargs)
        if _text(body) != "📦 Vật tư & thiết bị":
            return result
        frame = inspect.currentframe()
        caller = frame.f_back if frame else None
        try:
            if caller is None or caller.f_code.co_name != "render_material_management":
                return result
            glb, loc = caller.f_globals, caller.f_locals
            db = glb.get("db")
            pid = _int(loc.get("pid"))
            if db is None or pid <= 0:
                return result
            can_update_fn = glb.get("_can_update")
            is_admin_fn = glb.get("_is_admin")
            identity_fn = glb.get("_cloud_identity")
            gateway_fn = glb.get("_drive_gateway")
            token_fn = glb.get("_gateway_session_token")
            render_owner_supplied_materials(
                st,
                db,
                pid,
                identity=identity_fn() if callable(identity_fn) else None,
                can_update=bool(can_update_fn()) if callable(can_update_fn) else False,
                is_admin=bool(is_admin_fn()) if callable(is_admin_fn) else False,
                gateway=gateway_fn() if callable(gateway_fn) else None,
                session_token=_text(token_fn()) if callable(token_fn) else "",
            )
        except Exception as exc:
            st.warning(f"ERP vật tư CĐT cấp chưa khởi tạo được: {exc}")
        finally:
            del caller
            del frame
        return result

    st.subheader = subheader_with_erp
    st._qlda_owner_material_erp_installed = True
    st._qlda_owner_material_erp_marker = PATCH_MARKER
    try:
        import qlda.runtime_core.project_database as pg

        order = list(pg.TABLE_ORDER)
        for table in (WAREHOUSE_TABLE, PLAN_TABLE, LEDGER_TABLE):
            if table not in order:
                order.append(table)
        pg.TABLE_ORDER = tuple(order)
        pg._ID_TABLES = set(pg.TABLE_ORDER)
    except Exception:
        pass


__all__ = ["install_owner_supplied_material_erp"]
