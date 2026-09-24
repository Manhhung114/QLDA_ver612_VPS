from __future__ import annotations

import sqlite3
import unittest
from contextlib import contextmanager

from qlda.runtime_core.contractor_access_control import set_ai_workspace_scope
from qlda.runtime_core.contractor_data_shared_ai import build_contractor_data_hub_appendix


class _Builder:
    def __init__(self, connection):
        self.connection = connection

    @contextmanager
    def connect(self):
        yield self.connection

    @staticmethod
    def table_exists(connection, table: str) -> bool:
        row = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone()
        return bool(row)


class ContractorDataSharedAITests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.execute(
            """CREATE TABLE contractor_data_spaces(
                master_project_id INTEGER, workspace_project_id INTEGER,
                contractor_code TEXT, contractor_name TEXT
            )"""
        )
        self.connection.execute(
            """CREATE TABLE contractor_data_records(
                master_project_id INTEGER, workspace_project_id INTEGER, source_id TEXT,
                source_name TEXT, category TEXT, worksheet TEXT, record_type TEXT,
                record_ref TEXT, work_item TEXT, zone TEXT, progress_percent REAL,
                content TEXT, source_row INTEGER, synced_at TEXT
            )"""
        )
        self.connection.execute(
            "INSERT INTO contractor_data_spaces VALUES(1,101,'NT-01','SIGMA')"
        )
        self.connection.execute(
            "INSERT INTO contractor_data_spaces VALUES(1,102,'NT-02','REE')"
        )
        self.connection.execute(
            """INSERT INTO contractor_data_records VALUES(
                1,101,'src-1','Theo doi san luong','PRODUCTION','S2 (update)','PRODUCTION',
                'r1','Lap dat ong','T1',75.0,'Cong tac=Lap dat ong | T1=75%',10,'2026-09-24 10:00:00'
            )"""
        )
        self.connection.execute(
            """INSERT INTO contractor_data_records VALUES(
                1,102,'src-2','Theo doi san luong','PRODUCTION','Ham','PRODUCTION',
                'r2','Keo day','Zone 1',40.0,'Cong tac=Keo day | Zone 1=40%',12,'2026-09-24 10:05:00'
            )"""
        )
        self.connection.commit()
        self.builder = _Builder(self.connection)

    def tearDown(self):
        set_ai_workspace_scope(None)
        self.connection.close()

    def test_shared_assistant_sees_data_hub_summary_and_matching_rows(self):
        text = build_contractor_data_hub_appendix(self.builder, 1, "S2 T1 lap dat ong")
        self.assertIn("KHO DỮ LIỆU NHÀ THẦU", text)
        self.assertIn("NT-01 - SIGMA", text)
        self.assertIn("S2 (update)", text)
        self.assertIn("T1", text)
        self.assertIn("75.0%", text)

    def test_contractor_scope_does_not_include_other_workspace(self):
        set_ai_workspace_scope(101)
        text = build_contractor_data_hub_appendix(self.builder, 1, "san luong")
        self.assertIn("NT-01 - SIGMA", text)
        self.assertNotIn("NT-02 - REE", text)
        self.assertNotIn("Zone 1=40", text)


if __name__ == "__main__":
    unittest.main()
