from __future__ import annotations

from typing import Any


# Presentation-only helper. It does not change persistence, workflow rules or
# permissions. Existing edit forms continue to save through the current app
# services; this module only makes those forms easier to discover and open.
_EDIT_TARGETS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("hồ sơ", ("doc_select_",)),
    ("bản vẽ", ("drawing_select_",)),
    ("nhật ký", ("diary_select_",)),
    ("boq", ("cost_boq_sel_",)),
    ("vật tư", ("mat_sel_",)),
    ("kế hoạch mua sắm", ("proc_sel_",)),
    ("phiếu nhập xuất", ("inv_sel_",)),
)


def _has_value(value: Any) -> bool:
    if value is None or value is False:
        return False
    if isinstance(value, str) and not value.strip():
        return False
    return True


def _selection_active(st, label: str) -> bool:
    text = str(label or "").lower()
    prefixes: tuple[str, ...] = ()
    for marker, target_prefixes in _EDIT_TARGETS:
        if marker in text:
            prefixes = target_prefixes
            break
    if not prefixes:
        return False

    for key in list(st.session_state.keys()):
        key_text = str(key)
        if key_text.endswith("_pending"):
            continue
        if not any(key_text.startswith(prefix) for prefix in prefixes):
            continue
        try:
            if _has_value(st.session_state.get(key)):
                return True
        except Exception:
            continue
    return False


def _edit_button_label(label: Any) -> Any:
    if not isinstance(label, str):
        return label
    prefix = "📝 Mở / xử lý"
    if label.startswith(prefix):
        return "✏️ Chỉnh sửa / xử lý" + label[len(prefix):]
    return label


def install_edit_sheet_ux(st) -> None:
    """Improve edit discoverability for all table/sheet-style screens.

    Behaviour:
    - Existing ``Mở / xử lý`` buttons are presented as ``Chỉnh sửa / xử lý``.
    - When an existing row is selected, the matching ``Thêm / sửa`` expander
      opens automatically and its title changes to ``Chỉnh sửa thông tin``.
    - Database writes, approval workflow and permission checks remain untouched.
    """

    if getattr(st, "_qlda_edit_sheet_ux_installed", False):
        return

    original_expander = st.expander
    original_button = st.button

    def _expander(label, *args, **kwargs):
        display_label = label
        if isinstance(label, str) and label.startswith("✏️ Thêm / sửa"):
            if _selection_active(st, label):
                kwargs["expanded"] = True
                display_label = label.replace("✏️ Thêm / sửa", "✏️ Chỉnh sửa thông tin", 1)
        return original_expander(display_label, *args, **kwargs)

    def _button(label, *args, **kwargs):
        new_label = _edit_button_label(label)
        if new_label != label and "help" not in kwargs:
            kwargs["help"] = "Chọn đúng 1 dòng trong bảng để mở form chỉnh sửa thông tin."
        return original_button(new_label, *args, **kwargs)

    st._qlda_edit_sheet_original_expander = original_expander
    st._qlda_edit_sheet_original_button = original_button
    st.expander = _expander
    st.button = _button

    # Buttons inside st.columns are DeltaGenerator.button calls, not st.button.
    # Apply the same presentation-only label change there as well.
    try:
        from streamlit.delta_generator import DeltaGenerator

        if not getattr(DeltaGenerator, "_qlda_edit_sheet_ux_installed", False):
            original_dg_button = DeltaGenerator.button

            def _dg_button(self, label, *args, **kwargs):
                new_label = _edit_button_label(label)
                if new_label != label and "help" not in kwargs:
                    kwargs["help"] = "Chọn đúng 1 dòng trong bảng để mở form chỉnh sửa thông tin."
                return original_dg_button(self, new_label, *args, **kwargs)

            DeltaGenerator._qlda_edit_sheet_original_button = original_dg_button
            DeltaGenerator.button = _dg_button
            DeltaGenerator._qlda_edit_sheet_ux_installed = True
    except Exception:
        pass

    st._qlda_edit_sheet_ux_installed = True
