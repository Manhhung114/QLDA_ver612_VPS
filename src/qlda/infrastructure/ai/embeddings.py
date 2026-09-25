from __future__ import annotations

import os
from typing import Sequence


class ProviderEmbeddingAdapter:
    """Small provider adapter used only for semantic retrieval.

    If no embedding credential/model is available callers should catch the error
    and keep lexical retrieval active; chat availability is never coupled to RAG.
    """

    def __init__(self, provider: str | None = None) -> None:
        configured = str(provider or os.environ.get("QLDA_AI_EMBEDDING_PROVIDER", "auto") or "auto").strip().lower()
        if configured == "auto":
            configured = "openai" if os.environ.get("OPENAI_API_KEY") else "gemini"
        self.provider = configured

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        values = [str(x or "") for x in texts]
        if not values:
            return []
        if self.provider == "openai":
            return self._openai(values)
        if self.provider in {"gemini", "google"}:
            return self._gemini(values)
        raise RuntimeError(f"Embedding provider không hỗ trợ: {self.provider}")

    @staticmethod
    def _openai(texts: list[str]) -> list[list[float]]:
        from openai import OpenAI

        key = str(os.environ.get("OPENAI_API_KEY") or "").strip()
        if not key:
            raise RuntimeError("OPENAI_API_KEY trống")
        model = str(os.environ.get("OPENAI_EMBEDDING_MODEL") or "text-embedding-3-small").strip()
        response = OpenAI(api_key=key).embeddings.create(model=model, input=texts)
        return [[float(v) for v in item.embedding] for item in response.data]

    @staticmethod
    def _gemini(texts: list[str]) -> list[list[float]]:
        from google import genai

        key = str(os.environ.get("GEMINI_API_KEY") or "").strip()
        if not key:
            raise RuntimeError("GEMINI_API_KEY trống")
        model = str(os.environ.get("GEMINI_EMBEDDING_MODEL") or "gemini-embedding-001").strip()
        client = genai.Client(api_key=key)
        result: list[list[float]] = []
        for text in texts:
            response = client.models.embed_content(model=model, contents=text)
            embeddings = getattr(response, "embeddings", None) or []
            if not embeddings:
                raise RuntimeError("Gemini embedding không trả vector")
            values = getattr(embeddings[0], "values", None) or []
            result.append([float(v) for v in values])
        return result


__all__ = ["ProviderEmbeddingAdapter"]
