from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from qlda.application.ai.ports import ContextBundle, ToolChoice


@dataclass(frozen=True, slots=True)
class RetrievalEvalCase:
    """One deterministic retrieval benchmark case."""

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


@dataclass(frozen=True, slots=True)
class AnswerEvalCase:
    """Deterministic grounded-answer benchmark without live LLM calls.

    Answers are expected to preserve the source-ref labels exposed by
    ``AIContextService`` (for example ``[NGUỒN 1: contract:7:page-12]``).
    ``required_source_refs`` measures citation recall. ``forbidden_phrases`` can
    lock known unsafe assertions such as approval language in golden fixtures.
    """

    required_source_refs: tuple[str, ...] = ()
    forbidden_phrases: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AnswerEvalResult:
    citation_recall: float
    cited_source_count: int
    unsupported_citation_count: int
    forbidden_phrase_count: int
    cited_source_refs: tuple[str, ...] = field(default_factory=tuple)

    @property
    def grounded(self) -> bool:
        return (
            self.citation_recall >= 1.0
            and self.unsupported_citation_count == 0
            and self.forbidden_phrase_count == 0
        )


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


def _answer_source_refs(answer: str) -> tuple[str, ...]:
    refs: list[str] = []
    for match in re.finditer(r"\[NGUỒN\s+\d+\s*:\s*([^\]]+)\]", str(answer or ""), flags=re.I):
        value = str(match.group(1) or "").strip()
        if value and value not in refs:
            refs.append(value)
    return tuple(refs)


def evaluate_answer(
    case: AnswerEvalCase,
    answer: str,
    *,
    available_source_refs: Iterable[str],
) -> AnswerEvalResult:
    """Evaluate citation grounding and simple safety assertions deterministically."""

    cited = _answer_source_refs(answer)
    available = {str(x) for x in available_source_refs if str(x)}
    required = {str(x) for x in case.required_source_refs if str(x)}
    hits = required & set(cited)
    recall = len(hits) / len(required) if required else 1.0
    unsupported = sum(1 for ref in cited if ref not in available)
    normalized = str(answer or "").casefold()
    forbidden_count = sum(
        1
        for phrase in case.forbidden_phrases
        if str(phrase or "").strip() and str(phrase).casefold() in normalized
    )
    return AnswerEvalResult(
        citation_recall=recall,
        cited_source_count=len(cited),
        unsupported_citation_count=unsupported,
        forbidden_phrase_count=forbidden_count,
        cited_source_refs=cited,
    )


__all__ = [
    "RetrievalEvalCase",
    "RetrievalEvalResult",
    "PlannerEvalCase",
    "PlannerEvalResult",
    "AnswerEvalCase",
    "AnswerEvalResult",
    "evaluate_retrieval",
    "evaluate_planner",
    "evaluate_answer",
]
