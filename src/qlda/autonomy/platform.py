from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .events import EventBus
from .integrity import DataIntegrityGate
from .models import ReleaseStage
from .orchestrator import AIOrchestrator
from .policy import AutonomyPolicy
from .services import ToolRegistry, default_tool_specs
from .supervisor import ProjectSupervisor


@dataclass(slots=True)
class AutomationPlatform:
    integrity: DataIntegrityGate
    tools: ToolRegistry
    events: EventBus
    orchestrator: AIOrchestrator
    supervisor: ProjectSupervisor
    autonomy_policy: AutonomyPolicy
    target_stage: ReleaseStage = ReleaseStage.V9_0

    @property
    def capabilities(self) -> dict[str, str]:
        return {
            "V7.7": "Data Integrity + Evidence",
            "V7.8": "Unified Service/Tool Layer",
            "V7.9": "Event & Background Automation",
            "V8.0": "AI Router/Planner/Executor + Approval Gate",
            "V8.1": "AI Project Supervisor",
            "V8.2": "Semi-Autonomous Policy",
            "V9.0": "Contractor-isolated Autonomous Project Operations",
        }


def build_platform(
    *,
    handlers: dict[str, Callable[..., Any]] | None = None,
    audit_sink: Callable[[dict[str, Any]], None] | None = None,
) -> AutomationPlatform:
    registry = ToolRegistry(audit_sink=audit_sink)
    handlers = dict(handlers or {})

    def missing_handler(name: str):
        def _handler(**kwargs):
            raise NotImplementedError(
                f"Tool '{name}' chưa được nối adapter nghiệp vụ. "
                "Không cho AI ghi trực tiếp DB; hãy đăng ký handler service tương ứng."
            )
        return _handler

    for spec in default_tool_specs():
        registry.register(spec, handlers.get(spec.name) or missing_handler(spec.name))

    return AutomationPlatform(
        integrity=DataIntegrityGate(),
        tools=registry,
        events=EventBus(),
        orchestrator=AIOrchestrator(registry),
        supervisor=ProjectSupervisor(),
        autonomy_policy=AutonomyPolicy(),
    )
