from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from vps_status_v622 import (
    PATCH_MARKER,
    collect_vps_storage,
    disk_health,
    human_bytes,
    render_vps_status_v622,
)


class _Cursor:
    def fetchone(self):
        return (123456789,)


class _Connection:
    def execute(self, sql):
        assert "pg_database_size" in sql
        return _Cursor()


class _DB:
    def connect(self):
        class _CM:
            def __enter__(self):
                return _Connection()

            def __exit__(self, exc_type, exc, tb):
                return False

        return _CM()


class _NonAdminSt:
    def __init__(self):
        self.warning_messages = []

    def warning(self, message):
        self.warning_messages.append(str(message))


class VPSStatusV622Tests(unittest.TestCase):
    def test_human_bytes(self):
        self.assertEqual(human_bytes(0), "0 B")
        self.assertEqual(human_bytes(1024), "1.00 KB")
        self.assertEqual(human_bytes(1024 ** 3), "1.00 GB")

    def test_health_thresholds(self):
        self.assertEqual(disk_health(10)[1], "🟢 Bình thường")
        self.assertEqual(disk_health(70)[1], "🟡 Cần theo dõi")
        self.assertEqual(disk_health(85)[1], "🟠 Sắp đầy")
        self.assertEqual(disk_health(95)[1], "🔴 Nguy hiểm")

    def test_collect_reads_disk_directories_and_postgres(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data = root / "data"
            app = root / "app"
            shared = root / "shared"
            logs = root / "logs"
            for path in (data, app, shared, logs):
                path.mkdir()
            (data / "sample.bin").write_bytes(b"x" * 4096)
            payload = collect_vps_storage(
                _DB(),
                data_path=data,
                app_path=app,
                shared_path=shared,
                log_path=logs,
                disk_path=root,
            )
            self.assertEqual(payload["marker"], PATCH_MARKER)
            self.assertGreater(payload["disk"]["total"], 0)
            components = {row["component"]: row for row in payload["components"]}
            self.assertGreaterEqual(components["File dữ liệu QLDA"]["bytes"], 4096)
            self.assertEqual(components["PostgreSQL hiện tại"]["bytes"], 123456789)

    def test_render_is_defense_in_depth_admin_only(self):
        st = _NonAdminSt()
        render_vps_status_v622(st, _DB(), is_admin=False)
        self.assertEqual(len(st.warning_messages), 1)
        self.assertIn("chỉ dành cho Admin", st.warning_messages[0])

    def test_v7_tools_wiring_is_admin_only(self):
        source = Path("v622_ui_v7_compact_patch.py").read_text(encoding="utf-8")
        self.assertIn("from vps_status_v622 import render_vps_status_v622", source)
        self.assertIn('if _v7_group == "📚 Công cụ" and bool(_is_admin()):', source)
        self.assertIn('("🖥️ Trạng thái VPS", lambda: render_vps_status_v622(st, db, is_admin=True))', source)


if __name__ == "__main__":
    unittest.main()
