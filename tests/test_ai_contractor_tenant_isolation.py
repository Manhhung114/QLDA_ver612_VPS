from __future__ import annotations

import unittest

from qlda.autonomy.qlda_adapters import QLDAAutomationAdapters
from qlda.modules.contractor_data.worker import project_ids_for_supervisor
from qlda.runtime_core.ai_supervisor_navigation import (
    AI_NAV_LABEL,
    HOME_NAV_LABEL,
    _inject_ai_nav_option,
    _is_main_navigation,
)


class _Row(dict):
    def keys(self):
        return super().keys()


class _Connection:
    def __init__(self, *, contractor_rows=None, project_rows=None):
        self.contractor_rows = list(contractor_rows or [])
        self.project_rows = list(project_rows or [])

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=()):
        text = " ".join(str(sql).split()).lower()
        if "from project_contractors where workspace_project_id=?" in text:
            wid = int(params[0])
            rows = [x for x in self.contractor_rows if int(x.get("workspace_project_id") or 0) == wid]
            return _Result(rows[:1])
        if "from project_contractors" in text and "master_project_id=?" in text and "limit 2" in text:
            mid = int(params[0])
            rows = [x for x in self.contractor_rows if int(x.get("master_project_id") or 0) == mid and x.get("status") == "Đang hoạt động"]
            return _Result(rows[:2])
        if "select workspace_project_id as id" in text:
            active = [
                _Row(id=int(x["workspace_project_id"]))
                for x in self.contractor_rows
                if x.get("status") == "Đang hoạt động"
            ]
            master_ids = {int(x.get("master_project_id") or 0) for x in self.contractor_rows}
            standalone = [
                _Row(id=int(x["id"])) for x in self.project_rows
                if int(x["id"]) not in master_ids
            ]
            return _Result(active + standalone)
        if "select id from projects" in text:
            return _Result([_Row(id=int(x["id"])) for x in self.project_rows])
        return _Result([])


class _Result:
    def __init__(self, rows):
        self.rows = list(rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)


class _DB:
    def __init__(self, contractor_rows=None, project_rows=None):
        self.contractor_rows = contractor_rows or []
        self.project_rows = project_rows or []

    def connect(self):
        return _Connection(
            contractor_rows=self.contractor_rows,
            project_rows=self.project_rows,
        )


class ContractorAITenantIsolationTests(unittest.TestCase):
    def setUp(self):
        self.contractors = [
            _Row(
                id=11,
                master_project_id=1,
                workspace_project_id=101,
                contractor_code="NT-01",
                contractor_name="Alpha",
                status="Đang hoạt động",
            ),
            _Row(
                id=12,
                master_project_id=1,
                workspace_project_id=102,
                contractor_code="NT-02",
                contractor_name="Beta",
                status="Đang hoạt động",
            ),
        ]

    def test_workspace_resolves_to_one_contractor_tenant(self):
        adapters = QLDAAutomationAdapters(_DB(self.contractors))
        scope = adapters._resolve_scope(102)
        self.assertEqual(scope["master_project_id"], 1)
        self.assertEqual(scope["workspace_project_id"], 102)
        self.assertEqual(scope["contractor_id"], 12)
        self.assertEqual(scope["contractor_code"], "NT-02")
        self.assertFalse(scope["standalone"])

    def test_master_project_cannot_be_used_as_implicit_contractor_ai(self):
        adapters = QLDAAutomationAdapters(_DB(self.contractors))
        with self.assertRaisesRegex(ValueError, "không được chạy ở phạm vi dự án tổng"):
            adapters._resolve_scope(1)

    def test_background_supervisor_fans_out_to_each_workspace(self):
        db = _DB(self.contractors, project_rows=[_Row(id=1), _Row(id=101), _Row(id=102), _Row(id=200)])
        ids = project_ids_for_supervisor(db)
        self.assertIn(101, ids)
        self.assertIn(102, ids)
        self.assertIn(200, ids)
        self.assertNotIn(1, ids)

    def test_ai_supervisor_is_its_own_main_navigation_item(self):
        original = [
            HOME_NAV_LABEL,
            "📋 Công việc",
            "🏗️ Thi công",
            "📁 Hồ sơ",
            "💰 Tài chính",
            "📚 Công cụ",
        ]
        options = _inject_ai_nav_option(original)
        self.assertTrue(_is_main_navigation("Nhóm chức năng", original))
        self.assertEqual(options.count(AI_NAV_LABEL), 1)
        self.assertEqual(options.index(AI_NAV_LABEL), options.index(HOME_NAV_LABEL) + 1)
        self.assertEqual(original[0], HOME_NAV_LABEL)
        self.assertNotIn(AI_NAV_LABEL, original)


if __name__ == "__main__":
    unittest.main()
