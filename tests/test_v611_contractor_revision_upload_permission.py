from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP = (ROOT / 'streamlit_app.py').read_text(encoding='utf-8')
GW = (ROOT / 'drive_gateway.py').read_text(encoding='utf-8')
GS = (ROOT / 'google_drive_appscript' / 'Code.gs').read_text(encoding='utf-8')


def test_gateway_sends_approval_submission_purpose():
    assert 'upload_purpose: str = ""' in GW
    assert '"upload_purpose": str(upload_purpose or "")' in GW
    assert 'upload_purpose="approval_submission" if approval_submission else ""' in APP


def test_contractor_read_role_can_upload_only_approval_targets():
    assert 'function requireUploadRole_(body)' in GS
    assert "approvalRole === 'CONTRACTOR'" in GS
    assert "purpose !== 'approval_submission'" in GS
    assert "['RFA','RFI']" in GS
    assert "['SHOPDRAWING','AS_BUILT']" in GS
    assert "const session = requireUploadRole_(body);" in GS


def test_revision_has_streamlit_file_uploader_fallback():
    assert 'Tải file cập nhật trực tiếp trong app (dự phòng)' in APP
    assert '_contractor_fallback_files' in APP
    assert 'upload_purpose="approval_submission"' in APP
    assert 'st.session_state[panel_key + "_new_upload_detected"] = True' in APP


def test_version_marker():
    assert 'Approval UI / Workflow engine: V6.11' in APP
    assert "version: '6.11'" in GS
