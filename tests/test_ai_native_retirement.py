from __future__ import annotations

import os

import pytest

from qlda.infrastructure.ai.document_ingestion import build_document_chunks
from qlda.infrastructure.ai.provider_engine import NativeProviderEngine, NativeProviderError, _safe_error
from qlda.infrastructure.native_ai import NativeAIAdapter


def test_document_ingestion_provenance_is_deterministic_and_tenant_scoped() -> None:
    data = b"NCR-001: xu ly be tong\nRFI-009: cho phan hoi"
    p1, chunks1 = build_document_chunks(17, "site-note.txt", data, source_ref="upload:17:site-note")
    p2, chunks2 = build_document_chunks(17, "site-note.txt", data, source_ref="upload:17:site-note")

    assert p1 == p2
    assert p1.workspace_project_id == 17
    assert p1.source_ref == "upload:17:site-note"
    assert p1.checksum
    assert chunks1 == chunks2
    assert chunks1
    assert all(chunk.workspace_project_id == 17 for chunk in chunks1)
    assert all(chunk.source_ref.startswith("upload:17:site-note#chunk=") for chunk in chunks1)
    assert all(chunk.metadata.get("document_checksum") == p1.checksum for chunk in chunks1)


def test_document_ingestion_rejects_invalid_tenant() -> None:
    with pytest.raises(ValueError):
        build_document_chunks(0, "x.txt", b"hello")


def test_provider_error_redaction_does_not_corrupt_message_when_keys_are_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    error = _safe_error(RuntimeError("plain provider failure"), "openai")
    assert str(error) == "plain provider failure"


def test_provider_error_redacts_configured_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-secret-value")
    error = _safe_error(RuntimeError("bad sk-test-secret-value token"), "openai")
    assert "sk-test-secret-value" not in str(error)
    assert "***" in str(error)


def test_native_adapter_chat_calls_native_provider_not_legacy(monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = NativeAIAdapter()
    monkeypatch.setattr(adapter, "_ground_question", lambda tenant, question, provider: (question, ["source:1"]))
    calls: list[tuple[str, str, bool | None]] = []

    def fake_complete(provider: str, prompt: str, *, use_web: bool | None = None) -> str:
        calls.append((provider, prompt, use_web))
        return "native-ok"

    monkeypatch.setattr(adapter, "_complete", fake_complete)
    monkeypatch.setattr(adapter, "_event", lambda *args, **kwargs: None)

    result = adapter.ask(9, "Tiến độ?", provider="openai", workspace_scope=9, use_web=False)
    assert result == "native-ok"
    assert calls and calls[0][0] == "openai"
    assert "WORKSPACE_PROJECT_ID: 9" in calls[0][1]


def test_native_adapter_enforces_project_workspace_identity() -> None:
    adapter = NativeAIAdapter()
    with pytest.raises(ValueError):
        adapter.ask(9, "x", workspace_scope=10)


def test_provider_engine_requires_key_without_importing_legacy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(NativeProviderError) as exc:
        NativeProviderEngine.complete("openai", "hello", use_web=False)
    assert exc.value.code == "missing_api_key"
