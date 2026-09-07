from postgres_backend_v622 import CompatRow, _rewrite_sql


def test_qmark_translation_preserves_quoted_question_mark():
    sql, wants_id = _rewrite_sql("SELECT '?' AS q, id FROM projects WHERE id=?")
    assert sql == "SELECT '?' AS q, id FROM projects WHERE id=%s"
    assert wants_id is False


def test_sqlite_ddl_translation():
    sql, wants_id = _rewrite_sql(
        "CREATE TABLE demo(id INTEGER PRIMARY KEY AUTOINCREMENT, payload BLOB)"
    )
    assert sql == "CREATE TABLE demo(id SERIAL PRIMARY KEY, payload BYTEA)"
    assert wants_id is False


def test_insert_lastrowid_compatibility():
    sql, wants_id = _rewrite_sql("INSERT INTO projects(code,name) VALUES(?,?)")
    assert sql.endswith("RETURNING id")
    assert "VALUES(%s,%s)" in sql
    assert wants_id is True


def test_compat_row_supports_sqlite_access_patterns():
    row = CompatRow(["id", "name"], [7, "QLDA"])
    assert row[0] == 7
    assert row["name"] == "QLDA"
    assert row.get("missing", "x") == "x"
    assert dict(row) == {"id": 7, "name": "QLDA"}
