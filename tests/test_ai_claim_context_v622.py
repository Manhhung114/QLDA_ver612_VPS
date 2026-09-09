from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import ai_service
from ai_claim_context_v622 import _claim_appendix, _payment_delay_days
from cloud_db import CloudDatabase


class AIClaimPaymentDelayContextTests(unittest.TestCase):
    def test_payment_delay_formula_per_claim(self):
        self.assertEqual(_payment_delay_days("2025-04-11", "2025-04-20"), 9)
        self.assertEqual(_payment_delay_days("2025-04-11", "2025-04-11"), 0)
        self.assertEqual(_payment_delay_days("2025-04-11", "2025-04-01"), 0)
        self.assertIsNone(_payment_delay_days("2025-04-11", ""))

    def test_ai_context_contains_due_disbursement_and_late_days_for_each_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "ai_claim_delay.db"
            db = CloudDatabase(db_path)
            pid = db.add_project("AI-CLAIM", "Dự án kiểm tra Claim AI")

            with db.connect() as c:
                c.execute(
                    """CREATE TABLE payment_claims(
                           claim_id TEXT PRIMARY KEY,
                           project_id INTEGER,
                           claim_no TEXT,
                           claim_code TEXT,
                           filename TEXT,
                           contractor TEXT,
                           contract_no TEXT,
                           from_date TEXT,
                           to_date TEXT,
                           contract_value REAL DEFAULT 0,
                           requested_amount REAL DEFAULT 0,
                           approved_amount REAL DEFAULT 0,
                           disbursed_amount REAL DEFAULT 0,
                           certified_cumulative REAL DEFAULT 0,
                           retention_cumulative REAL DEFAULT 0,
                           advance_amount REAL DEFAULT 0,
                           advance_recovery REAL DEFAULT 0,
                           current_deductions REAL DEFAULT 0,
                           payment_status TEXT,
                           payment_due_date TEXT,
                           disbursement_date TEXT,
                           latest_revision INTEGER DEFAULT 0,
                           updated_at TEXT
                       )"""
                )
                c.execute(
                    """INSERT INTO payment_claims VALUES(
                           ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
                       )""",
                    (
                        "c01", pid, "01", "IPC-01", "IPC#1.xlsx", "SIGMA", "HD-01",
                        "2025-03-04", "2025-03-25", 10_000_000, 1_000_000, 1_000_000,
                        1_000_000, 1_000_000, 0, 0, 0, 0, "Đã duyệt",
                        "2025-04-11", "2025-04-20", 0, "2025-04-20",
                    ),
                )
                c.execute(
                    """INSERT INTO payment_claims VALUES(
                           ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
                       )""",
                    (
                        "c02", pid, "02", "IPC-02", "IPC#2.xlsx", "SIGMA", "HD-01",
                        "2025-03-26", "2025-04-25", 10_000_000, 2_000_000, 2_000_000,
                        2_000_000, 3_000_000, 0, 0, 0, 0, "Đã duyệt",
                        "2025-05-10", "2025-05-08", 0, "2025-05-08",
                    ),
                )

            builder = ai_service.ProjectContextBuilder(db_path)
            context = _claim_appendix(builder, pid, "số ngày trễ thanh toán từng claim")

            self.assertIn("[CLAIM:IPC-01]", context)
            self.assertIn("tới_hạn=2025-04-11", context)
            self.assertIn("ngày_giải_ngân=2025-04-20", context)
            self.assertIn("late_payment_days=9", context)
            self.assertIn("[CLAIM-DELAY:IPC-01]", context)
            self.assertIn("số ngày trễ=9 ngày", context)

            self.assertIn("[CLAIM:IPC-02]", context)
            self.assertIn("late_payment_days=0", context)
            self.assertIn("[CLAIM-DELAY:IPC-02]", context)
            self.assertIn("số ngày trễ=0 ngày", context)

            self.assertIn("2/2 Claim đủ cả ngày tới hạn và ngày giải ngân", context)
            self.assertIn("1 Claim trễ hạn", context)
            self.assertIn("KHÔNG được kết luận thiếu dữ liệu", context)


if __name__ == "__main__":
    unittest.main()
