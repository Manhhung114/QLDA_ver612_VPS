from __future__ import annotations

from qlda.runtime_core import project_database as db


def test_packaged_table_order_uses_sql_identifiers_only():
    assert "legal_documents" in db.TABLE_ORDER
    assert "qlda.runtime_core.legal_documents" not in db.TABLE_ORDER
    assert all(db._safe_identifier(name) == name for name in db.TABLE_ORDER)
    assert all("." not in name for name in db.TABLE_ORDER)


def test_legal_tables_remain_in_portable_backup_order():
    assert db.TABLE_ORDER[-2:] == ("legal_documents", "legal_sync_log")
