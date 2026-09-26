from __future__ import annotations

import re
import unittest
from contextlib import contextmanager
from unittest.mock import patch

from qlda.infrastructure.ai import aggregate_context as agg
from qlda.infrastructure.ai import project_chat_complete as complete


class _Result:
    def __init__(self, rows):
        self.rows = list(rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)


class _AggregateConnection:
    def __init__(self):
        self.documents = [
            {
                "id": 11,
                "project_id": 101,
                "doc_type": "BBHT",
                "code": "S4-MEP-001",
                "subject": "KIỂM TRA LẠI KHU VỰC S4",
                "discipline": "MEP",
                "contractor": "SIGMA",
                "issuer": "Nguyễn A",
                "assignee": "SIGMA",
                "issue_date": "2026-09-25",
                "status": "Mới lập",
                "priority": "Thấp",
                "related_wbs": "",
                "description": "Kiểm tra hiện trường khu vực S4.",
                "response": "",
                "note": "",
                "cost_impact": 0,
                "time_impact_days": 0,
            },
            {
                "id": 10,
                "project_id": 101,
                "doc_type": "BBHT",
                "code": "B12-MEP-002",
                "subject": "Nhà thầu khắc phục tồn tại",
                "discipline": "MEP",
                "contractor": "SIGMA",
                "issuer": "Nguyễn B",
                "assignee": "SIGMA",
                "issue_date": "2026-09-20",
                "status": "Mới lập",
                "priority": "Thấp",
                "related_wbs": "",
                "description": "Khắc phục tồn tại tại B12.",
                "response": "",
                "note": "",
                "cost_impact": 0,
                "time_impact_days": 0,
            },
            {
                "id": 4,
                "project_id": 101,
                "doc_type": "BBHT",
                "code": "S2-MEP-001",
                "subject": "Biên bản bàn giao mặt bằng",
                "discipline": "MEP",
                "contractor": "SIGMA",
                "issuer": "Đất Việt",
                "assignee": "Sigma",
                "issue_date": "2026-09-16",
                "status": "Mới lập",
                "priority": "Thấp",
                "related_wbs": "",
                "description": "Bàn giao MB lắp giàn nóng và lắp đặt thiết bị.",
                "response": "",
                "note": "",
                "cost_impact": 0,
                "time_impact_days": 0,
            },
            {
                "id": 3,
                "project_id": 101,
                "doc_type": "BBHOP",
                "code": "BBH-001",
                "subject": "Họp combine",
                "discipline": "MEP",
                "contractor": "SIGMA",
                "issuer": "Lê Việt Phúc",
                "assignee": "Team MEP và Nhà thầu Sigma",
                "issue_date": "2026-09-07",
                "status": "",
                "priority": "",
                "related_wbs": "",
                "description": "Xem file đính kèm.",
                "response": "",
                "note": "",
                "cost_impact": 0,
                "time_impact_days": 0,
            },
        ]
        self.counts = {
            "documents": 4,
            "material_master": 3,
        }
        self.calls = []

    def execute(self, sql, params=()):
        text = " ".join(str(sql).split()).lower()
        self.calls.append((text, params))
        if text.startswith("select count(*) as n from"):
            match = re.search(r"from\s+([a-z0-9_]+)\s+where", text)
            table = match.group(1) if match else ""
            return _Result([{"n": self.counts.get(table, 0)}])
        if "group by upper(coalesce(doc_type,''))" in text:
            grouped = {}
            for row in self.documents:
                dtype = str(row.get("doc_type") or "").upper()
                grouped[dtype] = grouped.get(dtype, 0) + 1
            return _Result([{"doc_type": key, "n": value} for key, value in grouped.items()])
        if " from documents" in f" {text}":
            if "upper(coalesce(doc_type,''))=%s" in text:
                dtype = str(params[1]).upper()
                limit = int(params[2])
                rows = [row for row in self.documents if str(row.get("doc_type") or "").upper() == dtype]
                return _Result(sorted(rows, key=lambda row: row["id"], reverse=True)[:limit])
            limit = int(params[1])
            return _Result(sorted(self.documents, key=lambda row: row["id"], reverse=True)[:limit])
        return _Result([])


class ExactAggregateContextTests(unittest.TestCase):
    def test_statistical_phrases_are_exhaustive(self):
        for question in (
            "Thống kê tất cả các biên bản hiện trường",
            "Có bao nhiêu biên bản hiện trường?",
            "Liệt kê toàn bộ biên bản hiện trường",
            "Đếm các biên bản hiện trường",
        ):
            with self.subTest(question=question):
                self.assertTrue(agg.is_exhaustive_intent(question))

    def test_bbht_total_is_exact_and_all_three_rows_are_exposed(self):
        connection = _AggregateConnection()
        with patch.object(agg, "_table_exists", side_effect=lambda _c, table: table in {"documents"}):
            text = agg.build_authoritative_aggregate_context(
                connection,
                [101],
                "Thống kê tất cả các biên bản hiện trường",
            )

        self.assertIn("[EXACT-DOC-TYPE] BBHT=3 | Biên bản hiện trường", text)
        self.assertIn("[EXACT-DOC-SUMMARY] loại=BBHT | Biên bản hiện trường | tổng bản ghi=3", text)
        self.assertIn("S4-MEP-001", text)
        self.assertIn("B12-MEP-002", text)
        self.assertIn("S2-MEP-001", text)
        self.assertNotIn("[EXACT-DOC:3]", text)
        self.assertNotIn("BBH-001", text)

    def test_requested_document_type_with_zero_rows_never_falls_back_to_other_types(self):
        connection = _AggregateConnection()
        with patch.object(agg, "_table_exists", side_effect=lambda _c, table: table == "documents"):
            text = agg.build_authoritative_aggregate_context(
                connection,
                [101],
                "Thống kê tất cả NCR",
            )

        self.assertIn("[EXACT-DOC-TYPE] NCR=0 | NCR", text)
        self.assertIn("[EXACT-DOC-SUMMARY] loại=NCR | NCR | tổng bản ghi=0", text)
        self.assertNotIn("S2-MEP-001", text)
        self.assertNotIn("BBH-001", text)

    def test_other_sheet_statistics_use_exact_sql_count_not_keyword_subset(self):
        connection = _AggregateConnection()
        with patch.object(agg, "_table_exists", side_effect=lambda _c, table: table == "material_master"):
            text = agg.build_authoritative_aggregate_context(
                connection,
                [101],
                "Thống kê tất cả vật tư",
            )

        self.assertIn("[EXACT-DOMAIN] materials=3 | Vật tư/thiết bị", text)
        self.assertNotIn("documents=", text)

    def test_non_statistical_question_does_not_bloat_prompt_with_aggregate_inventory(self):
        connection = _AggregateConnection()
        text = agg.build_authoritative_aggregate_context(
            connection,
            [101],
            "Nội dung biên bản S2-MEP-001 là gì?",
        )
        self.assertEqual(text, "")

    def test_complete_chat_places_exact_aggregate_ahead_of_retrieval_details(self):
        captured = {}

        @contextmanager
        def fake_connect():
            yield object()

        def fake_run(provider, action, tenant, project, prompt, **kwargs):
            captured["prompt"] = prompt
            return "ok"

        with (
            patch.object(
                complete,
                "build_live_project_context",
                return_value=("[LIVE-SUMMARY] hồ sơ=4", {"workspace_ids": [101], "master_project_id": 1, "project_wide": False}),
            ),
            patch.object(complete, "connect", fake_connect),
            patch.object(
                complete,
                "build_authoritative_aggregate_context",
                return_value="[AGGREGATE-AUTHORITY]\n[EXACT-DOC-TYPE] BBHT=3",
            ),
            patch.object(
                complete,
                "build_live_domain_context",
                return_value="[DOC:4] Biên bản hiện trường S2-MEP-001",
            ),
            patch.object(complete.NativeProviderGateway, "run", side_effect=fake_run),
            patch.object(complete, "record_ai_event"),
        ):
            result = complete.ask_project_chat(
                101,
                "Thống kê tất cả biên bản hiện trường",
                provider="gemini",
                workspace_scope=101,
            )

        self.assertEqual(result, "ok")
        prompt = captured["prompt"]
        self.assertLess(prompt.index("[EXACT-DOC-TYPE] BBHT=3"), prompt.index("[DOC:4]"))
        self.assertIn("Tuyệt đối không dùng số lượng dòng chi tiết", prompt)
        self.assertIn("tổng chính xác từ SQL", prompt)


if __name__ == "__main__":
    unittest.main()
