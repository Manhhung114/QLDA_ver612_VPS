from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from boq_persist_v624 import VERIFY_COMPLETE, save_boq_result_batched
from cloud_db import CloudDatabase


def _result(*names: str) -> dict:
    items = [
        {
            "sheet": "BOQ",
            "row_no": index + 2,
            "task_ref": str(index + 1),
            "boq_item": name,
            "quantity": 1,
            "unit": "m",
            "unit_price": 100,
            "budget_total": 100,
        }
        for index, name in enumerate(names)
    ]
    return {
        "filename": "BOQ-goc.xlsx",
        "batch_id": "abcdef1234567890",
        "detail_items": items,
        "detail_line_count": len(items),
        "detail_grand_total": len(items) * 100,
    }


class BOQPostgreSQLRowVerificationTests(unittest.TestCase):
    def _database(self, directory: str) -> tuple[CloudDatabase, int]:
        db = CloudDatabase(Path(directory) / "qlda.db")
        project_id = db.add_project("P-V624", "Kiểm tra BOQ V6.24.2")
        return db, project_id

    def test_reports_expected_scanned_written_and_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            db, project_id = self._database(tmp)
            stats = save_boq_result_batched(
                db,
                project_id,
                _result("Cáp điện", "Ống điện", "Tủ điện"),
                batch_size=50,
            )

            self.assertEqual(stats["expected_rows"], 3)
            self.assertEqual(stats["scanned_rows"], 3)
            self.assertEqual(stats["prepared_rows"], 3)
            self.assertEqual(stats["inserted"], 3)
            self.assertEqual(stats["written_rows"], 3)
            self.assertEqual(stats["failed_rows"], 0)
            self.assertEqual(stats["verification_status"], VERIFY_COMPLETE)
            self.assertTrue(stats["verified_postgresql"])

    def test_rolls_back_when_database_writes_fewer_rows_than_expected(self):
        with tempfile.TemporaryDirectory() as tmp:
            db, project_id = self._database(tmp)
            with db.connect() as connection:
                connection.execute(
                    "CREATE TRIGGER skip_one_v624 BEFORE INSERT ON cost_budgets "
                    "WHEN NEW.boq_item='BỎ QUA' BEGIN SELECT RAISE(IGNORE); END"
                )

            with self.assertRaisesRegex(Exception, "CHƯA ĐỦ"):
                save_boq_result_batched(
                    db,
                    project_id,
                    _result("Cáp điện", "BỎ QUA"),
                    batch_size=50,
                )

            with db.connect() as connection:
                row = connection.execute(
                    "SELECT COUNT(*) FROM cost_budgets WHERE project_id=?",
                    (project_id,),
                ).fetchone()
            self.assertEqual(int(row[0]), 0)

    def test_rejects_parser_count_mismatch_before_database_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            db, project_id = self._database(tmp)
            result = _result("Cáp điện", "Ống điện")
            result["detail_line_count"] = 3

            with self.assertRaisesRegex(Exception, "chưa nhất quán"):
                save_boq_result_batched(db, project_id, result)

            with db.connect() as connection:
                row = connection.execute(
                    "SELECT COUNT(*) FROM cost_budgets WHERE project_id=?",
                    (project_id,),
                ).fetchone()
            self.assertEqual(int(row[0]), 0)


@unittest.skipUnless(os.environ.get("DATABASE_URL"), "DATABASE_URL chưa được cấu hình")
class BOQPostgreSQLIntegrationTests(unittest.TestCase):
    def test_confirms_rows_from_real_postgresql(self):
        from postgres_backend_v622 import _PGConnectionContext

        class PostgreSQLTestDatabase:
            def __init__(self, url: str):
                self.url = url

            def connect(self):
                return _PGConnectionContext(self.url)

        db = PostgreSQLTestDatabase(str(os.environ["DATABASE_URL"]))
        with db.connect() as connection:
            connection.execute("DROP TABLE IF EXISTS cost_budgets")
            connection.execute(
                "CREATE TABLE cost_budgets("
                "id BIGSERIAL PRIMARY KEY,project_id BIGINT NOT NULL,task_ref TEXT DEFAULT '',"
                "boq_item TEXT NOT NULL,quantity REAL DEFAULT 0,unit TEXT DEFAULT '',"
                "unit_price REAL DEFAULT 0,budget_total REAL DEFAULT 0,contract_type TEXT DEFAULT '',"
                "contractor TEXT DEFAULT '',note TEXT DEFAULT '',created_at TEXT DEFAULT '',updated_at TEXT DEFAULT ''"
                ")"
            )

        try:
            stats = save_boq_result_batched(
                db,
                6242,
                _result("Cáp điện", "Ống điện", "Tủ điện", "Máy biến áp"),
                batch_size=50,
            )
            with db.connect() as connection:
                row = connection.execute(
                    "SELECT COUNT(*) AS row_count FROM cost_budgets "
                    "WHERE project_id=? AND note LIKE ?",
                    (6242, "[QLDA_BOQ_EXCEL]%|batch=abcdef1234567890"),
                ).fetchone()

            self.assertEqual(int(row["row_count"]), 4)
            self.assertEqual(stats["expected_rows"], 4)
            self.assertEqual(stats["written_rows"], 4)
            self.assertEqual(stats["failed_rows"], 0)
            self.assertEqual(stats["verification_status"], VERIFY_COMPLETE)
            self.assertTrue(stats["verified_postgresql"])
        finally:
            with db.connect() as connection:
                connection.execute("DROP TABLE IF EXISTS cost_budgets")


if __name__ == "__main__":
    unittest.main()
