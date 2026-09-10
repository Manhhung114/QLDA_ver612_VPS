from __future__ import annotations

import sqlite3
import unittest

from claim_component_fullscan_v622 import fullscan_claim_components


class ClaimComponentFullscanTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        self.db.executescript(
            """
            CREATE TABLE payment_claims(
                claim_id TEXT PRIMARY KEY,
                project_id INTEGER NOT NULL,
                claim_no TEXT,
                claim_code TEXT,
                filename TEXT,
                updated_at TEXT
            );
            CREATE TABLE payment_claim_items(
                claim_id TEXT NOT NULL,
                row_no INTEGER NOT NULL,
                boq_item TEXT,
                unit TEXT,
                material_unit_price REAL DEFAULT 0,
                labor_unit_price REAL DEFAULT 0,
                material_previous_qty REAL DEFAULT 0,
                material_current_qty REAL DEFAULT 0,
                material_cumulative_qty REAL DEFAULT 0,
                installation_previous_pct REAL DEFAULT 0,
                installation_current_pct REAL DEFAULT 0,
                installation_cumulative_pct REAL DEFAULT 0,
                material_previous_value REAL DEFAULT 0,
                material_current_value REAL DEFAULT 0,
                material_cumulative_value REAL DEFAULT 0,
                installation_previous_value REAL DEFAULT 0,
                installation_current_value REAL DEFAULT 0,
                installation_cumulative_value REAL DEFAULT 0,
                deduction_previous REAL DEFAULT 0,
                deduction_current REAL DEFAULT 0,
                deduction_cumulative REAL DEFAULT 0,
                PRIMARY KEY(claim_id,row_no)
            );
            """
        )
        self.db.executemany(
            "INSERT INTO payment_claims(claim_id,project_id,claim_no,claim_code,filename,updated_at) VALUES(?,?,?,?,?,?)",
            [
                ("c10", 1, "10", "IPC-10", "IPC10.xlsx", "2026-09-10"),
                ("c09", 1, "09", "IPC-09", "IPC09.xlsx", "2026-09-09"),
            ],
        )
        # Gross installation values intentionally follow the real Claim template:
        # gross = installation quantity * (material unit price + labor unit price)
        # deduction = installation quantity * material unit price
        self.db.executemany(
            """INSERT INTO payment_claim_items(
                   claim_id,row_no,boq_item,unit,material_unit_price,labor_unit_price,
                   material_previous_qty,material_current_qty,material_cumulative_qty,
                   installation_previous_pct,installation_current_pct,installation_cumulative_pct,
                   material_previous_value,material_current_value,material_cumulative_value,
                   installation_previous_value,installation_current_value,installation_cumulative_value,
                   deduction_previous,deduction_current,deduction_cumulative
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            [
                ("c10", 101, "Tủ điện A", "bộ", 200.0, 100.0, 1, 2, 3, 2, 1, 3,
                 200, 400, 600, 600, 300, 900, 400, 200, 600),
                ("c10", 102, "Cáp điện B", "m", 50.0, 25.0, 4, 1, 5, 5, 2, 7,
                 200, 50, 250, 375, 150, 525, 250, 100, 350),
                ("c09", 201, "Thiết bị C", "bộ", 20.0, 10.0, 0, 1, 1, 0, 1, 1,
                 0, 20, 20, 0, 30, 30, 0, 20, 20),
            ],
        )
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_ipc10_labor_is_quantity_times_labor_unit_price_for_all_rows(self):
        stats = fullscan_claim_components(self.db, 1, "Tính chi phí nhân công lũy kế IPC-10")
        self.assertEqual(len(stats), 1)
        claim = stats[0]
        self.assertEqual(claim["claim_code"], "IPC-10")
        self.assertEqual(claim["scanned_rows"], 2)
        self.assertAlmostEqual(claim["labor_current_total"], 1 * 100 + 2 * 25)
        self.assertAlmostEqual(claim["labor_cumulative_total"], 3 * 100 + 7 * 25)
        self.assertAlmostEqual(claim["material_current_total"], 2 * 200 + 1 * 50)
        self.assertAlmostEqual(claim["material_cumulative_total"], 3 * 200 + 5 * 50)
        # Cross-check: gross installation - material deduction = pure labor.
        self.assertAlmostEqual(
            claim["net_installation_labor_cumulative_total"],
            claim["labor_cumulative_total"],
        )
        self.assertAlmostEqual(claim["labor_crosscheck_delta"], 0.0)
        self.assertEqual(claim["labor_crosscheck_mismatch_rows"], 0)

    def test_fullscan_never_overwrites_gross_installation_values(self):
        before = self.db.execute(
            "SELECT installation_current_value,installation_cumulative_value,deduction_current,deduction_cumulative "
            "FROM payment_claim_items WHERE claim_id='c10' AND row_no=102"
        ).fetchone()
        fullscan_claim_components(self.db, 1, "Chi phí nhân công IPC-10", persist=True)
        after = self.db.execute(
            "SELECT installation_current_value,installation_cumulative_value,deduction_current,deduction_cumulative "
            "FROM payment_claim_items WHERE claim_id='c10' AND row_no=102"
        ).fetchone()
        self.assertEqual(tuple(before), tuple(after))
        self.assertEqual(tuple(after), (150.0, 525.0, 100.0, 350.0))

    def test_all_claims_are_calculated_independently(self):
        stats = fullscan_claim_components(self.db, 1, "Tổng hợp chi phí nhân công các Claim")
        by_code = {row["claim_code"]: row for row in stats}
        self.assertEqual(set(by_code), {"IPC-09", "IPC-10"})
        self.assertAlmostEqual(by_code["IPC-09"]["labor_cumulative_total"], 10.0)
        self.assertAlmostEqual(by_code["IPC-10"]["labor_cumulative_total"], 475.0)
        # Cumulative values remain per Claim; never sum IPC-09 + IPC-10 as lũy kế IPC-10.
        self.assertNotEqual(
            by_code["IPC-10"]["labor_cumulative_total"],
            by_code["IPC-09"]["labor_cumulative_total"] + by_code["IPC-10"]["labor_cumulative_total"],
        )


if __name__ == "__main__":
    unittest.main()
