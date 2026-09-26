from __future__ import annotations

"""QLDA automation/autonomy platform from V7.7 through V9.0.

The package is intentionally transport- and database-independent. Production
adapters register existing QLDA services as tools; AI never writes storage
outside those services.
"""

from .events import DEFAULT_EVENT_TYPES, EventBus
from .integrity import DataIntegrityGate, evidence_id, make_evidence
from .loop_engine import LOOP_STAGES, ClosedLoopEngine, ClosedLoopRepository
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
from .persistence import AutomationRepository
from .platform import AutomationPlatform, build_platform
from .policy import AutonomyDecision, AutonomyPolicy
from .postgres_compat import install_closed_loop_postgres_compat
from .qlda_adapters import QLDAAutomationAdapters
from .scheduler import DailyAutomationScheduler, ScheduledJob, recommended_daily_schedule
from .services import ToolRegistry, default_tool_specs
from .supervisor import ProjectSupervisor

# Install once at package import so every runtime path (Streamlit, API, workers,
# tests) uses PostgreSQL-safe TEXT timestamp DML for Closed Loop persistence.
install_closed_loop_postgres_compat()

__all__ = [
    "ActionMode",
    "AIOrchestrator",
    "ApprovalPolicy",
    "AutonomyDecision",
    "AutonomyPolicy",
    "AutomationPlatform",
    "AutomationRepository",
    "ClosedLoopEngine",
    "ClosedLoopRepository",
    "DEFAULT_EVENT_TYPES",
    "DailyAutomationScheduler",
    "DataIntegrityGate",
    "DomainEvent",
    "EventBus",
    "ExecutionPlan",
    "ExecutionResult",
    "HealthFinding",
    "HeuristicPlanner",
    "IntegrityReport",
    "LOOP_STAGES",
    "PlanStep",
    "ProjectHealthReport",
    "ProjectSupervisor",
    "QLDAAutomationAdapters",
    "ReleaseStage",
    "RiskLevel",
    "ScheduledJob",
    "ToolRegistry",
    "ToolSpec",
    "build_platform",
    "default_tool_specs",
    "evidence_id",
    "make_evidence",
    "recommended_daily_schedule",
]
