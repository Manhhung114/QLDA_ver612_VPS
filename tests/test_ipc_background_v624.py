from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from pathlib import Path

from cloud_db import CloudDatabase
from ipc_background_v624 import parse_ipc_path
from ipc_claim_v622 import ipc_claim_items, ipc_claim_revisions, list_ipc_claims, update_ipc_claim_finance
from ipc_persist_v624 import VERIFY_COMPLETE, save_ipc_result_batched
from tests.test_ipc_claim_v622 import sample_ipc_bytes


def _bulk_result(line_count: int = 4_029) -> dict:
    numeric_fields = (
        "contract_qty",
        "material_unit_price",
        "labor_unit_price",
        "contract_amount",
        "material_previous_qty",
        "material_current_qty",
        "material_cumulative_qty",
        "installation_previous_pct",
        "installation_current_pct",
        "installation_cumulative_pct",
        "material_previous_value",
        "material_current_value",
        "material_cumulative_value",
        "installation_previous_value",
        "installation_current_value",
        "installation_cumulative_value",
        "deduction_previous",
        "deduction_current",
        "deduction_cumulative",
        "current_value",
        "cumulative_value",
        "completion_ratio",
    )
    items = []
    for index in range(1, line_count + 1):
        item = {
            "sheet_name": "GTHT",
            "row_no": index + 14,
            "seq": index,
            "boq_item": f"Hạng mục IPC {index}",
            "unit": "m",
            "spec": "",
            "item_code": "",
            "brand": "",
            "origin": "",
            "note": "",
            "cost_code": "",
            "system": "MEP",
        }
        item.update({field: 0.0 for field in numeric_fields})
        items.append(item)
    return {
        "filename": "IPC-10.xlsx",
        "batch_id": "ipc10-4029-row-proof",
        "claim_no": "10",
        "claim_code": "IPC-10",
        "metadata": {"contractor": "Nhà thầu IPC-10"},
        "summary": {"requested_amount": 0, "cumulative_completed": 0},
        "detail_items": items,
        "detail_line_count": line_count,
    }


class IPCBackgroundPathTests(unittest.TestCase):
    def test_parses_from_path_and_preserves_exact_source_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "IPC-01.xlsx"
            original = sample_ipc_bytes()
            path.write_bytes(original)
            before = hashlib.sha256(path.read_bytes()).hexdigest()

            result = parse_ipc_path(path, path.name)

            after = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertEqual(before, after)
            self.assertEqual(result["source_sha256"], before)
            self.assertEqual(result["batch_id"], before[:20])
            self.assertEqual(result["claim_code"], "IPC-01")
            self.assertEqual(result["detail_line_count"], 2)
            self.assertEqual(result["source_file_size"], len(original))


class IPCBackgroundPersistenceTests(unittest.TestCase):
    def _database(self, directory: str) -> tuple[CloudDatabase, int]:
        db = CloudDatabase(Path(directory) / "qlda.db")
        project_id = db.add_project("P-V6243", "Kiểm tra IPC V6.24.3")
        return db, project_id

    def _result(self, directory: str, *, second_value: float = 500_000.0) -> dict:
        path = Path(directory) / f"IPC-{int(second_value)}.xlsx"
        path.write_bytes(sample_ipc_bytes(second_value=second_value))
        return parse_ipc_path(path, path.name)

    def test_reports_database_counts_and_keeps_each_claim_independent(self):
        with tempfile.TemporaryDirectory() as tmp:
            db, project_id = self._database(tmp)
            result = self._result(tmp)
            stats = save_ipc_result_batched(db, project_id, result, batch_size=50)

            self.assertEqual(stats["expected_rows"], 2)
            self.assertEqual(stats["scanned_rows"], 2)
            self.assertEqual(stats["prepared_rows"], 2)
            self.assertEqual(stats["inserted_rows"], 2)
            self.assertEqual(stats["written_rows"], 2)
            self.assertEqual(stats["failed_rows"], 0)
            self.assertEqual(stats["verification_status"], VERIFY_COMPLETE)
            self.assertTrue(stats["verified_postgresql"])
            self.assertEqual(stats["revision_no"], 0)

            same = save_ipc_result_batched(db, project_id, result, batch_size=50)
            self.assertEqual(same["claim_id"], stats["claim_id"])
            self.assertEqual(same["revision_no"], 0)
            self.assertEqual(len(ipc_claim_revisions(db, stats["claim_id"])), 1)

            second_claim_path = Path(tmp) / "IPC-02.xlsx"
            second_claim_path.write_bytes(sample_ipc_bytes(claim_no="02"))
            second = save_ipc_result_batched(
                db,
                project_id,
                parse_ipc_path(second_claim_path, second_claim_path.name),
                batch_size=50,
            )
            self.assertNotEqual(second["claim_id"], stats["claim_id"])
            self.assertEqual(len(list_ipc_claims(db, project_id)), 2)

    def test_revision_preserves_finance_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            db, project_id = self._database(tmp)
            first = save_ipc_result_batched(db, project_id, self._result(tmp), batch_size=50)
            update_ipc_claim_finance(
                db,
                first["claim_id"],
                approved_amount=1_200_000_000,
                disbursed_amount=1_100_000_000,
                payment_status="Đã giải ngân",
                disbursement_date="2025-04-05",
                note="Giữ nguyên qua revision",
            )

            revised = save_ipc_result_batched(
                db,
                project_id,
                self._result(tmp, second_value=700_000.0),
                batch_size=50,
            )
            claim = next(row for row in list_ipc_claims(db, project_id) if row["claim_id"] == first["claim_id"])
            self.assertEqual(revised["claim_id"], first["claim_id"])
            self.assertEqual(revised["revision_no"], 1)
            self.assertEqual(float(claim["approved_amount"]), 1_200_000_000)
            self.assertEqual(float(claim["disbursed_amount"]), 1_100_000_000)
            self.assertEqual(claim["payment_status"], "Đã giải ngân")

    def test_batches_and_verifies_all_4029_ipc10_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            db, project_id = self._database(tmp)
            stats = save_ipc_result_batched(
                db,
                project_id,
                _bulk_result(),
                batch_size=500,
            )

            self.assertEqual(stats["claim_code"], "IPC-10")
            self.assertEqual(stats["expected_rows"], 4_029)
            self.assertEqual(stats["inserted_rows"], 4_029)
            self.assertEqual(stats["written_rows"], 4_029)
            self.assertEqual(stats["failed_rows"], 0)
            self.assertEqual(stats["verification_status"], VERIFY_COMPLETE)

    def test_row_mismatch_rolls_back_and_keeps_previous_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            db, project_id = self._database(tmp)
            first_result = self._result(tmp)
            first = save_ipc_result_batched(db, project_id, first_result, batch_size=50)
            original_batch = first["batch_id"]

            with db.connect() as connection:
                connection.execute(
                    "CREATE TRIGGER skip_ipc_row_v6243 BEFORE INSERT ON payment_claim_items "
                    "WHEN NEW.boq_item='Ống gió chống cháy EI45' BEGIN SELECT RAISE(IGNORE); END"
                )

            with self.assertRaisesRegex(Exception, "CHƯA ĐỦ"):
                save_ipc_result_batched(
                    db,
                    project_id,
                    self._result(tmp, second_value=700_000.0),
                    batch_size=50,
                )

            claim = next(row for row in list_ipc_claims(db, project_id) if row["claim_id"] == first["claim_id"])
            self.assertEqual(claim["batch_id"], original_batch)
            self.assertEqual(int(claim["latest_revision"]), 0)
            self.assertEqual(len(ipc_claim_items(db, first["claim_id"])), 2)
            self.assertEqual(len(ipc_claim_revisions(db, first["claim_id"])), 1)


@unittest.skipUnless(os.environ.get("DATABASE_URL"), "DATABASE_URL chưa được cấu hình")
class IPCBackgroundPostgreSQLTests(unittest.TestCase):
    def test_confirms_every_ipc_row_from_real_postgresql(self):
        from postgres_backend_v622 import _PGConnectionContext

        class PostgreSQLTestDatabase:
            def __init__(self, url: str):
                self.url = url

            def connect(self):
                return _PGConnectionContext(self.url)

        db = PostgreSQLTestDatabase(str(os.environ["DATABASE_URL"]))
        tables = (
            "payment_claim_items",
            "payment_claim_revisions",
            "payment_claim_workbooks",
            "payment_claims",
            "payment_tracking",
        )
        with db.connect() as connection:
            for table in tables:
                connection.execute(f"DROP TABLE IF EXISTS {table}")
            connection.execute(
                "CREATE TABLE payment_tracking("
                "id BIGSERIAL PRIMARY KEY,project_id BIGINT NOT NULL,payment_code TEXT DEFAULT '',"
                "task_ref TEXT DEFAULT '',installment TEXT DEFAULT '',certified_cumulative REAL DEFAULT 0,"
                "paid_amount REAL DEFAULT 0,advance_amount REAL DEFAULT 0,advance_recovery REAL DEFAULT 0,"
                "planned_disbursement_pct REAL DEFAULT 0,payment_status TEXT DEFAULT '',payment_date TEXT DEFAULT '',"
                "note TEXT DEFAULT '',created_at TEXT DEFAULT '',updated_at TEXT DEFAULT '')"
            )

        try:
            stats = save_ipc_result_batched(db, 6243, _bulk_result(), batch_size=500)

            with db.connect() as connection:
                row = connection.execute(
                    "SELECT COUNT(*) AS row_count FROM payment_claim_items WHERE claim_id=?",
                    (stats["claim_id"],),
                ).fetchone()

            self.assertEqual(int(row["row_count"]), 4_029)
            self.assertEqual(stats["expected_rows"], 4_029)
            self.assertEqual(stats["written_rows"], 4_029)
            self.assertEqual(stats["failed_rows"], 0)
            self.assertEqual(stats["verification_status"], VERIFY_COMPLETE)
            self.assertTrue(stats["verified_postgresql"])
        finally:
            with db.connect() as connection:
                for table in tables:
                    connection.execute(f"DROP TABLE IF EXISTS {table}")


if __name__ == "__main__":
    unittest.main()
