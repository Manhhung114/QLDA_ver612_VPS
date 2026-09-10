from __future__ import annotations

import base64
import gzip
import unittest
from pathlib import Path

from build_v621_webopt import _finalize_source
from v622_local_vps_patch import patch_local_vps
from v622_contractor_workspace_patch import patch_contractor_workspace
from v622_contractor_access_patch import patch_contractor_access
from v622_contractor_sidebar_patch import patch_contractor_sidebar_ui
from v622_single_session_patch import patch_single_session
from v622_schedule_management_patch import PATCH_MARKER, patch_schedule_management


ROOT = Path(__file__).resolve().parents[1]


def _generated_source() -> str:
    parts = sorted((ROOT / "v621_webopt_source").glob("part_*.b64"))
    assert len(parts) == 9
    encoded = "".join(p.read_text(encoding="ascii").strip() for p in parts)
    source = gzip.decompress(base64.b64decode(encoded)).decode("utf-8")
    source = _finalize_source(source)
    source = patch_local_vps(source)
    source = patch_contractor_workspace(source)
    source = patch_contractor_access(source)
    source = patch_single_session(source)
    source = patch_contractor_sidebar_ui(source)
    return patch_schedule_management(source)


class ScheduleManagementPatchTests(unittest.TestCase):
    def test_compact_manager_replaces_legacy_delete_block(self):
        source = _generated_source()
        self.assertIn(PATCH_MARKER, source)
        self.assertIn('with st.expander("⚙️ Quản lý công việc", expanded=False):', source)
        self.assertIn('"Công việc cần xử lý"', source)
        self.assertIn('"🗑 Xóa công việc"', source)
        self.assertNotIn('selectbox("Xóa task"', source)
        self.assertNotIn('"Xóa công việc đã chọn"', source)
        compile(source, "schedule_management_test.py", "exec")

    def test_bulk_delete_is_admin_only_and_uses_active_workspace_pid(self):
        source = _generated_source()
        self.assertIn('if _is_admin():', source)
        self.assertIn('"**ADMIN · Xóa bảng tiến độ**"', source)
        self.assertIn('"DELETE FROM tasks WHERE project_id=?"', source)
        self.assertIn("project_id=? AND LOWER(TRIM(COALESCE(source_type,'')))='mpp'", source)
        self.assertIn("UPDATE projects SET source_mpp_path='', last_sync='' WHERE id=?", source)
        self.assertIn('(int(pid),)', source)
        self.assertNotIn('schedule_bulk_contractor', source)

    def test_bulk_delete_requires_explicit_confirmation_and_preserves_other_modules(self):
        source = _generated_source()
        self.assertIn('XOA TIEN DO', source)
        self.assertIn('BOQ, Claim/IPC, VO, Hồ sơ, Bản vẽ và các nhà thầu khác không bị xóa.', source)
        self.assertNotIn('DELETE FROM cost_budgets', source)
        self.assertNotIn('DELETE FROM payment_claims', source)
        self.assertNotIn('DELETE FROM documents', source)
        self.assertNotIn('DELETE FROM drawings', source)


if __name__ == "__main__":
    unittest.main()
