from __future__ import annotations

import re
import threading
from typing import Any


PATCH_MARKER = "V6.22 BOQ CLAIM TERMS V1"
_LOCK = threading.RLock()


def _rowdict(row: Any) -> dict:
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    try:
        return {str(k): row[k] for k in row.keys()}
    except Exception:
        try:
            return dict(row)
        except Exception:
            return {}


def _boq_text(text: Any) -> str:
    value = str(text or "")
    replacements = (
        ("Chi phí thành phần = Số lượng × Đơn giá thành phần", "Chi phí thành phần = Khối lượng × Đơn giá thành phần"),
        ("chi phí vật tư = số lượng × đơn giá vật tư", "chi phí vật tư = khối lượng × đơn giá vật tư"),
        ("chi phí nhân công = số lượng × đơn giá nhân công", "chi phí nhân công = khối lượng × đơn giá nhân công"),
        ("SL=", "Khối_lượng="),
    )
    for old, new in replacements:
        value = value.replace(old, new)
    return value


def _install_boq_terms() -> None:
    import qlda.runtime_core.boq_multisheet as boq
    if getattr(boq, "_qlda_boq_claim_terms_installed", False):
        return
    original_parse = boq.parse_boq_workbook

    def parse_with_quantity_terms(data: bytes, filename: str = "BOQ.xlsx"):
        result = original_parse(data, filename)
        result["warnings"] = [_boq_text(x) for x in list(result.get("warnings") or [])]
        return result

    boq.parse_boq_workbook = parse_with_quantity_terms
    boq._qlda_boq_claim_terms_installed = True
    boq._qlda_boq_claim_terms_marker = PATCH_MARKER



def _install_claim_header_aliases() -> None:
    """Accept Excel's two-row header: Đơn giá -> Vật tư / Nhân công."""
    import qlda.runtime_core.ipc_adaptive_parser as adaptive
    if getattr(adaptive, "_qlda_boq_claim_terms_installed", False):
        return

    original_find = adaptive._find_header_col

    def find_header_col_with_terms(headers, include, exclude=()):
        found = original_find(headers, include, exclude)
        if found is not None:
            return found
        wanted = tuple(include or ())
        if wanted == ("don gia lap dat",):
            return original_find(headers, ("don gia nhan cong",), exclude)
        if wanted == ("hang muc cong viec",):
            return original_find(headers, ("noi dung cong viec",), exclude)
        return None

    adaptive._find_header_col = find_header_col_with_terms
    adaptive._qlda_boq_claim_terms_installed = True
    adaptive._qlda_boq_claim_terms_marker = PATCH_MARKER


def _claim_detail_intent(norm_question: str) -> bool:
    delay_words = ("tre han", "tre thanh toan", "cham thanh toan", "so ngay tre", "ngay tre", "qua han", "toi han")
    if any(word in norm_question for word in delay_words):
        return False
    return any(word in norm_question for word in (
        "claim", "ipc", "nghiem thu", "khoi luong", "vat tu", "nhan cong", "don gia", "lap dat", "chi phi",
    ))




def _render_claim_component_table(db, project_id: int) -> None:
    """Supplement the Claim UI with the same BOQ-style quantity/unit-price view."""
    try:
        import pandas as pd
        import streamlit as st
        import qlda.runtime_core.ipc_claim as ipc
    except Exception:
        return

    try:
        with db.connect() as connection:
            claims = [_rowdict(row) for row in connection.execute(
                "SELECT claim_id,claim_code,claim_no FROM payment_claims WHERE project_id=? ORDER BY claim_no",
                (int(project_id),),
            ).fetchall()]
    except Exception:
        return
    if not claims:
        return

    st.markdown("### 📐 Khối lượng & Đơn giá Vật tư / Nhân công theo Claim")
    st.caption(
        "Chuẩn dữ liệu: Khối lượng; Đơn giá gồm Vật tư + Nhân công. "
        "Chi phí vật tư = Khối lượng vật tư × Đơn giá vật tư; "
        "Chi phí nhân công = Khối lượng lắp đặt × Đơn giá nhân công."
    )
    labels = [str(row.get("claim_code") or f"IPC-{row.get('claim_no','')}") for row in claims]
    selected_label = st.selectbox(
        "Claim xem chi tiết khối lượng / đơn giá",
        labels,
        key=f"ipc_component_view_{int(project_id)}",
    )
    selected = claims[labels.index(selected_label)]
    try:
        with db.connect() as connection:
            rows = [_rowdict(row) for row in connection.execute(
                """SELECT row_no,boq_item,unit,contract_qty,material_unit_price,labor_unit_price,
                          material_current_qty,material_cumulative_qty,
                          installation_current_pct,installation_cumulative_pct,
                          material_current_value,installation_current_value,current_value,cumulative_value
                   FROM payment_claim_items WHERE claim_id=? ORDER BY row_no""",
                (str(selected.get("claim_id") or ""),),
            ).fetchall()]
    except Exception:
        rows = []
    if not rows:
        st.info("Claim này chưa có dữ liệu khối lượng/đơn giá chuẩn hóa.")
        return

    table = pd.DataFrame(rows).rename(columns={
        "row_no": "Dòng",
        "boq_item": "Tên công tác / Diễn giải",
        "unit": "ĐVT",
        "contract_qty": "Khối lượng HĐ",
        "material_unit_price": "Đơn giá vật tư (VND)",
        "labor_unit_price": "Đơn giá nhân công (VND)",
        "material_current_qty": "Khối lượng vật tư kỳ này",
        "material_cumulative_qty": "Khối lượng vật tư lũy kế",
        "installation_current_pct": "Khối lượng lắp đặt kỳ này",
        "installation_cumulative_pct": "Khối lượng lắp đặt lũy kế",
        "material_current_value": "Chi phí vật tư kỳ này (VND)",
        "installation_current_value": "Chi phí nhân công kỳ này (VND)",
        "current_value": "Giá trị kỳ này (VND)",
        "cumulative_value": "Giá trị lũy kế (VND)",
    })
    numeric = {
        "Dòng", "Khối lượng HĐ", "Đơn giá vật tư (VND)", "Đơn giá nhân công (VND)",
        "Khối lượng vật tư kỳ này", "Khối lượng vật tư lũy kế",
        "Khối lượng lắp đặt kỳ này", "Khối lượng lắp đặt lũy kế",
        "Chi phí vật tư kỳ này (VND)", "Chi phí nhân công kỳ này (VND)",
        "Giá trị kỳ này (VND)", "Giá trị lũy kế (VND)",
    }
    for column in table.columns:
        if column in numeric:
            table[column] = table[column].map(ipc.format_table_number)
    st.dataframe(table, hide_index=True, width="stretch", height=560)


def _install_claim_ui_hook() -> None:
    import qlda.runtime_core.ipc_claim_patch as claim_patch
    if getattr(claim_patch, "_qlda_boq_claim_terms_installed", False):
        return

    original_install = claim_patch.install_ipc_claim_due_date

    def install_due_date_then_terms() -> None:
        original_install()
        import qlda.runtime_core.ipc_claim as ipc
        if getattr(ipc, "_qlda_boq_claim_terms_ui_installed", False):
            return
        original_render = ipc.render_ipc_claim_ui

        def render_with_quantity_terms(db, project_id: int, can_update: bool = False):
            result = original_render(db, project_id, can_update=can_update)
            _render_claim_component_table(db, int(project_id))
            return result

        ipc.render_ipc_claim_ui = render_with_quantity_terms
        ipc._qlda_boq_claim_terms_ui_installed = True
        ipc._qlda_boq_claim_terms_ui_marker = PATCH_MARKER

    claim_patch.install_ipc_claim_due_date = install_due_date_then_terms
    claim_patch._qlda_boq_claim_terms_installed = True
    claim_patch._qlda_boq_claim_terms_marker = PATCH_MARKER


def install_boq_claim_terms() -> None:
    """Standardize BOQ/Claim terminology without renaming legacy DB columns."""
    import qlda.runtime_core.project_store as cloud_db
    if getattr(cloud_db, "_qlda_boq_claim_terms_global_installed", False):
        return
    with _LOCK:
        if getattr(cloud_db, "_qlda_boq_claim_terms_global_installed", False):
            return
        _install_boq_terms()
        _install_claim_header_aliases()
        _install_claim_ui_hook()
        cloud_db._qlda_boq_claim_terms_global_installed = True
        cloud_db._qlda_boq_claim_terms_marker = PATCH_MARKER
