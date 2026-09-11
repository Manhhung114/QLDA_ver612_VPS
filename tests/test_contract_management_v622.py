from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from contract_management_v622 import (
    PATCH_MARKER,
    build_contract_ai_prompt,
    can_access_contract_management,
    can_edit_contract_management,
    contract_summary,
    create_contract_record,
    delete_contract_record,
    get_contract_record,
    list_contract_file_versions,
    list_contract_records,
    register_contract_file,
    update_contract_record,
)


class _DB:
    def __init__(self, path: str):
        self.path = path
        with self.connect() as c:
            c.executescript(
                """
                PRAGMA foreign_keys=ON;
                CREATE TABLE IF NOT EXISTS projects(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code TEXT DEFAULT '',
                    name TEXT DEFAULT ''
                );
                INSERT INTO projects(id,code,name) VALUES(1,'NT-A','Nhà thầu A');
                INSERT INTO projects(id,code,name) VALUES(2,'NT-B','Nhà thầu B');
                """
            )

    def connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def project(self, project_id: int):
        with self.connect() as c:
            row = c.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
            return dict(row) if row else None


class ContractManagementV622Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = _DB(str(Path(self.tmp.name) / "contract.sqlite"))
        self.admin = {"email": "admin@test.vn", "name": "Admin", "role": "admin", "approval_role": "PROJECT_MANAGEMENT"}
        self.bdh = {"email": "bdh@test.vn", "name": "BĐH", "role": "update", "approval_role": "SITE_MANAGEMENT"}
        self.bqlda_read = {"email": "bqlda@test.vn", "name": "BQLDA", "role": "read", "approval_role": "PROJECT_MANAGEMENT"}
        self.contractor = {"email": "nt@test.vn", "name": "Nhà thầu", "role": "update", "approval_role": "CONTRACTOR"}
        self.consultant = {"email": "tvgs@test.vn", "name": "TVGS", "role": "read", "approval_role": "CONSULTANT"}

    def tearDown(self):
        self.tmp.cleanup()

    def test_marker_and_role_visibility(self):
        self.assertIn("WORKSPACE PRIVATE", PATCH_MARKER)
        self.assertTrue(can_access_contract_management(self.admin))
        self.assertTrue(can_access_contract_management(self.bdh))
        self.assertTrue(can_access_contract_management(self.bqlda_read))
        self.assertFalse(can_access_contract_management(self.contractor))
        self.assertFalse(can_access_contract_management(self.consultant))
        self.assertTrue(can_edit_contract_management(self.bdh))
        self.assertFalse(can_edit_contract_management(self.bqlda_read))

    def test_records_are_isolated_by_workspace(self):
        a = create_contract_record(
            self.db,
            workspace_project_id=1,
            record_type="Hợp đồng",
            record_no="HD-01",
            title="Hợp đồng MEP",
            signed_date="2026-01-10",
            amount=1000000000,
            actor=self.admin,
        )
        b = create_contract_record(
            self.db,
            workspace_project_id=2,
            record_type="Hợp đồng",
            record_no="HD-01",
            title="Hợp đồng nhà thầu B",
            amount=500000000,
            actor=self.admin,
        )
        self.assertNotEqual(a["id"], b["id"])
        rows_a = list_contract_records(self.db, 1)
        rows_b = list_contract_records(self.db, 2)
        self.assertEqual([x["title"] for x in rows_a], ["Hợp đồng MEP"])
        self.assertEqual([x["title"] for x in rows_b], ["Hợp đồng nhà thầu B"])
        self.assertEqual(contract_summary(self.db, 1)["contracts"], 1)

    def test_contract_and_appendix_share_one_sheet(self):
        create_contract_record(self.db, workspace_project_id=1, record_type="Hợp đồng", record_no="HD-01", actor=self.admin)
        create_contract_record(self.db, workspace_project_id=1, record_type="Phụ lục", record_no="PL-01", actor=self.admin)
        summary = contract_summary(self.db, 1)
        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["contracts"], 1)
        self.assertEqual(summary["appendices"], 1)

    def test_file_versions_keep_history_and_latest_pointer(self):
        record = create_contract_record(self.db, workspace_project_id=1, record_type="Hợp đồng", record_no="HD-02", actor=self.admin)
        v1 = register_contract_file(
            self.db,
            record_id=record["id"],
            workspace_project_id=1,
            file_item={"id": "file-1", "name": "HD-02.pdf", "mime_type": "application/pdf", "size": 100},
            uploaded_by=self.admin["email"],
        )
        v2 = register_contract_file(
            self.db,
            record_id=record["id"],
            workspace_project_id=1,
            file_item={"id": "file-2", "name": "HD-02-rev1.pdf", "mime_type": "application/pdf", "size": 120},
            uploaded_by=self.admin["email"],
        )
        self.assertEqual(v1["version_no"], 1)
        self.assertEqual(v2["version_no"], 2)
        versions = list_contract_file_versions(self.db, record["id"], workspace_project_id=1)
        self.assertEqual(len(versions), 2)
        self.assertEqual(versions[0]["file_id"], "file-2")
        self.assertEqual(int(versions[0]["is_current"]), 1)
        self.assertEqual(int(versions[1]["is_current"]), 0)
        latest = get_contract_record(self.db, record["id"], workspace_project_id=1)
        self.assertEqual(latest["current_file_id"], "file-2")
        self.assertEqual(contract_summary(self.db, 1)["with_file"], 1)

    def test_update_and_delete_stay_inside_workspace(self):
        a = create_contract_record(self.db, workspace_project_id=1, record_type="Hợp đồng", record_no="HD-A", actor=self.admin)
        b = create_contract_record(self.db, workspace_project_id=2, record_type="Hợp đồng", record_no="HD-B", actor=self.admin)
        updated = update_contract_record(self.db, a["id"], workspace_project_id=1, actor=self.bdh, title="Đã cập nhật")
        self.assertEqual(updated["title"], "Đã cập nhật")
        with self.assertRaises(ValueError):
            update_contract_record(self.db, b["id"], workspace_project_id=1, actor=self.bdh, title="Không được")
        delete_contract_record(self.db, a["id"], workspace_project_id=1)
        self.assertEqual(list_contract_records(self.db, 1), [])
        self.assertEqual(len(list_contract_records(self.db, 2)), 1)

    def test_ai_prompt_is_contract_only_and_source_tagged(self):
        record = create_contract_record(
            self.db,
            workspace_project_id=1,
            record_type="Phụ lục",
            record_no="PL-03",
            title="Gia hạn thời gian",
            actor=self.admin,
        )
        prompt = build_contract_ai_prompt([record], "Gia hạn bao lâu?", ["PL-03.pdf"], [])
        self.assertIn("[HĐ:", prompt)
        self.assertIn("PL-03", prompt)
        self.assertIn("Gia hạn bao lâu?", prompt)
        self.assertIn("Không dùng dữ liệu ngoài workspace này", prompt)

    def test_navigation_source_hides_contract_sheet_by_role(self):
        source = Path("v622_ui_v7_compact_patch.py").read_text(encoding="utf-8")
        self.assertIn("from contract_management_v622 import can_access_contract_management, render_contract_management_v622", source)
        self.assertIn('if _v7_group == "📁 Hồ sơ" and can_access_contract_management(_v7_identity):', source)
        self.assertIn("📑 Quản lý hợp đồng", source)
        self.assertIn("st, db, pid", source)
        self.assertIn("session_token=_gateway_session_token()", source)


if __name__ == "__main__":
    unittest.main()
