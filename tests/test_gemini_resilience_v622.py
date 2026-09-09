from __future__ import annotations

import os
import unittest

import ai_service
import gemini_resilience_v622 as resilience


class FakeGeminiError(RuntimeError):
    def __init__(self, status_code: int, message: str = "temporary overload"):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


class _ModelInfo:
    def __init__(self, name: str):
        self.name = f"models/{name}"
        self.supported_actions = ["generateContent"]


class _Response:
    text = "OK"


class _Models:
    def __init__(self):
        self.calls: list[str] = []
        self.available = [
            "gemini-3.7-flash",
            "gemini-3.6-flash",
            "gemini-3.5-flash",
            "gemini-3.1-flash-lite",
            "gemini-2.5-flash",
            "gemini-2.5-flash-lite",
        ]

    def list(self):
        return [_ModelInfo(x) for x in self.available]

    def generate_content(self, *, model, contents, config):
        self.calls.append(model)
        if model == "gemini-3.6-flash":
            raise FakeGeminiError(503, "The model is overloaded")
        if model == "gemini-2.5-flash":
            return _Response()
        raise AssertionError(f"Unexpected model before stable fallback: {model}")


class _Client:
    def __init__(self):
        self.models = _Models()


class GeminiResilienceTests(unittest.TestCase):
    def setUp(self):
        resilience.install_gemini_resilience()
        resilience._MODEL_COOLDOWN_UNTIL.clear()
        self.old_env = {k: os.environ.get(k) for k in (
            "GEMINI_RETRY_ATTEMPTS",
            "GEMINI_MAX_FALLBACK_MODELS",
            "GEMINI_MODEL_COOLDOWN_SECONDS",
        )}
        os.environ["GEMINI_RETRY_ATTEMPTS"] = "1"
        os.environ["GEMINI_MAX_FALLBACK_MODELS"] = "5"
        os.environ["GEMINI_MODEL_COOLDOWN_SECONDS"] = "90"

    def tearDown(self):
        for key, value in self.old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        resilience._MODEL_COOLDOWN_UNTIL.clear()

    def test_first_four_auto_candidates_include_stable_25_fallbacks(self):
        assistant = ai_service.GeminiProjectAssistant(
            ":memory:", ai_service.GeminiSettings(api_key="test", model="auto", use_web=False)
        )
        client = _Client()
        seq = assistant._fallback_model_sequence(client)
        self.assertEqual(seq[0], "gemini-3.6-flash")
        self.assertEqual(seq[1], "gemini-2.5-flash")
        self.assertIn("gemini-2.5-flash-lite", seq[:4])

    def test_503_moves_from_36_to_25_flash(self):
        assistant = ai_service.GeminiProjectAssistant(
            ":memory:", ai_service.GeminiSettings(api_key="test", model="auto", use_web=False)
        )
        client = _Client()
        response = assistant._generate_content_with_fallback(client, contents=["hello"], config=None)
        self.assertEqual(response.text, "OK")
        self.assertEqual(client.models.calls[:2], ["gemini-3.6-flash", "gemini-2.5-flash"])
        self.assertGreater(resilience._cooldown_remaining("gemini-3.6-flash"), 0)

    def test_cooldown_deprioritizes_recently_overloaded_model(self):
        assistant = ai_service.GeminiProjectAssistant(
            ":memory:", ai_service.GeminiSettings(api_key="test", model="auto", use_web=False)
        )
        client = _Client()
        resilience._mark_cooldown("gemini-3.6-flash", 60)
        seq = assistant._fallback_model_sequence(client)
        self.assertEqual(seq[0], "gemini-2.5-flash")
        self.assertGreater(seq.index("gemini-3.6-flash"), seq.index("gemini-2.5-flash-lite"))


if __name__ == "__main__":
    unittest.main()
