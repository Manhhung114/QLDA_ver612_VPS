from __future__ import annotations

"""QLDA automation/autonomy platform from V7.7 through V9.0.

The package is intentionally transport- and database-independent. Production
adapters register existing QLDA services as tools; AI never writes storage
outside those services.
"""

from .digital_twin import ProjectDigitalTwin, ScenarioResult, TwinState
from .events import DEFAULT_EVENT_TYPES, EventBus
from .integrity import DataIntegrityGate, evidence_id, make_evidence
from .models import (
    ActionMode,
    DomainEvent,
    ExecutionPlan,
    ExecutionResult,
    HealthFinding,
    IntegrityReport,
    PlanStep,
    ProjectHealthReport,
    ReleaseStage,
    RiskLevel,
    ToolSpec,
)
from .orchestrator import AIOrchestrator, ApprovalPolicy, HeuristicPlanner
from .platform import AutomationPlatform, build_platform
from .policy import AutonomyDecision, AutonomyPolicy
from .services import ToolRegistry, default_tool_specs
from .supervisor import ProjectSupervisor

__all__ = [
    "ActionMode",
    "AIOrchestrator",
    "ApprovalPolicy",
    "AutonomyDecision",
    "AutonomyPolicy",
    "AutomationPlatform",
    "DEFAULT_EVENT_TYPES",
    "DataIntegrityGate",
    "DomainEvent",
    "EventBus",
    "ExecutionPlan",
    "ExecutionResult",
    "HealthFinding",
    "HeuristicPlanner",
    "IntegrityReport",
    "PlanStep",
    "ProjectDigitalTwin",
    "ProjectHealthReport",
    "ProjectSupervisor",
    "ReleaseStage",
    "RiskLevel",
    "ScenarioResult",
    "ToolRegistry",
    "ToolSpec",
    "TwinState",
    "build_platform",
    "default_tool_specs",
    "evidence_id",
    "make_evidence",
]
