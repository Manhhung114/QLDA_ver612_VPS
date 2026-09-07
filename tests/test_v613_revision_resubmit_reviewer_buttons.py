from pathlib import Path
from tempfile import TemporaryDirectory

from cloud_db import CloudDatabase


def _approvers():
    return {
        "CONTRACTOR": {"email": "contractor@example.com", "name": "Nhà thầu A"},
        "SITE_MANAGEMENT": {"email": "site@example.com", "name": "Ban điều hành"},
        "CONSULTANT": {"email": "tvgs@example.com", "name": "TVGS"},
        "PROJECT_MANAGEMENT": {"email": "pm@example.com", "name": "Ban QLDA"},
    }


def test_document_saved_after_return_is_repaired_to_return_stage():
    with TemporaryDirectory() as td:
        db = CloudDatabase(Path(td) / "db.sqlite")
        pid = db.add_project("P01", "Dự án")
        rid = db.save_document(pid, "RFA", {"code": "S2-MEP-002", "subject": "RFA", "status": "Soạn thảo"})
        wid = db.start_approval_workflow(pid, "document", "RFA", rid, "S2-MEP-002", "contractor@example.com", _approvers(), "Nhà thầu A")
        db.approval_action(wid, "SITE_MANAGEMENT", "site@example.com", "REQUEST_REVISION", "Sửa hồ sơ", "Ban điều hành", actor_role="SITE_MANAGEMENT")
        wf = db.approval_workflow(pid, "document", "RFA", rid)
        assert wf["current_stage"] == "CONTRACTOR"
        with db.connect() as c:
            c.execute("UPDATE documents SET updated_at='2099-01-01 00:00:00' WHERE id=?", (rid,))
        repaired = db.repair_revision_resubmit_if_saved(pid, "document", "RFA", rid, "contractor@example.com", "Nhà thầu A")
        assert repaired["repaired"] is True
        assert repaired["current_stage"] == "SITE_MANAGEMENT"
        assert repaired["revision_no"] == 1
        steps = {x["stage_code"]: x for x in db.approval_steps(wid)}
        assert steps["SITE_MANAGEMENT"]["status"] == "Đang chờ duyệt"


def test_drawing_saved_after_tvgs_return_is_repaired_to_tvgs():
    with TemporaryDirectory() as td:
        db = CloudDatabase(Path(td) / "db.sqlite")
        pid = db.add_project("P01", "Dự án")
        rid = db.save_drawing(pid, "SHOPDRAWING", {"drawing_no": "S2-MEP-003", "title": "Shop", "status": "Mới nhận"})
        wid = db.start_approval_workflow(pid, "drawing", "SHOPDRAWING", rid, "S2-MEP-003", "contractor@example.com", _approvers(), "Nhà thầu A")
        db.approval_action(wid, "SITE_MANAGEMENT", "site@example.com", "APPROVE", "OK", "Ban điều hành", actor_role="SITE_MANAGEMENT")
        db.approval_action(wid, "CONSULTANT", "tvgs@example.com", "REQUEST_REVISION", "Sửa bản vẽ", "TVGS", actor_role="CONSULTANT")
        with db.connect() as c:
            c.execute("UPDATE drawings SET updated_at='2099-01-01 00:00:00' WHERE id=?", (rid,))
        repaired = db.repair_revision_resubmit_if_saved(pid, "drawing", "SHOPDRAWING", rid, "contractor@example.com", "Nhà thầu A")
        assert repaired["repaired"] is True
        assert repaired["current_stage"] == "CONSULTANT"
        assert repaired["revision_no"] == 1
        steps = {x["stage_code"]: x for x in db.approval_steps(wid)}
        assert steps["SITE_MANAGEMENT"]["status"] == "Đã duyệt"
        assert steps["CONSULTANT"]["status"] == "Đang chờ duyệt"


def test_no_auto_resubmit_before_contractor_save():
    with TemporaryDirectory() as td:
        db = CloudDatabase(Path(td) / "db.sqlite")
        pid = db.add_project("P01", "Dự án")
        rid = db.save_document(pid, "RFI", {"code": "S2-MEP-004", "subject": "RFI", "status": "Soạn thảo"})
        wid = db.start_approval_workflow(pid, "document", "RFI", rid, "S2-MEP-004", "contractor@example.com", _approvers(), "Nhà thầu A")
        db.approval_action(wid, "SITE_MANAGEMENT", "site@example.com", "REQUEST_REVISION", "Sửa", "Ban điều hành", actor_role="SITE_MANAGEMENT")
        repaired = db.repair_revision_resubmit_if_saved(pid, "document", "RFI", rid, "contractor@example.com", "Nhà thầu A")
        assert repaired["repaired"] is False
        assert repaired["reason"] == "record_not_saved_after_return"
        assert db.approval_workflow(pid, "document", "RFI", rid)["current_stage"] == "CONTRACTOR"


def test_app_uses_v613_failsafe_for_all_online_types():
    app = Path(__file__).with_name("streamlit_app.py").read_text(encoding="utf-8")
    assert ("Approval UI / Workflow engine: V6.13" in app or "Approval UI / Workflow engine: V6.14" in app)
    assert ("Workflow engine: **V6.13**" in app or "Workflow engine: **V6.14**" in app)
    assert "repair_revision_resubmit_if_saved" in app
    assert 'APPROVAL_ELIGIBLE_DOCS = {"RFA", "RFI"}' in app
    assert 'APPROVAL_ELIGIBLE_DRAWINGS = {"SHOPDRAWING", "AS_BUILT"}' in app
    assert '"✅ Phê duyệt"' in app
    assert 'approval_role == current_stage' in app
