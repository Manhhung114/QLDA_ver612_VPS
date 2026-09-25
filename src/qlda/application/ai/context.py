from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from qlda.application.ai.ports import AIChunk, AIContextPort, ContextBundle


@dataclass(slots=True)
class AIContextService:
    """Framework-independent retrieval facade with hard tenant isolation."""

    retriever: AIContextPort
    max_chars: int = 18000

    def retrieve(
        self,
        workspace_project_id: int,
        query: str,
        *,
        domains: Sequence[str] | None = None,
        top_k: int = 8,
    ) -> ContextBundle:
        tenant = int(workspace_project_id)
        if tenant <= 0:
            raise ValueError("workspace_project_id phải > 0")
        bundle = self.retriever.retrieve(tenant, str(query or ""), domains=domains, top_k=max(1, int(top_k)))
        safe: list[AIChunk] = []
        for chunk in bundle.chunks:
            if int(chunk.workspace_project_id) != tenant:
                raise RuntimeError("AI retrieval trả dữ liệu ngoài contractor workspace hiện tại.")
            safe.append(chunk)
        return ContextBundle(tenant, str(query or ""), tuple(safe))

    def build_grounded_context(
        self,
        workspace_project_id: int,
        query: str,
        *,
        domains: Sequence[str] | None = None,
        top_k: int = 8,
    ) -> tuple[str, ContextBundle]:
        bundle = self.retrieve(workspace_project_id, query, domains=domains, top_k=top_k)
        parts: list[str] = []
        used = 0
        for index, chunk in enumerate(bundle.chunks, start=1):
            label = chunk.source_ref or f"source:{index}"
            block = f"[NGUỒN {index}: {label}]\n{chunk.content.strip()}"
            if not block.strip():
                continue
            remaining = max(0, int(self.max_chars) - used)
            if remaining <= 0:
                break
            block = block[:remaining]
            parts.append(block)
            used += len(block)
        return "\n\n".join(parts), bundle


__all__ = ["AIContextService"]
