from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

from qlda.autonomy.closed_loop_runtime import RuntimeClosedLoopEngine, RuntimeClosedLoopRepository
from qlda.autonomy.models import ActionMode, RiskLevel, ToolSpec
from qlda.autonomy.persistence import AutomationRepository
from qlda.autonomy.services import ToolRegistry


def _connect_factory(path: Path):
    def connect():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        return conn

    return connect


def _engine(tmp_path: Path):
    connect = _connect_factory(tmp_path / "closed-loop.sqlite")
    automation = AutomationRepository(connect)
    automation.ensure_schema()
    repository = RuntimeClosedLoopRepository(connect)
    repository.ensure_schema()
    calls: dict[str, int] = {}
    registry = ToolRegistry(audit_sink=automation.audit)

    def register(name: str, risk=RiskLevel.LOW, mode=ActionMode.AUTO, schema=None):
        calls[name] = 0

        def handler(*, project_id: int, actor: str = "", **kwargs):
            calls[name] += 1
            return {"ok": True, "project_id": project_id, "actor": actor, "arguments": kwargs}

        registry.register(
            ToolSpec(
                name,
                f"test {name}",
                risk,
                mode,
                ("admin", "update"),
                True,
                schema or {"type": "object", "properties": {}, "additionalProperties": False},
            ),
            handler,
        )

    register("create_work_task")
    register("safe_check")
    register("writer")
    register("high_guard", RiskLevel.HIGH, ActionMode.APPROVAL_REQUIRED)
    platform = SimpleNamespace(tools=registry)
    engine = RuntimeClosedLoopEngine(
        platform=platform,
        automation_repository=automation,
        repository=repository,
    )
    return engine, repository, automation, calls


def _result(project_id: int, *, health: float, finding: str | None, tool: str = "safe_check", valid: bool = True):
    findings = []
    proposed = []
    if finding:
        findings = [
            {
                "code": finding,
                "severity": "high",
                "title": f"Finding {finding}",
                "detail": f"Detail {finding}",
                "recommended_action": f"Handle {finding}",
            }
        ]
        proposed = [
            {
                "tool": tool,
                "finding": finding,
                "reason": f"Handle {finding}",
                "severity": "high",
            }
        ]
    return {
        "project_id": project_id,
        "workspace_project_id": project_id,
        "health_score": health,
        "findings": findings,
        "proposed_actions": proposed,
        "integrity": {"valid": valid, "score": 100 if valid else 50},
        "indicators": {"data_integrity_score": 100 if valid else 50},
        "local_day": "2026-09-26",
        "supervisor_schema": "TEST",
    }


def test_closed_loop_full_lifecycle_verify_and_learn(tmp_path: Path) -> None:
    engine, repository, _automation, calls = _engine(tmp_path)

    loop = engine.capture_supervisor_result(
        _result(1, health=70, finding="SCHEDULE_DELAY", tool="create_work_task"),
        actor="admin@example.com",
        role="admin",
    )
    assert loop["status"] == "OPEN"
    assert loop["current_stage"] == "RECOMMEND"
    assert loop["verification"]["outcome"] == "BASELINE"
    assert loop["recommendations"][0]["status"] == "READY"
    assert loop["recommendations"][0]["arguments"]["title"].startswith("[AI Supervisor]")

    acted = engine.execute_ready(
        project_id=1,
        loop_id=loop["loop_id"],
        actor="admin@example.com",
        role="admin",
        dry_run=False,
    )
    assert acted["recommendations"][0]["status"] == "SUCCESS"
    assert calls["create_work_task"] == 1

    deduped = engine.execute_ready(
        project_id=1,
        loop_id=loop["loop_id"],
        actor="admin@example.com",
        role="admin",
        dry_run=False,
    )
    assert deduped["recommendations"][0]["status"] == "ALREADY_EXECUTED"
    assert calls["create_work_task"] == 1

    verified = engine.capture_supervisor_result(
        _result(1, health=100, finding=None),
        actor="admin@example.com",
        role="admin",
    )
    assert verified["loop_id"] == loop["loop_id"]
    assert verified["status"] == "CLOSED"
    assert verified["current_stage"] == "CLOSED"
    assert verified["verification"]["outcome"] == "RESOLVED"
    assert verified["verification"]["health_delta"] == 30.0
    assert "SCHEDULE_DELAY" in verified["verification"]["resolved_findings"]

    learned = engine.add_feedback(
        project_id=1,
        loop_id=loop["loop_id"],
        actor="admin@example.com",
        rating="EFFECTIVE",
        note="Đã xử lý xong",
        tool_name="create_work_task",
    )
    assert learned["learning"]["feedback_total"] == 1
    summary = engine.learning_summary(project_id=1)
    assert summary["effectiveness_rate"] == 100.0
    assert summary["verification_outcomes"]["RESOLVED"] == 1


def test_closed_loop_approval_reuses_existing_approval_repository(tmp_path: Path) -> None:
    engine, _repository, automation, calls = _engine(tmp_path)
    loop = engine.capture_supervisor_result(
        _result(3, health=60, finding="HIGH_RISK", tool="high_guard"),
        actor="admin@example.com",
        role="admin",
    )
    assert loop["recommendations"][0]["status"] == "PENDING_APPROVAL"

    pending_loop = engine.execute_ready(
        project_id=3,
        loop_id=loop["loop_id"],
        actor="admin@example.com",
        role="admin",
        dry_run=False,
    )
    assert pending_loop["current_stage"] == "APPROVE"
    pending = automation.pending_approvals(project_id=3)
    assert len(pending) == 1
    step_id = str(pending[0]["step_id"])
    automation.decide_approval(
        project_id=3,
        plan_id=loop["loop_id"],
        step_id=step_id,
        approved=True,
        approved_by="director@example.com",
    )

    acted = engine.execute_ready(
        project_id=3,
        loop_id=loop["loop_id"],
        actor="admin@example.com",
        role="admin",
        dry_run=False,
    )
    assert acted["recommendations"][0]["status"] == "SUCCESS"
    assert calls["high_guard"] == 1


def test_closed_loop_actual_write_fails_closed_when_integrity_invalid(tmp_path: Path) -> None:
    engine, _repository, _automation, calls = _engine(tmp_path)
    loop = engine.capture_supervisor_result(
        _result(4, health=50, finding="BAD_DATA", tool="writer", valid=False),
        actor="admin@example.com",
        role="admin",
    )
    blocked = engine.execute_ready(
        project_id=4,
        loop_id=loop["loop_id"],
        actor="admin@example.com",
        role="admin",
        dry_run=False,
    )
    assert blocked["recommendations"][0]["status"] == "BLOCKED_DATA_INTEGRITY"
    assert blocked["current_stage"] == "SENSE"
    assert calls["writer"] == 0

    dry = engine.execute_ready(
        project_id=4,
        loop_id=loop["loop_id"],
        actor="admin@example.com",
        role="admin",
        dry_run=True,
    )
    assert dry["recommendations"][0]["status"] == "DRY_RUN"
    assert calls["writer"] == 0


def test_closed_loop_repository_is_workspace_scoped(tmp_path: Path) -> None:
    engine, repository, _automation, _calls = _engine(tmp_path)
    loop = engine.capture_supervisor_result(
        _result(10, health=80, finding="RFI_OVERDUE"),
        actor="admin@example.com",
        role="admin",
    )
    assert repository.get_loop(project_id=10, loop_id=loop["loop_id"])["loop_id"] == loop["loop_id"]
    assert repository.get_loop(project_id=11, loop_id=loop["loop_id"]) == {}
    assert repository.latest_loop(project_id=11) == {}


def test_ai_supervisor_navigation_renders_closed_loop_panel() -> None:
    source = Path("src/qlda/presentation/streamlit/ai_supervisor_navigation.py").read_text(encoding="utf-8")
    panel = Path("src/qlda/presentation/streamlit/closed_loop_panel.py").read_text(encoding="utf-8")
    assert "render_closed_loop_panel" in source
    assert "Sense → Analyze → Recommend → Approve → Act → Verify → Learn" in panel
    assert "Dry-run trước khi thực thi" in panel
    assert "BLOCKED_DATA_INTEGRITY" not in panel  # policy is enforced in the runtime, not presentation.
