from __future__ import annotations

import unittest
from unittest.mock import patch

import qlda.infrastructure.ai.project_chat as project_chat


class _Result:
    def __init__(self, rows):
        self.rows = list(rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)


class _ScopeConnection:
    def __init__(self):
        self.contractors = [
            {
                "master_project_id": 1,
                "workspace_project_id": 101,
                "contractor_code": "NT-01",
                "contractor_name": "Alpha",
                "status": "Đang hoạt động",
            },
            {
                "master_project_id": 1,
                "workspace_project_id": 102,
                "contractor_code": "NT-02",
                "contractor_name": "Beta",
                "status": "Đang hoạt động",
            },
        ]

    def execute(self, sql, params=()):
        text = " ".join(str(sql).split()).lower()
        if "select master_project_id from project_contractors" in text:
            selected = int(params[0])
            rows = [
                {"master_project_id": row["master_project_id"]}
                for row in self.contractors
                if int(row["workspace_project_id"]) == selected
            ]
            return _Result(rows[:1])
        if "from project_contractors where master_project_id=" in text:
            master = int(params[0])
            return _Result(
                [row for row in self.contractors if int(row["master_project_id"]) == master]
            )
        return _Result([])


class _TaskConnection:
    def __init__(self):
        self.rows = [
            {
                "id": 11,
                "project_id": 101,
                "wbs": "S4-MEP-01",
                "name": "Lắp đặt ống cấp nước tháp S4",
                "responsible": "Đội MEP",
                "start_date": "2026-09-01",
                "end_date": "2026-09-30",
                "duration": 30,
                "planned_progress": 70,
                "actual_progress": 55,
                "actual_override": None,
                "actual_update_date": "2026-09-25",
                "actual_finish_date": "",
                "status": "Chậm tiến độ",
                "predecessor": "",
                "note": "Khu vực tầng 12-20",
                "critical": 1,
                "total_slack": 0,
                "resource_names": "MEP Team",
                "source_type": "mpp",
            },
            {
                "id": 12,
                "project_id": 101,
                "wbs": "S2-MEP-01",
                "name": "Lắp đặt ống cấp nước tháp S2",
                "responsible": "Đội MEP",
                "start_date": "2026-09-01",
                "end_date": "2026-09-30",
                "duration": 30,
                "planned_progress": 80,
                "actual_progress": 80,
                "actual_override": None,
                "actual_update_date": "2026-09-25",
                "actual_finish_date": "",
                "status": "Đúng tiến độ",
                "predecessor": "",
                "note": "",
                "critical": 0,
                "total_slack": 2,
                "resource_names": "MEP Team",
                "source_type": "mpp",
            },
        ]

    def execute(self, sql, params=()):
        del sql, params
        return _Result(self.rows)


class _LegacyProductionConnection:
    def __init__(self):
        self.rows = [
            {
                "project_id": 1,
                "source_id": "sheet-1",
                "worksheet": "S4 (update)",
                "work_item": "Lắp đặt ống cấp nước",
                "zone": "Tầng 12-20",
                "progress_percent": 62.5,
                "source_row": 88,
                "synced_at": "2026-09-26 06:50:00",
                "source_name": "Theo dõi sản lượng SIGMA",
                "spreadsheet_title": "Sản lượng MEP E3",
            },
            {
                "project_id": 1,
                "source_id": "sheet-1",
                "worksheet": "S2 (update)",
                "work_item": "Lắp đặt ống cấp nước",
                "zone": "Tầng 10-18",
                "progress_percent": 71.0,
                "source_row": 77,
                "synced_at": "2026-09-26 06:50:00",
                "source_name": "Theo dõi sản lượng SIGMA",
                "spreadsheet_title": "Sản lượng MEP E3",
            },
        ]

    def execute(self, sql, params=()):
        text = " ".join(str(sql).split()).lower()
        if "from contractor_data_records" in text and "count(*) as records" in text:
            return _Result([
                {
                    "records": 0,
                    "sources": 0,
                    "worksheets": 0,
                    "production_points": 0,
                    "avg_progress": None,
                    "last_sync": None,
                }
            ])
        if "from production_progress_current c" in text:
            allowed = set(int(x) for x in (params[0] if params else []))
            return _Result([row for row in self.rows if int(row["project_id"]) in allowed])
        return _Result([])


class AIProjectChatScopeTests(unittest.TestCase):
    def setUp(self):
        self.connection = _ScopeConnection()

    @patch("qlda.infrastructure.ai.project_chat._table_exists", return_value=True)
    @patch("qlda.infrastructure.ai.project_chat._scope_has_live_data")
    def test_management_empty_selected_workspace_recovers_authorized_project_scope(
        self, has_data, _table_exists
    ):
        def probe(_connection, _master, ids):
            return list(ids) != [102]

        has_data.side_effect = probe
        master, ids, labels, project_wide = project_chat._resolve_scope(
            self.connection,
            project_id=1,
            workspace_scope=102,
            question="Đánh giá sản lượng lắp đặt tháp S4",
            allow_project_wide=True,
        )

        self.assertEqual(master, 1)
        self.assertEqual(ids[0], 1)
        self.assertEqual(set(ids), {1, 101, 102})
        self.assertTrue(project_wide)
        self.assertEqual(labels[102], "NT-02 - Beta")

    @patch("qlda.infrastructure.ai.project_chat._table_exists", return_value=True)
    @patch("qlda.infrastructure.ai.project_chat._scope_has_live_data")
    def test_contractor_scope_never_widens_implicitly(self, has_data, _table_exists):
        master, ids, _labels, project_wide = project_chat._resolve_scope(
            self.connection,
            project_id=1,
            workspace_scope=102,
            question="Đánh giá sản lượng lắp đặt tháp S4",
            allow_project_wide=False,
        )

        self.assertEqual(master, 1)
        self.assertEqual(ids, [102])
        self.assertFalse(project_wide)
        has_data.assert_not_called()

    @patch("qlda.infrastructure.ai.project_chat._table_exists", return_value=True)
    def test_explicit_project_wide_scope_always_includes_master_project(self, _table_exists):
        master, ids, _labels, project_wide = project_chat._resolve_scope(
            self.connection,
            project_id=1,
            workspace_scope=102,
            question="Tổng quan toàn bộ dự án và tất cả nhà thầu",
            allow_project_wide=True,
        )

        self.assertEqual(master, 1)
        self.assertEqual(ids[0], 1)
        self.assertEqual(set(ids), {1, 101, 102})
        self.assertTrue(project_wide)

    @patch("qlda.infrastructure.ai.project_chat._table_exists", return_value=True)
    def test_task_context_ranks_s4_evidence_ahead_of_related_tower_rows(self, _table_exists):
        lines = project_chat._task_detail_context(
            _TaskConnection(),
            [101],
            {101: "NT-01 - Alpha"},
            "Đánh giá sản lượng lắp đặt tháp S4",
            2,
        )
        context = "\n".join(lines)

        self.assertIn("[TASK:11]", context)
        self.assertIn("tháp S4", context)
        self.assertIn("KH=70.0%", context)
        self.assertIn("TT=55.0%", context)
        self.assertLess(context.index("[TASK:11]"), context.index("[TASK:12]"))

    @patch("qlda.infrastructure.ai.project_chat._table_exists")
    def test_empty_normalized_data_hub_falls_back_to_live_production_rows(self, table_exists):
        table_exists.side_effect = lambda _conn, table: table in {
            "contractor_data_records",
            "production_progress_current",
            "production_sheet_sources",
        }
        lines = project_chat._data_hub_context(
            _LegacyProductionConnection(),
            master=1,
            workspace_ids=[1],
            labels={1: "NT-01 - SIGMA"},
            question="Đánh giá sản lượng lắp đặt tháp S4",
        )
        context = "\n".join(lines)

        self.assertIn("[DATA-HUB-COMPAT]", context)
        self.assertIn("records=2", context)
        self.assertIn("worksheet=S4 (update)", context)
        self.assertIn("tiến độ=62.5%", context)
        self.assertIn("NT-01 - SIGMA", context)
        self.assertLess(context.index("worksheet=S4 (update)"), context.index("worksheet=S2 (update)"))


if __name__ == "__main__":
    unittest.main()
