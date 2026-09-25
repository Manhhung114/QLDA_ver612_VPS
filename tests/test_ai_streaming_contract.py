from __future__ import annotations

import unittest

from qlda.presentation.streamlit.legacy_ai_streaming_contract import (
    install_legacy_ai_streaming_contract,
)
from qlda.runtime_core.ai_service import GeminiProjectAssistant, OpenAIProjectAssistant


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


if __name__ == "__main__":
    unittest.main()
