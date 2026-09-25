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

    def generate_content(self, **kwargs):
        self.calls += 1
        if self.calls <= self.failures:
            raise RuntimeError(
                "503 UNAVAILABLE. This model is currently experiencing high demand. Please try again later."
            )
        return SimpleNamespace(text="ok")


class _FakeClient:
    def __init__(self, failures: int):
        self.models = _FakeModels(failures)


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
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1.0, 2.0])

    def test_gemini_persistent_503_stops_after_maximum_retries(self):
        client = _FakeClient(failures=99)
        with patch("qlda.infrastructure.ai.provider_gateway.time.sleep"), patch(
            "qlda.infrastructure.ai.provider_gateway.random.uniform", return_value=0.0
        ):
            with self.assertRaises(RuntimeError):
                _gemini_generate(
                    client,
                    model="gemini-3.8-flash",
                    contents="hello",
                    config=object(),
                )

        self.assertEqual(client.models.calls, 4)

    def test_503_is_mapped_to_retryable_friendly_error(self):
        err = _friendly_error(RuntimeError("503 UNAVAILABLE: high demand"))
        self.assertEqual(err.code, "service_unavailable")
        self.assertTrue(err.retryable)
        self.assertIn("quá tải", err.action)


if __name__ == "__main__":
    unittest.main()
