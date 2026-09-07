from pathlib import Path

APP = Path(__file__).with_name('streamlit_app.py').read_text(encoding='utf-8')


def _between(start: str, end: str) -> str:
    i = APP.index(start)
    j = APP.index(end, i)
    return APP[i:j]


def test_rfa_rfi_save_auto_starts_workflow():
    block = _between('def _render_approval_document_type', 'def render_document_type')
    assert 'doc_id = db.save_document' in block
    after = block[block.index('doc_id = db.save_document'):]
    assert '_ensure_approval_workflow_started(' in after
    assert '"document", doc_type' in after
    assert 'tự động trình Ban điều hành phê duyệt' in after


def test_shopdrawing_save_auto_starts_workflow():
    block = _between('def _render_approval_shopdrawing_type', 'def render_drawing_type')
    assert 'drawing_id = db.save_drawing' in block
    after = block[block.index('drawing_id = db.save_drawing'):]
    assert '_ensure_approval_workflow_started(' in after
    assert '"drawing", drawing_type' in after
    assert 'tự động trình Ban điều hành phê duyệt' in after


def test_legacy_saved_file_without_workflow_is_recovered_when_opened():
    block = _between('def _render_online_approval', 'def _render_approval_document_type')
    assert 'if not wf:' in block
    assert 'if has_submission_file:' in block
    assert '_ensure_approval_workflow_started(' in block
    assert 'Hồ sơ đã được đưa vào luồng phê duyệt' in block


def test_contractor_record_is_locked_after_submission():
    assert 'contractor_edit_locked' in APP
    assert 'Nhà thầu chỉ được sửa khi một cấp duyệt yêu cầu chỉnh sửa' in APP

def test_non_admin_can_read_public_approval_directory_endpoint():
    gateway = Path(__file__).with_name('drive_gateway.py').read_text(encoding='utf-8')
    apps = Path(__file__).with_name('google_drive_appscript').joinpath('Code.gs').read_text(encoding='utf-8')
    assert 'def approval_users' in gateway
    assert '"approval_users"' in gateway
    assert "case 'approval_users': result = approvalUsers_(body); break;" in apps
    assert 'function approvalUsers_(body)' in apps
    approval_block = apps[apps.index('function approvalUsers_(body)'):apps.index('function setUser_(body)')]
    assert 'requireSession_(body)' in approval_block
    assert '.map(publicUser_)' in approval_block
