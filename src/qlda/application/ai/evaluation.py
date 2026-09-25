from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence

from qlda.application.ai.ports import ContextBundle, ToolChoice


@dataclass(frozen=True, slots=True)
class RetrievalEvalCase:
    """One deterministic retrieval benchmark case.

    ``expected_source_refs`` are source refs that should be present in the first
    ``k`` results. The metric intentionally evaluates retrieval/provenance without
    requiring a live LLM call so it is stable in CI.
    """

    workspace_project_id: int
    query: str
    expected_source_refs: tuple[str, ...]
    k: int = 8


@dataclass(frozen=True, slots=True)
class RetrievalEvalResult:
    recall_at_k: float
    precision_at_k: float
    source_coverage: float
    tenant_leakage_count: int
    retrieved_count: int
    expected_count: int

    @property
    def passed_tenant_isolation(self) -> bool:
        return self.tenant_leakage_count == 0


@dataclass(frozen=True, slots=True)
class PlannerEvalCase:
    objective: str
    expected_tools: tuple[str, ...] = ()
    forbidden_tools: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PlannerEvalResult:
    tool_recall: float
    forbidden_tool_count: int
    unknown_tool_count: int
    selected_tools: tuple[str, ...] = field(default_factory=tuple)

    @property
    def safe(self) -> bool:
        return self.forbidden_tool_count == 0 and self.unknown_tool_count == 0


def evaluate_retrieval(case: RetrievalEvalCase, bundle: ContextBundle) -> RetrievalEvalResult:
    """Score top-k retrieval plus the non-negotiable tenant-isolation invariant."""

    k = max(1, int(case.k))
    chunks = tuple(bundle.chunks[:k])
    expected = {str(x) for x in case.expected_source_refs if str(x)}
    retrieved = [str(chunk.source_ref or "") for chunk in chunks]
    retrieved_set = {x for x in retrieved if x}
    hits = expected & retrieved_set
    recall = len(hits) / len(expected) if expected else 1.0
    precision = len(hits) / len(retrieved_set) if retrieved_set else (1.0 if not expected else 0.0)
    leakage = sum(
        1 for chunk in chunks if int(chunk.workspace_project_id) != int(case.workspace_project_id)
    )
    coverage = sum(1 for ref in retrieved if ref) / len(chunks) if chunks else 1.0
    return RetrievalEvalResult(
        recall_at_k=recall,
        precision_at_k=precision,
        source_coverage=coverage,
        tenant_leakage_count=leakage,
        retrieved_count=len(chunks),
        expected_count=len(expected),
    )


def evaluate_planner(
    case: PlannerEvalCase,
    choices: Sequence[ToolChoice],
    *,
    registered_tools: Iterable[str],
) -> PlannerEvalResult:
    """Score tool selection while making unsafe/unknown selections explicit."""

    selected = tuple(str(choice.tool_name or "") for choice in choices if str(choice.tool_name or ""))
    registered = {str(x) for x in registered_tools}
    expected = {str(x) for x in case.expected_tools}
    forbidden = {str(x) for x in case.forbidden_tools}
    hit_count = len(expected & set(selected))
    recall = hit_count / len(expected) if expected else 1.0
    return PlannerEvalResult(
        tool_recall=recall,
        forbidden_tool_count=sum(1 for name in selected if name in forbidden),
        unknown_tool_count=sum(1 for name in selected if name not in registered),
        selected_tools=selected,
    )


__all__ = [
    "RetrievalEvalCase",
    "RetrievalEvalResult",
    "PlannerEvalCase",
    "PlannerEvalResult",
    "evaluate_retrieval",
    "evaluate_planner",
]
