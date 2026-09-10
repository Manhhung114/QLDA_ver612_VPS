from __future__ import annotations

import sqlite3
import unittest

from claim_component_fullscan_v622 import fullscan_claim_components
from ipc_claim_v622 import _encode_result


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
                updated_at TEXT,
                contract_value REAL DEFAULT 0,
                certified_cumulative REAL DEFAULT 0
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
            CREATE TABLE payment_claim_workbooks(
                claim_id TEXT PRIMARY KEY,
                payload TEXT NOT NULL
            );
            """
        )
        self.db.executemany(
            "INSERT INTO payment_claims(claim_id,project_id,claim_no,claim_code,filename,updated_at,contract_value,certified_cumulative) VALUES(?,?,?,?,?,?,?,?)",
            [
                ("c10", 1, "10", "IPC-10", "IPC10.xlsx", "2026-09-10", 2000.0, 1325.0),
                ("c09", 1, "09", "IPC-09", "IPC09.xlsx", "2026-09-09", 100.0, 30.0),
                ("c08", 1, "08", "IPC-08", "IPC08.xlsx", "2026-09-08", 511_000_000_000.0, 300_000_000_000.0),
            ],
        )
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
                # Deliberately absurd row formula if 100 were blindly treated as quantity:
                # 100 * 7.68762361629b = 768.762b > 511b contract.
                ("c08", 301, "Legacy/ambiguous row", "%", 1_000_000_000.0, 7_687_623_616.29,
                 0, 0, 150, 0, 0, 100,
                 0, 0, 150_000_000_000.0, 0, 0, 250_000_000_000.0,
                 0, 0, 100_000_000_000.0),
            ],
        )

        payloads = {
            "c10": {
                "summary": {
                    "contract_value": 2000.0,
                    "cumulative_acceptance": 1325.0,
                    "cumulative_material_acceptance": 850.0,
                    "cumulative_installation_acceptance": 1425.0,
                    "cumulative_material_deduction": 950.0,
                },
                "detail_items": [{"installation_measure": "quantity"}],
            },
            "c09": {
                "summary": {
                    "contract_value": 100.0,
                    "cumulative_acceptance": 30.0,
                    "cumulative_material_acceptance": 20.0,
                    "cumulative_installation_acceptance": 30.0,
                    "cumulative_material_deduction": 20.0,
                },
                "detail_items": [{"installation_measure": "quantity"}],
            },
            "c08": {
                "summary": {
                    "contract_value": 511_000_000_000.0,
                    "cumulative_acceptance": 300_000_000_000.0,
                    "cumulative_material_acceptance": 150_000_000_000.0,
                    "cumulative_installation_acceptance": 250_000_000_000.0,
                    "cumulative_material_deduction": 100_000_000_000.0,
                },
                # No quantity marker = legacy/unknown semantics.
                "detail_items": [{}],
            },
        }
        self.db.executemany(
            "INSERT INTO payment_claim_workbooks(claim_id,payload) VALUES(?,?)",
            [(cid, _encode_result(payload)) for cid, payload in payloads.items()],
        )
        self.db.commit()

    def tearDown(self):
        self.db.close()

    def test_ipc10_uses_validated_payment_summary_identity(self):
        stats = fullscan_claim_components(self.db, 1, "Tính chi phí nhân công lũy kế IPC-10")
        self.assertEqual(len(stats), 1)
        claim = stats[0]
        self.assertEqual(claim["claim_code"], "IPC-10")
        self.assertEqual(claim["scanned_rows"], 2)
        self.assertEqual(claim["installation_measure"], "quantity")
        self.assertTrue(claim["summary_identity_ok"])
        self.assertEqual(claim["selected_source"], "payment_summary_identity")
        self.assertAlmostEqual(claim["selected_labor_cumulative"], 475.0)
        self.assertAlmostEqual(claim["selected_material_cumulative"], 850.0)
        self.assertAlmostEqual(claim["detail_quantity_labor_cumulative"], 475.0)

    def test_legacy_pct_is_not_blindly_multiplied_as_quantity(self):
        stats = fullscan_claim_components(self.db, 1, "Nhân công lũy kế IPC-08")
        claim = stats[0]
        self.assertEqual(claim["installation_measure"], "legacy_or_unknown")
        self.assertEqual(claim["detail_quantity_labor_cumulative"], 0.0)
        self.assertTrue(claim["summary_identity_ok"])
        self.assertEqual(claim["selected_source"], "payment_summary_identity")
        self.assertAlmostEqual(claim["selected_labor_cumulative"], 150_000_000_000.0)
        self.assertLessEqual(claim["selected_labor_cumulative"], 511_000_000_000.0)

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
        self.assertEqual(set(by_code), {"IPC-08", "IPC-09", "IPC-10"})
        self.assertAlmostEqual(by_code["IPC-09"]["selected_labor_cumulative"], 10.0)
        self.assertAlmostEqual(by_code["IPC-10"]["selected_labor_cumulative"], 475.0)
        self.assertNotEqual(
            by_code["IPC-10"]["selected_labor_cumulative"],
            by_code["IPC-09"]["selected_labor_cumulative"] + by_code["IPC-10"]["selected_labor_cumulative"],
        )


if __name__ == "__main__":
    unittest.main()
