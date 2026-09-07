from pathlib import Path

APP = Path(__file__).with_name("streamlit_app.py").read_text(encoding="utf-8")


def test_v610_version_visible():
    assert "Approval UI / Workflow engine: V6.10" in APP


def test_returned_document_auto_creates_uploader():
    start = APP.index('pre_panel_key = _revision_upload_panel_key(\n            "v69_doc_attach"')
    end = APP.index('revision_file_ready =', start)
    block = APP[start:end]
    assert "_ensure_revision_upload_ticket" in block
    assert '"🔄 Tạo lại phiên đính kèm file" if is_revision_return else "📎 Đính kèm file"' in block
    assert "KHÔNG rerun" in block


def test_returned_drawing_auto_creates_uploader():
    start = APP.index('pre_panel_key = _revision_upload_panel_key(\n            "v69_drawing_attach"')
    end = APP.index('revision_file_ready =', start)
    block = APP[start:end]
    assert "_ensure_revision_upload_ticket" in block
    assert '"🔄 Tạo lại phiên đính kèm file" if is_revision_return else "📎 Đính kèm file"' in block


def test_uploader_has_tab_fallback_and_iframe():
    start = APP.index('def _render_inline_drive_attachments')
    end = APP.index('def _record_drive_counts', start)
    block = APP[start:end]
    assert "Mở trình tải file ở tab riêng" in block
    assert "components.iframe" in block
    assert "Phiên đính kèm file đã sẵn sàng" in block


def test_all_online_types_still_enabled():
    assert 'APPROVAL_ELIGIBLE_DOCS = {"RFA", "RFI"}' in APP
    assert 'APPROVAL_ELIGIBLE_DRAWINGS = {"SHOPDRAWING", "AS_BUILT"}' in APP
