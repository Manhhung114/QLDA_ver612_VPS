from __future__ import annotations

import sqlite3
import unittest

from qlda.presentation.streamlit.legacy_ai_streaming_contract import (
    install_legacy_ai_streaming_contract,
)
from qlda.runtime_core.ai_service import (
    GeminiProjectAssistant,
    OpenAIProjectAssistant,
    ProjectContextBuilder,
)


class AIStreamingContractTests(unittest.TestCase):
    def test_legacy_project_assistants_expose_streaming_contract_for_ui(self):
        install_legacy_ai_streaming_contract()
        self.assertTrue(callable(getattr(OpenAIProjectAssistant, "ask_project_stream", None)))
        self.assertTrue(callable(getattr(GeminiProjectAssistant, "ask_project_stream", None)))

    def test_install_is_idempotent(self):
        install_legacy_ai_streaming_contract()
        first = GeminiProjectAssistant.ask_project_stream
        install_legacy_ai_streaming_contract()
        self.assertIs(GeminiProjectAssistant.ask_project_stream, first)

    def test_legacy_legal_module_alias_maps_to_real_sql_table(self):
        install_legacy_ai_streaming_contract()
        builder = ProjectContextBuilder(":memory:")
        connection = sqlite3.connect(":memory:")
        try:
            connection.execute("CREATE TABLE legal_documents (id INTEGER PRIMARY KEY)")
            self.assertTrue(
                builder.table_exists(connection, "qlda.runtime_core.legal_documents")
            )
            self.assertTrue(builder.table_exists(connection, "legal_documents"))
        finally:
            connection.close()


if __name__ == "__main__":
    unittest.main()
