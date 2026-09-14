from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from pathlib import Path

from qlda.runtime_core.project_store import CloudDatabase
from tests.test_vo_claim import _workbook_bytes
from qlda.import_engines.vo_background import parse_vo_path
from qlda.runtime_core.vo_claim import list_vos, update_vo_finance, vo_items, vo_revisions
from qlda.import_engines.vo_persistence import VERIFY_COMPLETE, save_vo_result_batched


def _bulk_result(line_count: int = 2_003) -> dict:
    items = []
    for index in range(1, line_count + 1):
        items.append(
            {
                "sheet_name": "MEP VO",
                "row_no": index + 3,
                "seq": str(index),
                "description": f"Phát sinh VO {index}",
                "unit": "m",
                "contract_qty": 0.0,
                "actual_qty": 1.0,
                "increase_qty": 1.0,
                "decrease_qty": 0.0,
                "variation_qty": 1.0,
                "spec": "",
                "item_code": "",
                "brand": "",
                "origin": "",
                "material_unit_price": 80.0,
                "labor_unit_price": 20.0,
                "unit_price_total": 100.0,
                "variation_amount": 100.0,
                "variation_kind": "Tăng",
                "note": "",
            }
        )
    total = float(line_count * 100)
    return {
        "schema": "qlda_vo_excel_v2",
        "filename": "VO-88.xlsx",
        "batch_id": "vo88-2003-row-proof",
        "metadata": {"revision_label": "R0", "project": "Dự án", "package": "MEP"},
        "vo_no": 88,
        "vo_code": "VO-88",
        "summary": {
            "subtotal_before_vat": total,
            "vat_amount": 0,
            "total_after_vat": total,
            "increase_amount": total,
            "decrease_amount": 0,
        },
        "detail_items": items,
        "detail_line_count": line_count,
    }


class VOBackgroundPathTests(unittest.TestCase):
    def test_stream_parser_matches_business_values_and_preserves_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "2025.09.29 VO-03.xlsx"
            original = _workbook_bytes()
            path.write_bytes(original)
            before = hashlib.sha256(path.read_bytes()).hexdigest()

            result = parse_vo_path(path, path.name)

            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)
            self.assertEqual(result["source_sha256"], before)
            self.assertEqual(result["batch_id"], before)
            self.assertEqual(result["vo_code"], "VO-03")
            self.assertEqual(result["detail_line_count"], 2)
            self.assertEqual(result["summary"]["total_after_vat"], -110)
            self.assertEqual(result["summary"]["increase_amount"], 200)
            self.assertEqual(result["summary"]["decrease_amount"], -300)
            self.assertEqual(result["summary"]["detail_discrepancy"], 0)
            self.assertEqual(result["source_file_size"], len(original))


class VOBackgroundPersistenceTests(unittest.TestCase):
    def _database(self, directory: str) -> tuple[CloudDatabase, int]:
        db = CloudDatabase(Path(directory) / "qlda.db")
        return db, db.add_project("P-V6244", "Kiểm tra VO V6.24.4")

    def _path_result(self, directory: str, filename: str = "2025.09.29 VO-03.xlsx") -> dict:
        path = Path(directory) / filename
        path.write_bytes(_workbook_bytes())
        return parse_vo_path(path, path.name)

    def test_counts_idempotency_and_independent_vo_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            db, project_id = self._database(tmp)
            result = self._path_result(tmp)
            first = save_vo_result_batched(db, project_id, result, batch_size=50)
            self.assertEqual(first["expected_rows"], 2)
            self.assertEqual(first["scanned_rows"], 2)
            self.assertEqual(first["inserted_rows"], 2)
            self.assertEqual(first["written_rows"], 2)
            self.assertEqual(first["failed_rows"], 0)
            self.assertEqual(first["verification_status"], VERIFY_COMPLETE)
            self.assertTrue(first["verified_postgresql"])
            self.assertEqual(first["revision_no"], 0)

            same = save_vo_result_batched(db, project_id, result, batch_size=50)
            self.assertEqual(same["vo_id"], first["vo_id"])
            self.assertEqual(same["revision_no"], 0)
            self.assertEqual(len(vo_revisions(db, first["vo_id"])), 1)

            other = save_vo_result_batched(
                db,
                project_id,
                self._path_result(tmp, "2025.09.29 VO-04.xlsx"),
                batch_size=50,
            )
            self.assertNotEqual(other["vo_id"], first["vo_id"])
            self.assertEqual(len(list_vos(db, project_id)), 2)

    def test_revision_preserves_approved_business_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            db, project_id = self._database(tmp)
            first_result = self._path_result(tmp)
            first = save_vo_result_batched(db, project_id, first_result, batch_size=50)
            update_vo_finance(
                db,
                first["vo_id"],
                approved_amount=-95,
                status="Đã duyệt",
                funding_source="Giảm giá trị hợp đồng",
                vo_date="2025-09-29",
                note="Giữ nguyên qua revision",
            )
            revised_result = dict(first_result)
            revised_result["batch_id"] = "revision-vo03-r1"
            revised_result["metadata"] = {**first_result["metadata"], "revision_label": "R1"}
            revised = save_vo_result_batched(db, project_id, revised_result, batch_size=50)
            order = next(row for row in list_vos(db, project_id) if row["vo_id"] == first["vo_id"])

            self.assertEqual(revised["revision_no"], 1)
            self.assertEqual(float(order["approved_amount"]), -95)
            self.assertEqual(order["status"], "Đã duyệt")
            self.assertEqual(order["funding_source"], "Giảm giá trị hợp đồng")

    def test_mismatch_rolls_back_and_keeps_previous_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            db, project_id = self._database(tmp)
            first_result = self._path_result(tmp)
            first = save_vo_result_batched(db, project_id, first_result, batch_size=50)
            with db.connect() as connection:
                connection.execute(
                    "CREATE TRIGGER skip_vo_row_v6244 BEFORE INSERT ON variation_order_items "
                    "WHEN NEW.description='Hạng mục giảm' BEGIN SELECT RAISE(IGNORE); END"
                )
            revised_result = dict(first_result)
            revised_result["batch_id"] = "revision-should-rollback"
            with self.assertRaisesRegex(Exception, "CHƯA ĐỦ"):
                save_vo_result_batched(db, project_id, revised_result, batch_size=50)

            order = next(row for row in list_vos(db, project_id) if row["vo_id"] == first["vo_id"])
            self.assertEqual(order["batch_id"], first["batch_id"])
            self.assertEqual(int(order["latest_revision"]), 0)
            self.assertEqual(len(vo_items(db, first["vo_id"])), 2)
            self.assertEqual(len(vo_revisions(db, first["vo_id"])), 1)

    def test_batches_and_verifies_2003_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            db, project_id = self._database(tmp)
            stats = save_vo_result_batched(db, project_id, _bulk_result(), batch_size=500)
            self.assertEqual(stats["expected_rows"], 2_003)
            self.assertEqual(stats["inserted_rows"], 2_003)
            self.assertEqual(stats["written_rows"], 2_003)
            self.assertEqual(stats["failed_rows"], 0)
            self.assertEqual(stats["verification_status"], VERIFY_COMPLETE)


@unittest.skipUnless(os.environ.get("DATABASE_URL"), "DATABASE_URL chưa được cấu hình")
class VOBackgroundPostgreSQLTests(unittest.TestCase):
    def test_confirms_all_vo_rows_in_real_postgresql(self):
        from qlda.runtime_core.project_database import _PGConnectionContext

        class PostgreSQLTestDatabase:
            def __init__(self, url: str):
                self.url = url

            def connect(self):
                return _PGConnectionContext(self.url)

        db = PostgreSQLTestDatabase(str(os.environ["DATABASE_URL"]))
        tables = (
            "variation_order_items",
            "variation_order_revisions",
            "variation_order_workbooks",
            "variation_orders",
            "cost_variations",
        )
        with db.connect() as connection:
            for table in tables:
                connection.execute(f"DROP TABLE IF EXISTS {table}")
            connection.execute(
                "CREATE TABLE cost_variations("
                "id BIGSERIAL PRIMARY KEY,project_id BIGINT NOT NULL,vo_code TEXT NOT NULL,"
                "task_ref TEXT DEFAULT '',description TEXT NOT NULL,proposed_amount REAL DEFAULT 0,"
                "approved_amount REAL DEFAULT 0,funding_source TEXT DEFAULT '',status TEXT DEFAULT '',"
                "vo_date TEXT DEFAULT '',note TEXT DEFAULT '',created_at TEXT DEFAULT '',updated_at TEXT DEFAULT '',"
                "UNIQUE(project_id,vo_code))"
            )
        try:
            stats = save_vo_result_batched(db, 6244, _bulk_result(), batch_size=500)
            with db.connect() as connection:
                row = connection.execute(
                    "SELECT COUNT(*) AS row_count FROM variation_order_items WHERE vo_id=?",
                    (stats["vo_id"],),
                ).fetchone()
            self.assertEqual(int(row["row_count"]), 2_003)
            self.assertEqual(stats["expected_rows"], 2_003)
            self.assertEqual(stats["written_rows"], 2_003)
            self.assertEqual(stats["failed_rows"], 0)
            self.assertEqual(stats["verification_status"], VERIFY_COMPLETE)
            self.assertTrue(stats["verified_postgresql"])
        finally:
            with db.connect() as connection:
                for table in tables:
                    connection.execute(f"DROP TABLE IF EXISTS {table}")


if __name__ == "__main__":
    unittest.main()
