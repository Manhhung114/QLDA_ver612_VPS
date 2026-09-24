from __future__ import annotations

import unittest

from qlda.autonomy import (
    ActionMode,
    DataIntegrityGate,
    DomainEvent,
    EventBus,
    ProjectSupervisor,
    RiskLevel,
    ToolRegistry,
    ToolSpec,
    build_platform,
)
from qlda.autonomy.orchestrator import AIOrchestrator
from qlda.autonomy.policy import AutonomyPolicy


class AutonomyPlatformTests(unittest.TestCase):
    def test_v77_integrity_blocks_missing_rows(self):
        gate = DataIntegrityGate()
        report = gate.reconcile(
            project_id=1,
            source_name="Google",
            source_rows=[{"source_row": 1}, {"source_row": 2}],
            stored_rows=[{"source_row": 1}],
            normalized_rows=[],
        )
        self.assertFalse(report.valid)
        self.assertEqual(report.missing_count, 1)
        with self.assertRaises(ValueError):
            gate.require_valid(report)

    def test_v78_tool_registry_enforces_approval(self):
        registry = ToolRegistry()
        registry.register(
            ToolSpec(
                "approve_ipc",
                "Approve IPC",
                RiskLevel.CRITICAL,
                ActionMode.APPROVAL_REQUIRED,
                ("admin",),
            ),
            lambda **kwargs: {"ok": True},
        )
        with self.assertRaises(PermissionError):
            registry.execute("approve_ipc", project_id=1, actor="A", role="admin")
        result = registry.execute("approve_ipc", project_id=1, actor="A", role="admin", approved=True)
        self.assertTrue(result["ok"])

    def test_v79_event_bus_is_replay_safe(self):
        bus = EventBus()
        seen = []
        bus.subscribe("PRODUCTION_CHANGED", lambda event: seen.append(event.event_id))
        event = DomainEvent("PRODUCTION_CHANGED", 1, {"delta": 1.0})
        event_id = bus.publish(event)
        self.assertEqual(bus.drain(), 1)
        self.assertEqual(bus.publish(event), event_id)
        self.assertEqual(bus.drain(), 0)
        self.assertEqual(len(seen), 1)

    def test_v80_orchestrator_stops_at_approval_gate(self):
        registry = ToolRegistry()
        registry.register(
            ToolSpec("get_project_status", "read", RiskLevel.LOW, ActionMode.READ_ONLY),
            lambda **kwargs: {"status": "ok"},
        )
        registry.register(
            ToolSpec("generate_report", "report", RiskLevel.LOW, ActionMode.AUTO),
            lambda **kwargs: {"report": "ok"},
        )
        orchestrator = AIOrchestrator(registry)
        plan = orchestrator.create_plan(1, "Đánh giá tình hình và lập báo cáo")
        results = orchestrator.execute_plan(plan, actor="AI", role="admin")
        self.assertTrue(results)
        self.assertTrue(all(x.status == "SUCCESS" for x in results))

    def test_v81_supervisor_flags_project_risks(self):
        report = ProjectSupervisor().evaluate(
            1,
            {
                "data_integrity_score": 100,
                "schedule_delay_percent": 12,
                "ncr_overdue": 2,
                "rfi_overdue": 1,
            },
        )
        self.assertLess(report.score, 100)
        codes = {x.code for x in report.findings}
        self.assertIn("SCHEDULE_DELAY", codes)
        self.assertIn("NCR_OVERDUE", codes)

    def test_v82_policy_requires_human_for_high_risk(self):
        spec = ToolSpec("approve_vo", "Approve VO", RiskLevel.CRITICAL, ActionMode.APPROVAL_REQUIRED, ("admin",))
        decision = AutonomyPolicy().decide(spec, role="admin", data_valid=True)
        self.assertTrue(decision.allowed)
        self.assertTrue(decision.requires_approval)

    def test_v90_keeps_autonomous_operations_without_digital_twin(self):
        platform = build_platform()
        self.assertIn("V9.0", platform.capabilities)
        self.assertIn("Autonomous Project Operations", platform.capabilities["V9.0"])
        self.assertFalse(hasattr(platform, "digital_twin"))


if __name__ == "__main__":
    unittest.main()