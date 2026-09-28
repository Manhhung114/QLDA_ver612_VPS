import pandas as pd

from qlda.presentation.streamlit.edit_sheet_ux import (
    _edit_button_label,
    _grid_edit_target,
    _selected_grid_ids,
    _selection_active,
)


class _FakeStreamlit:
    def __init__(self, state):
        self.session_state = state


def test_open_process_button_is_presented_as_edit_action():
    assert _edit_button_label("📝 Mở / xử lý hồ sơ") == "✏️ Chỉnh sửa / xử lý hồ sơ"
    assert _edit_button_label("📝 Mở / xử lý Shopdrawing") == "✏️ Chỉnh sửa / xử lý Shopdrawing"
    assert _edit_button_label("Khác") == "Khác"


def test_drawing_edit_form_opens_when_existing_row_is_selected():
    st = _FakeStreamlit({"drawing_select_1_SHOPDRAWING": 6})
    assert _selection_active(st, "✏️ Thêm / sửa bản vẽ") is True


def test_document_edit_form_opens_when_existing_row_is_selected():
    st = _FakeStreamlit({"doc_select_1_RFI": 12})
    assert _selection_active(st, "✏️ Thêm / sửa hồ sơ") is True


def test_new_record_does_not_force_edit_form_open():
    st = _FakeStreamlit({"drawing_select_1_SHOPDRAWING": None})
    assert _selection_active(st, "✏️ Thêm / sửa bản vẽ") is False


def test_material_procurement_inventory_and_boq_use_same_behavior():
    samples = [
        ({"cost_boq_sel_1": 3}, "✏️ Thêm / sửa BOQ"),
        ({"mat_sel_1": 4}, "✏️ Thêm / sửa vật tư"),
        ({"proc_sel_1": 5}, "✏️ Thêm / sửa kế hoạch mua sắm"),
        ({"inv_sel_1": 6}, "✏️ Thêm / sửa phiếu nhập xuất"),
        ({"diary_select_1": 7}, "✏️ Thêm / sửa nhật ký"),
    ]
    for state, label in samples:
        assert _selection_active(_FakeStreamlit(state), label) is True


def test_grid_keys_route_update_to_existing_edit_forms():
    assert _grid_edit_target("drawing_select_grid_3_ISSUED_DESIGN_1_6_123") == (
        "drawing_select_3_ISSUED_DESIGN_pending",
        "bản vẽ",
    )
    assert _grid_edit_target("doc_select_grid_4_RFI_1_8_456") == (
        "doc_select_4_RFI_pending",
        "hồ sơ",
    )
    assert _grid_edit_target("diary_grid_5_10_999_123") == (
        "diary_select_5_pending",
        "nhật ký",
    )
    assert _grid_edit_target("task_editor_1_1") is None


def test_only_checked_row_ids_are_used_for_update_action():
    frame = pd.DataFrame(
        [
            {"Chọn": False, "ID": 5, "Tên": "A"},
            {"Chọn": True, "ID": 6, "Tên": "B"},
        ]
    )
    assert _selected_grid_ids(frame) == [6]


def test_update_action_disables_for_zero_or_multiple_rows():
    empty = pd.DataFrame([{"Chọn": False, "ID": 1}])
    many = pd.DataFrame([{"Chọn": True, "ID": 1}, {"Chọn": True, "ID": 2}])
    assert _selected_grid_ids(empty) == []
    assert _selected_grid_ids(many) == [1, 2]
