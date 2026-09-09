from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cloud_db import CloudDatabase
import ipc_claim_v622 as ipc
from v622_ipc_claim_patch import DUE_DATE_PATCH_MARKER, install_ipc_claim_due_date, patch_ipc_claims


class IPCClaimDueDateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        install_ipc_claim_due_date()

    def test_due_date_column_is_added_and_persisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = CloudDatabase(Path(tmp) / "due_date.db")
            pid = db.add_project("IPC-DUE", "Dự án kiểm tra tới hạn")

            with db.connect() as c:
                ipc._ensure_tables(c)
                columns = [str(row[1]) for row in c.execute("PRAGMA table_info(payment_claims)").fetchall()]
                self.assertIn("payment_due_date", columns)
                c.execute(
                    """INSERT INTO payment_claims(
                           claim_id,project_id,claim_no,claim_code,payment_status,created_at,updated_at
                       ) VALUES(?,?,?,?,?,?,?)""",
                    ("claim-due-01", pid, "01", "IPC-01", "Đã duyệt", "2026-09-09", "2026-09-09"),
                )

            ipc.update_ipc_claim_finance(
                db,
                "claim-due-01",
                approved_amount=1_226_454_500,
                disbursed_amount=0,
                payment_status="Chờ giải ngân",
                payment_due_date="2026-09-30",
                disbursement_date="2026-10-02",
                note="Kiểm tra ngày tới hạn",
            )

            claim = ipc.list_ipc_claims(db, pid)[0]
            self.assertEqual(claim["payment_due_date"], "2026-09-30")
            self.assertEqual(claim["disbursement_date"], "2026-10-02")

    def test_due_date_survives_claim_revision_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = CloudDatabase(Path(tmp) / "revision.db")
            pid = db.add_project("IPC-DUE-REV", "Dự án revision")
            with db.connect() as c:
                ipc._ensure_tables(c)
                c.execute(
                    """INSERT INTO payment_claims(
                           claim_id,project_id,claim_no,claim_code,filename,batch_id,payment_status,
                           payment_due_date,created_at,updated_at
                       ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (
                        "claim-due-rev", pid, "02", "IPC-02", "old.xlsx", "old-batch", "Đã duyệt",
                        "2026-10-15", "2026-09-09", "2026-09-09",
                    ),
                )

            result = {
                "filename": "IPC#2_rev1.xlsx",
                "batch_id": "new-batch",
                "claim_no": "02",
                "claim_code": "IPC-02",
                "metadata": {"contractor": "Nhà thầu A", "contract_no": "HD-02"},
                "summary": {"contract_value": 10_000_000, "requested_amount": 1_000_000},
                "workbook_sheet_names": [],
                "workbook_sheets": {},
                "detail_items": [],
                "detail_line_count": 0,
                "warnings": [],
            }
            ipc.save_ipc_claim(db, pid, result)
            claim = ipc.list_ipc_claims(db, pid)[0]
            self.assertEqual(claim["payment_due_date"], "2026-10-15")

    def test_generated_app_installs_due_date_patch_before_renderer_import(self):
        source = '''\ndef render_cost_management(pid: int):\n    cost_tab, payment_tab, vo_tab = st.tabs(["BOQ", "Thanh toán & Giải ngân", "VO"])\n    with cost_tab:\n        st.write("cost")\n    with payment_tab:\n        st.write("legacy payment")\n    with vo_tab:\n        st.write("vo")\n'''
        patched = patch_ipc_claims(source)
        self.assertIn("_v622_install_ipc_claim_due_date()", patched)
        self.assertIn(DUE_DATE_PATCH_MARKER, DUE_DATE_PATCH_MARKER)
        compile(patched, "patched_due_date.py", "exec")


if __name__ == "__main__":
    unittest.main()
