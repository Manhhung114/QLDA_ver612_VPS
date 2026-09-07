from pathlib import Path
from cloud_db import CloudDatabase

APP = Path(__file__).with_name('streamlit_app.py').read_text(encoding='utf-8')


def test_shopdrawing_routes_to_role_based_ui():
    assert 'if drawing_type == "SHOPDRAWING":' in APP
    assert 'return _render_approval_shopdrawing_type(pid, drawing_type)' in APP


def test_shopdrawing_role_ui_contains_required_fields_and_actions():
    start = APP.index('def _render_approval_shopdrawing_type')
    end = APP.index('def render_drawing_type', start)
    block = APP[start:end]
    for label in [
        'Mã Shopdrawing *', 'Nội dung trình duyệt / Tên bản vẽ *',
        'Bộ môn / Hệ', 'Nhà thầu / Đơn vị', 'Mức độ', 'Revision',
        'Người trình', 'Ngày trình', 'Hạn xử lý', 'Mô tả',
        '💾 Lưu Shopdrawing', '📤 Tải file lên lưu', '📝 Mở / xử lý Shopdrawing',
    ]:
        assert label in block
    assert '_render_online_approval(' in block
    assert 'if is_contractor or is_reviewer:' in block


def test_drawing_schema_supports_shopdrawing_submission_fields(tmp_path):
    db = CloudDatabase(tmp_path / 'v64.sqlite')
    pid = db.add_project('P1', 'Project 1')
    did = db.save_drawing(pid, 'SHOPDRAWING', {
        'drawing_no': 'S2-MEP-001',
        'title': 'Shopdrawing ống gió',
        'discipline': 'MEP',
        'revision': '00',
        'issuer': 'Nhà thầu A',
        'receiver': 'Nguyễn Văn A',
        'received_date': '2026-08-28',
        'due_date': '2026-09-04',
        'priority': 'Trung bình',
        'description': 'Trình duyệt Shopdrawing',
        'status': 'Mới nhận',
    })
    row = db.drawing(did)
    assert row['due_date'] == '2026-09-04'
    assert row['priority'] == 'Trung bình'
    assert row['description'] == 'Trình duyệt Shopdrawing'
