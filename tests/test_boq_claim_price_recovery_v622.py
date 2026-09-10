from __future__ import annotations

import sqlite3
import unittest

import boq_persistence_v622 as boq_persistence
import ipc_claim_v622 as ipc
from boq_claim_price_recovery_v622 import (
    PATCH_MARKER,
    _detect_split_price_cols,
    _recover_boq,
    _recover_claims,
    install_boq_claim_price_recovery,
)


def _row(row_no: int, values: dict[int, object], width: int = 30) -> list[object]:
    cells: list[object] = [None] * width
    for excel_col_1based, value in values.items():
        cells[excel_col_1based - 1] = value
    return [row_no] + cells


def _boq_snapshot(material_label: str = "Vật Tư") -> dict:
    return {
        "sheet": "E.1 THÁP S2",
        "columns": ["Dòng"] + [chr(65 + i) for i in range(12)],
        "rows": [
            _row(2, {1: "TT", 2: "Nội dung công việc", 3: "Khối lượng", 4: "Đơn vị", 9: "Đơn giá", 11: "Thành tiền"}, 12),
            _row(3, {9: material_label, 10: "Nhân công"}, 12),
            _row(6, {1: 1, 2: "Tủ điện TĐ.S4-QH2", 3: 1, 4: "Set", 9: 52_573_400, 10: 1_375_000, 11: 53_948_400}, 12),
        ],
    }


def _claim_snapshot() -> dict:
    return {
        "sheet": "GTHT",
        "columns": ["Dòng"] + [f"C{i}" for i in range(30)],
        "rows": [
            _row(11, {1: "TT", 2: "Tên công tác / Diễn giải khối lượng", 3: "Khối lượng", 4: "Đơn vị", 9: "Hợp đồng", 12: "Khối lượng nghiệm thu", 18: "Giá trị nghiệm thu"}),
            _row(12, {9: "Đơn giá (VNĐ)", 11: "Thành tiền", 12: "Vật tư", 15: "Lắp đặt", 18: "Vật tư", 21: "Lắp đặt"}),
            _row(13, {9: "Vật Tư", 10: "Nhân công", 12: "Kỳ trước", 13: "Kỳ này", 14: "Lũy kế", 15: "Kỳ trước", 16: "Kỳ này", 17: "Lũy kế"}),
            _row(19, {1: 1, 2: "Tủ điện TĐ.S4-QH2", 3: 1, 4: "Set", 9: 52_573_400, 10: 1_375_000, 11: 53_948_400}),
        ],
    }


class PriceRecoveryTests(unittest.TestCase):
    def setUp(self):
        install_boq_claim_price_recovery()

    def test_detects_real_boq_grouped_prices_including_vat_lieu(self):
        for label in ("Vật Tư", "Vật liệu"):
            mapping = _detect_split_price_cols(_boq_snapshot(label))
            self.assertEqual(mapping["material_unit_price"], 8)
            self.assertEqual(mapping["labor_unit_price"], 9)

    def test_detects_real_claim_three_row_price_header(self):
        mapping = _detect_split_price_cols(_claim_snapshot())
        self.assertEqual(mapping["material_unit_price"], 8)
        self.assertEqual(mapping["labor_unit_price"], 9)

        import ipc_adaptive_parser_v622 as adaptive
        grid = [tuple([None] * 30) for _ in range(10)]
        grid += [
            tuple(_claim_snapshot()["rows"][0][1:]),
            tuple(_claim_snapshot()["rows"][1][1:]),
            tuple(_claim_snapshot()["rows"][2][1:]),
        ]
        headers = adaptive._composite_headers(ipc, grid, 11, 30)
        self.assertIn("don gia", headers[9])
        self.assertIn("nhan cong", headers[9])
        self.assertEqual(adaptive._find_header_col(headers, ("hang muc cong viec",)), 1)
        self.assertEqual(adaptive._find_header_col(headers, ("dvt",)), 3)
        self.assertEqual(adaptive._find_header_col(headers, ("hop dong", "khoi luong")), 2)
        self.assertEqual(adaptive._find_header_col(headers, ("don gia lap dat",)), 9)

    def test_recovers_existing_boq_rows_from_saved_workbook_snapshot(self):
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.execute(
            """CREATE TABLE cost_budgets(
                   id INTEGER PRIMARY KEY, project_id INTEGER, quantity REAL, unit_price REAL,
                   note TEXT, material_unit_price REAL, labor_unit_price REAL,
                   material_cost REAL, labor_cost REAL)"""
        )
        connection.execute(
            "CREATE TABLE boq_excel_workbooks(project_id INTEGER PRIMARY KEY,batch_id TEXT,payload TEXT)"
        )
        note = "[QLDA_BOQ_EXCEL] file=boq.xlsx|sheet=E.1 THÁP S2|row=6|batch=abc"
        connection.execute(
            "INSERT INTO cost_budgets(id,project_id,quantity,unit_price,note) VALUES(1,1,2,53948400,?)",
            (note,),
        )
        payload = boq_persistence._encode_result({"workbook_sheets": {"E.1 THÁP S2": _boq_snapshot()}})
        connection.execute(
            "INSERT INTO boq_excel_workbooks(project_id,batch_id,payload) VALUES(1,'abc',?)",
            (payload,),
        )
        recovered = _recover_boq(connection, 1)
        self.assertEqual(recovered, 1)
        row = connection.execute(
            "SELECT material_unit_price,labor_unit_price,material_cost,labor_cost FROM cost_budgets WHERE id=1"
        ).fetchone()
        self.assertEqual(row["material_unit_price"], 52_573_400)
        self.assertEqual(row["labor_unit_price"], 1_375_000)
        self.assertEqual(row["material_cost"], 105_146_800)
        self.assertEqual(row["labor_cost"], 2_750_000)

    def test_recovers_existing_claim_prices_from_saved_gtht_snapshot(self):
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.execute(
            "CREATE TABLE payment_claims(claim_id TEXT PRIMARY KEY,project_id INTEGER,claim_no TEXT,claim_code TEXT)"
        )
        connection.execute(
            "CREATE TABLE payment_claim_workbooks(claim_id TEXT PRIMARY KEY,batch_id TEXT,payload TEXT)"
        )
        connection.execute(
            """CREATE TABLE payment_claim_items(
                   claim_id TEXT,row_no INTEGER,material_unit_price REAL DEFAULT 0,labor_unit_price REAL DEFAULT 0)"""
        )
        connection.execute("INSERT INTO payment_claims VALUES('c6',1,'6','IPC-06')")
        connection.execute("INSERT INTO payment_claim_items VALUES('c6',19,0,0)")
        payload = ipc._encode_result({"workbook_sheets": {"GTHT": _claim_snapshot()}})
        connection.execute("INSERT INTO payment_claim_workbooks VALUES('c6','ipc6',?)", (payload,))
        recovered = _recover_claims(connection, 1, "chi phí nhân công Claim 6")
        self.assertEqual(recovered, 1)
        row = connection.execute(
            "SELECT material_unit_price,labor_unit_price FROM payment_claim_items WHERE claim_id='c6' AND row_no=19"
        ).fetchone()
        self.assertEqual(row["material_unit_price"], 52_573_400)
        self.assertEqual(row["labor_unit_price"], 1_375_000)

    def test_marker(self):
        self.assertIn("PRICE RECOVERY", PATCH_MARKER)


if __name__ == "__main__":
    unittest.main()
