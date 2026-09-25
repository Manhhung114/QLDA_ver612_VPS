from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from qlda.infrastructure.ai.provider_gateway import (
    _friendly_error,
    _gemini_generate,
)


class _FakeModels:
    def __init__(self, failures: int):
        self.failures = failures
        self.calls = 0
        self.models: list[str] = []

    def generate_content(self, **kwargs):
        self.calls += 1
        self.models.append(str(kwargs.get("model") or ""))
        if self.calls <= self.failures:
            raise RuntimeError(
                "503 UNAVAILABLE. This model is currently experiencing high demand. Please try again later."
            )
        return SimpleNamespace(text="ok")


class _FakeClient:
    def __init__(self, failures: int):
        self.models = _FakeModels(failures)


class _InvalidModelModels:
    def __init__(self):
        self.calls: list[str] = []
        self.list_called = False

    def list(self):
        self.list_called = True
        return [SimpleNamespace(name="models/gemini-2.5-flash")]

    def generate_content(self, **kwargs):
        model = str(kwargs.get("model") or "")
        self.calls.append(model)
        raise RuntimeError(
            "400 INVALID_ARGUMENT: GenerateContentRequest.model: unexpected model name format"
        )


class _InvalidModelClient:
    def __init__(self):
        self.models = _InvalidModelModels()


class _CaptureModels:
    def __init__(self):
        self.calls: list[str] = []

    def generate_content(self, **kwargs):
        self.calls.append(str(kwargs.get("model") or ""))
        return SimpleNamespace(text="ok")


class _CaptureClient:
    def __init__(self):
        self.models = _CaptureModels()


class AIProviderResilienceTests(unittest.TestCase):
    def test_gemini_transient_503_is_retried_with_bounded_backoff(self):
        client = _FakeClient(failures=2)
        with patch("qlda.infrastructure.ai.provider_gateway.time.sleep") as sleep, patch(
            "qlda.infrastructure.ai.provider_gateway.random.uniform", return_value=0.0
        ):
            response = _gemini_generate(
                client,
                model="gemini-3.8-flash",
                contents="hello",
                config=object(),
            )

        self.assertEqual(response.text, "ok")
        self.assertEqual(client.models.calls, 3)
        self.assertEqual(client.models.models, ["gemini-3.8-flash"] * 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1.0, 2.0])

    def test_gemini_persistent_503_stops_after_maximum_retries_same_model(self):
        client = _FakeClient(failures=99)
        with patch("qlda.infrastructure.ai.provider_gateway.time.sleep"), patch(
            "qlda.infrastructure.ai.provider_gateway.random.uniform", return_value=0.0
        ):
            with self.assertRaises(RuntimeError):
                _gemini_generate(
                    client,
                    model="gemini-3.5-flash",
                    contents="hello",
                    config=object(),
                )

        self.assertEqual(client.models.calls, 4)
        self.assertEqual(client.models.models, ["gemini-3.5-flash"] * 4)

    def test_invalid_model_does_not_list_or_fallback_to_another_model(self):
        client = _InvalidModelClient()
        with self.assertRaises(RuntimeError):
            _gemini_generate(
                client,
                model="gemini-3.5-flash",
                contents="hello",
                config=object(),
            )

        self.assertEqual(client.models.calls, ["gemini-3.5-flash"])
        self.assertFalse(client.models.list_called)

    def test_human_model_label_is_normalized_without_changing_generation(self):
        client = _CaptureClient()
        response = _gemini_generate(
            client,
            model="Gemini 3.5 Flash",
            contents="hello",
            config=object(),
        )
        self.assertEqual(response.text, "ok")
        self.assertEqual(client.models.calls, ["gemini-3.5-flash"])

    def test_models_prefix_is_normalized_without_changing_generation(self):
        client = _CaptureClient()
        _gemini_generate(
            client,
            model="models/gemini-2.5-flash",
            contents="hello",
            config=object(),
        )
        self.assertEqual(client.models.calls, ["gemini-2.5-flash"])

    def test_invalid_model_format_has_fixed_model_friendly_error(self):
        err = _friendly_error(
            RuntimeError("400 INVALID_ARGUMENT: GenerateContentRequest.model: unexpected model name format")
        )
        self.assertEqual(err.code, "model_unavailable")
        self.assertFalse(err.retryable)
        self.assertIn("model", str(err).lower())
        self.assertIn("không tự đổi", err.action.lower())

    def test_503_is_mapped_to_retryable_friendly_error(self):
        err = _friendly_error(RuntimeError("503 UNAVAILABLE: high demand"))
        self.assertEqual(err.code, "service_unavailable")
        self.assertTrue(err.retryable)
        self.assertIn("quá tải", err.action)


if __name__ == "__main__":
    unittest.main()
