from __future__ import annotations

import inspect
import unittest

from qlda.infrastructure.ai.project_chat import project_wide_intent
from qlda.presentation.streamlit import legacy_ai_streaming_contract as contract
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

    def test_project_chat_contract_uses_native_live_data_path(self):
        install_legacy_ai_streaming_contract()
        ask_source = inspect.getsource(contract._ask_project)
        stream_source = inspect.getsource(contract._ask_project_stream)
        self.assertIn("qlda.infrastructure.ai.project_chat_complete", ask_source)
        self.assertIn("_active_scope", ask_source)
        self.assertNotIn("qlda.runtime_core.ai_streaming", ask_source + stream_source)
        self.assertIn("_ask_project(", stream_source)
        self.assertIs(GeminiProjectAssistant.ask_project, contract._ask_project)
        self.assertIs(OpenAIProjectAssistant.ask_project, contract._ask_project)
        self.assertIs(GeminiProjectAssistant.ask_project_stream, contract._ask_project_stream)
        self.assertIs(OpenAIProjectAssistant.ask_project_stream, contract._ask_project_stream)

    def test_specialized_ai_entrypoints_are_workspace_guarded(self):
        source = inspect.getsource(contract.install_legacy_ai_streaming_contract)
        self.assertIn("assistant_cls.ask_project = _ask_project", source)
        self.assertIn("summarize_file", source)
        self.assertIn("attachment_catalog", source)
        self.assertIn("_active_scope", source)

    def test_whole_project_intent_matches_current_production_question(self):
        self.assertTrue(
            project_wide_intent(
                "Đánh giá tiến độ hoàn thành lắp đặt các hệ của tháp S4 và tổng quan toàn bộ dự án"
            )
        )
        self.assertTrue(project_wide_intent("Cho tôi tổng thể dự án và tất cả nhà thầu"))
        self.assertFalse(project_wide_intent("Tiến độ lắp đặt tháp S4 của nhà thầu đang chọn"))


if __name__ == "__main__":
    unittest.main()
