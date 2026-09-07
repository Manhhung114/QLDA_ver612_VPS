from pathlib import Path

APP = Path(__file__).with_name('streamlit_app.py').read_text(encoding='utf-8')


def _between(start: str, end: str) -> str:
    i = APP.index(start)
    j = APP.index(end, i)
    return APP[i:j]


def test_rfa_rfi_attach_is_before_save():
    block = _between('def _render_approval_document_type', 'def render_document_type')
    assert '📎 Đính kèm file' in block
    assert 'approval_doc_attach_before_save_' in block
    assert 'approval_doc_save_after_attach_' in block
    assert block.index('approval_doc_attach_before_save_') < block.index('approval_doc_save_after_attach_')
    assert 'disabled=not attach_ready or contractor_attachment_count <= 0' in block


def test_shopdrawing_attach_is_before_save():
    block = _between('def _render_approval_shopdrawing_type', 'def render_drawing_type')
    assert '📎 Đính kèm file' in block
    assert 'approval_sd_attach_before_save_' in block
    assert 'approval_sd_save_after_attach_' in block
    assert block.index('approval_sd_attach_before_save_') < block.index('approval_sd_save_after_attach_')
    assert 'disabled=not attach_ready or contractor_attachment_count <= 0' in block


def test_upload_can_use_code_before_sqlite_record_exists():
    assert 'record_id=int(selected or 0)' in APP
    assert 'record_code=normalized_code' in APP
    assert 'record_code=normalized_number' in APP
