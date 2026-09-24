from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ReleaseStage(str, Enum):
    V7_7 = "7.7"
    V7_8 = "7.8"
    V7_9 = "7.9"
    V8_0 = "8.0"
    V8_1 = "8.1"
    V8_2 = "8.2"
    V9_0 = "9.0"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ActionMode(str, Enum):
    READ_ONLY = "read_only"
    DRAFT = "draft"
    AUTO = "auto"
    APPROVAL_REQUIRED = "approval_required"


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    evidence_id: str
    source_kind: str
    source_name: str
    source_ref: str
    checksum: str = ""
    observed_at: str = field(default_factory=utcnow_iso)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class IntegrityReport:
    valid: bool
    score: float
    source_count: int
    stored_count: int
    normalized_count: int
    missing_count: int
    duplicate_count: int
    warnings: tuple[str, ...] = ()
    evidence: tuple[EvidenceRef, ...] = ()


@dataclass(frozen=True, slots=True)
class DomainEvent:
    event_type: str
    project_id: int
    payload: dict[str, Any] = field(default_factory=dict)
    workspace_project_id: int | None = None
    actor: str = "system"
    occurred_at: str = field(default_factory=utcnow_iso)
    event_id: str = ""


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    risk: RiskLevel = RiskLevel.LOW
    mode: ActionMode = ActionMode.READ_ONLY
    allowed_roles: tuple[str, ...] = ("admin", "update", "read")
    idempotent: bool = True


@dataclass(frozen=True, slots=True)
class PlanStep:
    step_id: str
    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    depends_on: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ExecutionPlan:
    project_id: int
    objective: str
    steps: tuple[PlanStep, ...]
    created_by: str = "AI"
    created_at: str = field(default_factory=utcnow_iso)
    plan_id: str = ""


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    step_id: str
    tool_name: str
    status: str
    output: Any = None
    error: str = ""
    approval_required: bool = False
    audit_id: str = ""


@dataclass(frozen=True, slots=True)
class HealthFinding:
    code: str
    title: str
    detail: str
    severity: RiskLevel
    metric: float | None = None
    threshold: float | None = None
    evidence: tuple[EvidenceRef, ...] = ()
    recommended_action: str = ""


@dataclass(frozen=True, slots=True)
class ProjectHealthReport:
    project_id: int
    score: float
    findings: tuple[HealthFinding, ...]
    generated_at: str = field(default_factory=utcnow_iso)
