from qlda.presentation.streamlit.edit_sheet_ux import _edit_button_label, _selection_active


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
