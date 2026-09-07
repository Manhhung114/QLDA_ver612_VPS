from pathlib import Path

APP = Path(__file__).with_name("streamlit_app.py").read_text(encoding="utf-8")


def test_v69_version_and_revision_specific_upload_panel():
    assert "Approval UI / Workflow engine: V6.9" in APP
    assert "def _revision_upload_panel_key" in APP
    assert "_revision_" in APP
    assert 'current_stage"] or "") == "CONTRACTOR"' in APP


def test_returned_contractor_opens_fresh_ticket_then_reruns():
    doc_start = APP.index('pre_panel_key = _revision_upload_panel_key(\n            "v69_doc_attach"')
    doc_end = APP.index('if attach_ready:', doc_start)
    doc_block = APP[doc_start:doc_end]
    assert "_prepare_inline_upload_ticket" in doc_block
    assert "st.rerun()" in doc_block
    assert 'key=f"approval_doc_attach_before_save_{pre_panel_key}"' in doc_block

    dr_start = APP.index('pre_panel_key = _revision_upload_panel_key(\n            "v69_drawing_attach"')
    dr_end = APP.index('if attach_ready:', dr_start)
    dr_block = APP[dr_start:dr_end]
    assert "_prepare_inline_upload_ticket" in dr_block
    assert "st.rerun()" in dr_block
    assert 'key=f"approval_sd_attach_before_save_{pre_panel_key}"' in dr_block


def test_resubmit_is_save_driven_no_separate_button():
    panel_start = APP.index('elif current_stage == "CONTRACTOR" and approval_role == "CONTRACTOR":')
    panel_end = APP.index('elif current_stage == "DONE":', panel_start)
    block = APP[panel_start:panel_end]
    assert "không dùng nút Trình lại riêng" in block
    assert "resubmit_" not in block
    assert "đính kèm file phiên bản mới" in block


def test_all_online_drawing_types_use_role_based_workflow():
    assert 'APPROVAL_ELIGIBLE_DRAWINGS = {"SHOPDRAWING", "AS_BUILT"}' in APP
    route_start = APP.index('def render_drawing_type(pid: int, drawing_type: str):')
    route_block = APP[route_start:route_start+350]
    assert "if drawing_type in APPROVAL_ELIGIBLE_DRAWINGS:" in route_block
    assert "_render_approval_shopdrawing_type(pid, drawing_type)" in route_block


def test_all_online_types_require_submission_file():
    start = APP.index('def _render_online_approval(')
    end = APP.index('steps = db.approval_steps', start)
    block = APP[start:end]
    assert 'subtype in APPROVAL_ELIGIBLE_DOCS' in block
    assert 'subtype in APPROVAL_ELIGIBLE_DRAWINGS' in block


def test_revision_requires_new_file_id_before_save():
    assert '_baseline_file_ids' in APP
    assert '_new_upload_detected' in APP
    assert 'current_file_ids - baseline_ids' in APP
    assert 'disabled=not attach_ready or contractor_attachment_count <= 0 or not revision_file_ready' in APP
    assert 'bắt buộc tải ít nhất 01 **file mới**' in APP
