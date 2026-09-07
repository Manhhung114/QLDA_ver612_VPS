from pathlib import Path
from tempfile import TemporaryDirectory

from v621_webopt_runtime import install_runtime

install_runtime()

from cloud_db import CloudDatabase


def _approvers():
    return {
        "CONTRACTOR": {"email": "contractor@example.com", "name": "Nhà thầu"},
        "SITE_MANAGEMENT": {"email": "site@example.com", "name": "Ban điều hành"},
        "CONSULTANT": {"email": "tvgs@example.com", "name": "TVGS"},
        "PROJECT_MANAGEMENT": {"email": "pm@example.com", "name": "Ban QLDA"},
    }


def test_webopt_batch_lookup_and_resubmit():
    with TemporaryDirectory() as td:
        db = CloudDatabase(Path(td) / "db.sqlite")
        pid = db.add_project("P21", "V6.21 WebOpt")
        rid = db.save_document(pid, "RFA", {"code": "RFA-001", "subject": "Test", "status": "Soạn thảo"})
        wid = db.start_approval_workflow(pid, "document", "RFA", rid, "RFA-001", "contractor@example.com", _approvers(), "Nhà thầu")
        db.approval_action(wid, "SITE_MANAGEMENT", "site@example.com", "REQUEST_REVISION", "Sửa", "Ban điều hành", actor_role="SITE_MANAGEMENT")
        with db.connect() as c:
            c.execute("UPDATE approval_workflows SET subtype='legacy_rfa' WHERE id=?", (wid,))
        db.save_document(pid, "RFA", {"code": "RFA-001", "subject": "Đã sửa", "status": "Soạn thảo"}, rid)
        with db.connect() as c:
            wf = c.execute("SELECT * FROM approval_workflows WHERE id=?", (wid,)).fetchone()
        assert wf["current_stage"] == "SITE_MANAGEMENT"
        assert int(wf["revision_no"]) == 1
        batch = db.approval_workflows_for_records(pid, "document", "RFA", [rid])
        assert rid in batch


def test_webopt_sqlite_indexes():
    with TemporaryDirectory() as td:
        db = CloudDatabase(Path(td) / "db.sqlite")
        with db.connect() as c:
            idx = {r["name"] for r in c.execute("PRAGMA index_list('approval_workflows')")}
            assert "idx_approval_workflows_record_fast" in idx
            assert c.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
