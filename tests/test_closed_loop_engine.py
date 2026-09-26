from __future__ import annotations

import sqlite3
import tempfile
import unittest
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


class ClosedLoopEngineTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_full_lifecycle_verify_and_learn(self) -> None:
        engine, _repository, _automation, calls = _engine(self.tmp_path)
        loop = engine.capture_supervisor_result(
            _result(1, health=70, finding="SCHEDULE_DELAY", tool="create_work_task"),
            actor="admin@example.com",
            role="admin",
        )
        self.assertEqual(loop["status"], "OPEN")
        self.assertEqual(loop["current_stage"], "RECOMMEND")
        self.assertEqual(loop["verification"]["outcome"], "BASELINE")
        self.assertEqual(loop["recommendations"][0]["status"], "READY")
        self.assertTrue(loop["recommendations"][0]["arguments"]["title"].startswith("[AI Supervisor]"))

        acted = engine.execute_ready(
            project_id=1,
            loop_id=loop["loop_id"],
            actor="admin@example.com",
            role="admin",
            dry_run=False,
        )
        self.assertEqual(acted["recommendations"][0]["status"], "SUCCESS")
        self.assertEqual(calls["create_work_task"], 1)

        deduped = engine.execute_ready(
            project_id=1,
            loop_id=loop["loop_id"],
            actor="admin@example.com",
            role="admin",
            dry_run=False,
        )
        self.assertEqual(deduped["recommendations"][0]["status"], "ALREADY_EXECUTED")
        self.assertEqual(calls["create_work_task"], 1)

        verified = engine.capture_supervisor_result(
            _result(1, health=100, finding=None),
            actor="admin@example.com",
            role="admin",
        )
        self.assertEqual(verified["loop_id"], loop["loop_id"])
        self.assertEqual(verified["status"], "CLOSED")
        self.assertEqual(verified["current_stage"], "CLOSED")
        self.assertEqual(verified["verification"]["outcome"], "RESOLVED")
        self.assertEqual(verified["verification"]["health_delta"], 30.0)
        self.assertIn("SCHEDULE_DELAY", verified["verification"]["resolved_findings"])

        learned = engine.add_feedback(
            project_id=1,
            loop_id=loop["loop_id"],
            actor="admin@example.com",
            rating="EFFECTIVE",
            note="Đã xử lý xong",
            tool_name="create_work_task",
        )
        self.assertEqual(learned["learning"]["feedback_total"], 1)
        summary = engine.learning_summary(project_id=1)
        self.assertEqual(summary["effectiveness_rate"], 100.0)
        self.assertEqual(summary["verification_outcomes"]["RESOLVED"], 1)

    def test_approval_reuses_existing_approval_repository(self) -> None:
        engine, _repository, automation, calls = _engine(self.tmp_path)
        loop = engine.capture_supervisor_result(
            _result(3, health=60, finding="HIGH_RISK", tool="high_guard"),
            actor="admin@example.com",
            role="admin",
        )
        self.assertEqual(loop["recommendations"][0]["status"], "PENDING_APPROVAL")

        pending_loop = engine.execute_ready(
            project_id=3,
            loop_id=loop["loop_id"],
            actor="admin@example.com",
            role="admin",
            dry_run=False,
        )
        self.assertEqual(pending_loop["current_stage"], "APPROVE")
        pending = automation.pending_approvals(project_id=3)
        self.assertEqual(len(pending), 1)
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
        self.assertEqual(acted["recommendations"][0]["status"], "SUCCESS")
        self.assertEqual(calls["high_guard"], 1)

    def test_actual_write_fails_closed_when_integrity_invalid(self) -> None:
        engine, _repository, _automation, calls = _engine(self.tmp_path)
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
        self.assertEqual(blocked["recommendations"][0]["status"], "BLOCKED_DATA_INTEGRITY")
        self.assertEqual(blocked["current_stage"], "SENSE")
        self.assertEqual(calls["writer"], 0)

        dry = engine.execute_ready(
            project_id=4,
            loop_id=loop["loop_id"],
            actor="admin@example.com",
            role="admin",
            dry_run=True,
        )
        self.assertEqual(dry["recommendations"][0]["status"], "DRY_RUN")
        self.assertEqual(calls["writer"], 0)

    def test_repository_is_workspace_scoped(self) -> None:
        engine, repository, _automation, _calls = _engine(self.tmp_path)
        loop = engine.capture_supervisor_result(
            _result(10, health=80, finding="RFI_OVERDUE"),
            actor="admin@example.com",
            role="admin",
        )
        self.assertEqual(repository.get_loop(project_id=10, loop_id=loop["loop_id"])["loop_id"], loop["loop_id"])
        self.assertEqual(repository.get_loop(project_id=11, loop_id=loop["loop_id"]), {})
        self.assertEqual(repository.latest_loop(project_id=11), {})

    def test_ai_supervisor_navigation_renders_closed_loop_panel(self) -> None:
        source = Path("src/qlda/presentation/streamlit/ai_supervisor_navigation.py").read_text(encoding="utf-8")
        panel = Path("src/qlda/presentation/streamlit/closed_loop_panel.py").read_text(encoding="utf-8")
        self.assertIn("render_closed_loop_panel", source)
        self.assertIn("Sense → Analyze → Recommend → Approve → Act → Verify → Learn", panel)
        self.assertIn("Dry-run trước khi thực thi", panel)
        self.assertNotIn("BLOCKED_DATA_INTEGRITY", panel)


if __name__ == "__main__":
    unittest.main()
