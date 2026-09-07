from pathlib import Path
APP = Path(__file__).with_name("streamlit_app.py").read_text(encoding="utf-8")

def test_v612_visible_and_native_uploader_exists():
    assert "Approval UI / Workflow engine: V6.12" in APP
    assert "def _render_contractor_upload_expander" in APP
    assert "with st.expander(label, expanded=is_revision_return)" in APP
    assert "_v612_native_files" in APP
    assert "Tải file đã chọn lên Google Drive" in APP

def test_v612_applies_to_documents_and_drawings():
    assert 'kind="document", subtype=doc_type' in APP
    assert 'kind="drawing", subtype=drawing_type' in APP
    assert 'show_contractor_upload=False' in APP

def test_v612_keeps_large_file_resumable_link():
    assert "Mở trình tải file lớn lên Google Drive" in APP
    assert "_ensure_revision_upload_ticket" in APP
