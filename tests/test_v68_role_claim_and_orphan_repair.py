from pathlib import Path
from tempfile import TemporaryDirectory

from cloud_db import CloudDatabase


def test_role_based_claim_without_preassigned_reviewer_emails():
    with TemporaryDirectory() as td:
        db = CloudDatabase(Path(td) / "qlda_v68.db")
        pid = db.add_project("P68", "Dự án V6.8")
        doc_id = db.save_document(pid, "RFA", {
            "code": "S2-MEP-068",
            "subject": "RFA V6.8",
            "status": "Soạn thảo",
        })
        # Chỉ xác định Nhà thầu; 3 cấp duyệt cố ý để trống email.
        approvers = {
            "CONTRACTOR": {"email": "contractor@example.com", "name": "Nhà thầu"},
        }
        wid = db.start_approval_workflow(
            pid, "document", "RFA", doc_id, "S2-MEP-068",
            "contractor@example.com", approvers, submitted_name="Nhà thầu",
        )
        steps = {r["stage_code"]: r for r in db.approval_steps(wid)}
        assert steps["SITE_MANAGEMENT"]["approver_email"] == ""

        r1 = db.approval_action(
            wid, "SITE_MANAGEMENT", "site@example.com", "APPROVE", "Đồng ý",
            actor_name="Ban điều hành", actor_role="SITE_MANAGEMENT",
        )
        assert r1["current_stage"] == "CONSULTANT"

        r2 = db.approval_action(
            wid, "CONSULTANT", "tvgs@example.com", "APPROVE", "Đạt",
            actor_name="TVGS", actor_role="CONSULTANT",
        )
        assert r2["current_stage"] == "PROJECT_MANAGEMENT"

        r3 = db.approval_action(
            wid, "PROJECT_MANAGEMENT", "pm@example.com", "APPROVE", "Phê duyệt",
            actor_name="Ban QLDA", actor_role="PROJECT_MANAGEMENT",
        )
        assert r3["completed"] is True
        wf = db.approval_workflow(pid, "document", "RFA", doc_id)
        assert wf["current_stage"] == "DONE"


def test_source_has_v68_fail_safe_and_version_marker():
    app = Path(__file__).with_name("streamlit_app.py").read_text(encoding="utf-8")
    assert "Approval UI / Workflow engine: V6.8" in app
    assert "tự sửa các hồ sơ legacy đã có file nhưng chưa có workflow" in app
    assert "reviewer_can_claim" in app
    assert "actor_role=approval_role" in app


if __name__ == "__main__":
    test_role_based_claim_without_preassigned_reviewer_emails()
    test_source_has_v68_fail_safe_and_version_marker()
    print("OK - V6.8 role claim + orphan repair")
