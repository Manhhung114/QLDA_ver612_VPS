from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, Sequence


@dataclass(frozen=True, slots=True)
class AIChunk:
    workspace_project_id: int
    source_kind: str
    source_name: str
    source_ref: str
    content: str
    checksum: str = ""
    score: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ContextBundle:
    workspace_project_id: int
    query: str
    chunks: tuple[AIChunk, ...] = ()

    @property
    def citations(self) -> tuple[str, ...]:
        return tuple(chunk.source_ref for chunk in self.chunks if chunk.source_ref)


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    parameters_schema: dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}, "additionalProperties": True})


@dataclass(frozen=True, slots=True)
class ToolChoice:
    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    depends_on: tuple[str, ...] = ()


class AIContextPort(Protocol):
    def retrieve(
        self,
        workspace_project_id: int,
        query: str,
        *,
        domains: Sequence[str] | None = None,
        top_k: int = 8,
    ) -> ContextBundle: ...


class AIEmbeddingPort(Protocol):
    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class AIToolCallingPort(Protocol):
    def choose_tools(
        self,
        workspace_project_id: int,
        objective: str,
        tools: Sequence[ToolDefinition],
        *,
        context: dict[str, Any] | None = None,
        max_steps: int = 24,
    ) -> list[ToolChoice]: ...


class AITelemetryPort(Protocol):
    def record(self, event: dict[str, Any]) -> None: ...


__all__ = [
    "AIChunk",
    "ContextBundle",
    "ToolDefinition",
    "ToolChoice",
    "AIContextPort",
    "AIEmbeddingPort",
    "AIToolCallingPort",
    "AITelemetryPort",
]
