from __future__ import annotations

import unittest

from qlda.infrastructure.ai.workspace_indexer import _chunk_from_row


class WorkspaceRAGIndexerTests(unittest.TestCase):
    def test_document_chunk_has_workspace_and_traceable_source(self):
        chunk = _chunk_from_row(
            "documents",
            {
                "id": 44,
                "project_id": 101,
                "doc_type": "RFI",
                "code": "RFI-017",
                "subject": "Xác nhận cao độ ống",
                "due_date": "2026-09-30",
                "status": "Đang xử lý",
            },
            101,
        )
        self.assertIsNotNone(chunk)
        self.assertEqual(chunk.workspace_project_id, 101)
        self.assertEqual(chunk.source_ref, "documents:44:RFI-017")
        self.assertEqual(chunk.metadata["domain"], "documents")
        self.assertIn("subject: Xác nhận cao độ ống", chunk.content)

    def test_financial_amounts_are_not_indexed_as_llm_authoritative_math(self):
        chunk = _chunk_from_row(
            "payment_claims",
            {
                "id": 9,
                "project_id": 101,
                "claim_code": "IPC-09",
                "status": "Đã trình",
                "requested_amount": 999999999999,
                "certified_cumulative": 888888888888,
                "payment_due_date": "2026-10-15",
            },
            101,
        )
        self.assertIsNotNone(chunk)
        self.assertNotIn("999999999999", chunk.content)
        self.assertNotIn("888888888888", chunk.content)
        self.assertIn("payment_due_date", chunk.content)


if __name__ == "__main__":
    unittest.main()
