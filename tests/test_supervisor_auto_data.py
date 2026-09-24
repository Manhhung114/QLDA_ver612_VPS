from __future__ import annotations

import sqlite3
import unittest
from datetime import datetime

from qlda.autonomy.supervisor import ProjectSupervisor
from qlda.autonomy.supervisor_data_collector import collect_supervisor_data


class _DB:
    def __init__(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row

    def connect(self):
        return self.connection


class SupervisorAutoDataTests(unittest.TestCase):
    def setUp(self):
        self.db = _DB()
        c = self.db.connection
        c.executescript(
            """
            CREATE TABLE documents(
                id INTEGER PRIMARY KEY,
                project_id INTEGER,
                doc_type TEXT,
                code TEXT,
                status TEXT,
                due_date TEXT
            );
            CREATE TABLE contractor_data_sources(
                source_id TEXT PRIMARY KEY,
                workspace_project_id INTEGER,
                name TEXT,
                last_sync TEXT,
                last_error TEXT,
                enabled INTEGER
            );
            CREATE TABLE contractor_data_records(
                record_key TEXT PRIMARY KEY,
                workspace_project_id INTEGER,
                source_id TEXT,
                record_type TEXT
            );
            CREATE TABLE contractor_data_snapshots(
                snapshot_id TEXT PRIMARY KEY,
                workspace_project_id INTEGER,
                source_id TEXT,
                captured_at TEXT
            );
            """
        )
        c.executemany(
            "INSERT INTO documents(project_id,doc_type,code,status,due_date) VALUES(?,?,?,?,?)",
            [
                (101, "NCR", "NCR-001", "Đang xử lý", "2026-09-20"),
                (101, "RFI", "RFI-003", "Chờ phản hồi", "2026-09-21"),
                (101, "BBHT", "BBHT-009", "Từ chối", "2026-09-26"),
                (101, "NCR", "NCR-CLOSED", "Đóng", "2026-09-19"),
                (102, "NCR", "OTHER-TENANT", "Đang xử lý", "2026-09-10"),
            ],
        )
        c.execute(
            "INSERT INTO contractor_data_sources(source_id,workspace_project_id,name,last_sync,last_error,enabled) VALUES(?,?,?,?,?,1)",
            ("prod-1", 101, "Sản lượng SIGMA", "2026-09-24 11:30:00", ""),
        )
        c.executemany(
            "INSERT INTO contractor_data_records(record_key,workspace_project_id,source_id,record_type) VALUES(?,?,?,?)",
            [
                ("r1", 101, "prod-1", "PRODUCTION"),
                ("r2", 101, "prod-1", "PRODUCTION"),
            ],
        )
        c.execute(
            "INSERT INTO contractor_data_snapshots(snapshot_id,workspace_project_id,source_id,captured_at) VALUES(?,?,?,?)",
            ("s1", 101, "prod-1", "2026-09-22 10:00:00"),
        )
        c.commit()

    def test_collects_document_and_production_signals_from_workspace(self):
        status = {
            "schedule": {
                "planned_progress": 70,
                "actual_progress": 50,
                "delay_percent": 20,
                "delayed_tasks": 3,
            }
        }
        collected = collect_supervisor_data(
            self.db,
            101,
            status=status,
            now=datetime(2026, 9, 24, 12, 0, 0),
        )
        indicators = collected["indicators"]

        self.assertEqual(indicators["ncr_overdue"], 1)
        self.assertEqual(indicators["rfi_overdue"], 1)
        self.assertEqual(indicators["inspection_rejected"], 1)
        self.assertEqual(indicators["production_source_count"], 1)
        self.assertEqual(indicators["production_point_count"], 2)
        self.assertTrue(indicators["production_monitor_ready"])
        self.assertTrue(indicators["production_expected_to_move"])
        self.assertFalse(indicators["production_changed_24h"])
        self.assertEqual(
            collected["evidence"]["documents"]["ncr_overdue"][0]["ref"],
            "NCR-001",
        )

    def test_supervisor_uses_auto_signals_without_payment_alarm(self):
        collected = collect_supervisor_data(
            self.db,
            101,
            status={
                "schedule": {
                    "planned_progress": 70,
                    "actual_progress": 50,
                    "delay_percent": 20,
                    "delayed_tasks": 3,
                }
            },
            now=datetime(2026, 9, 24, 12, 0, 0),
        )
        indicators = {
            "data_integrity_score": 100,
            "schedule_delay_percent": 20,
            "contract_days_remaining": 90,
            "payment_overdue_value": 2_320_542_260_000,
            **collected["indicators"],
        }
        report = ProjectSupervisor().evaluate(101, indicators)
        codes = {item.code for item in report.findings}

        self.assertIn("SCHEDULE_DELAY", codes)
        self.assertIn("PRODUCTION_STALLED", codes)
        self.assertIn("NCR_OVERDUE", codes)
        self.assertIn("RFI_OVERDUE", codes)
        self.assertIn("INSPECTION_REJECTED", codes)
        self.assertNotIn("PAYMENT_OVERDUE", codes)


if __name__ == "__main__":
    unittest.main()
