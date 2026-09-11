from __future__ import annotations

import os
import types
import unittest
from pathlib import Path

import performance_v1_v622 as perf
from performance_postgres_v1_v622 import (
    clear_performance_postgres_cache_for_tests,
    install_performance_postgres_v1,
)


class PerformanceV1Tests(unittest.TestCase):
    def setUp(self):
        os.environ["QLDA_PERFORMANCE_V1"] = "1"
        perf.clear_process_cache_for_tests()

    def test_compiled_app_cache_survives_repeated_calls(self):
        calls = {"n": 0}

        def builder():
            calls["n"] += 1
            return compile("VALUE = 42", "<perf-test>", "exec")

        first = perf.get_compiled_app(("bundle", 1), builder)
        second = perf.get_compiled_app(("bundle", 1), builder)
        self.assertIs(first, second)
        self.assertEqual(calls["n"], 1)
        snap = perf.snapshot()
        self.assertEqual(snap["counters"].get("compiled_cache_miss"), 1)
        self.assertEqual(snap["counters"].get("compiled_cache_hit"), 1)

    def test_postgres_core_schema_runs_once_per_process(self):
        counters = {"create": 0, "migrate": 0, "rewrite": 0}

        class FakeDB:
            def __init__(self, path):
                raise AssertionError("original __init__ should be replaced")

            def create_tables(self):
                counters["create"] += 1

            def migrate(self):
                counters["migrate"] += 1

        def rewrite(sql: str, *, return_insert_id: bool = True):
            counters["rewrite"] += 1
            return (sql.replace("?", "%s"), bool(return_insert_id))

        pg = types.SimpleNamespace(
            _rewrite_sql=rewrite,
            PostgresCloudDatabase=FakeDB,
            resolve_database_url=lambda: "postgresql://example/db",
            V622_SCHEMA_VERSION="6.22-test",
        )
        clear_performance_postgres_cache_for_tests()
        install_performance_postgres_v1(pg)

        first = pg.PostgresCloudDatabase("a.db")
        second = pg.PostgresCloudDatabase("b.db")
        self.assertEqual(Path("a.db"), first.path)
        self.assertEqual(Path("b.db"), second.path)
        self.assertEqual(counters["create"], 1)
        self.assertEqual(counters["migrate"], 1)

        self.assertEqual(pg._rewrite_sql("SELECT ?"), ("SELECT %s", True))
        self.assertEqual(pg._rewrite_sql("SELECT ?"), ("SELECT %s", True))
        self.assertEqual(counters["rewrite"], 1)


if __name__ == "__main__":
    unittest.main()
