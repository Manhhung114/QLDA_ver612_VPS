from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from qlda.autonomy.persistence import AutomationRepository
from qlda.autonomy.qlda_adapters import QLDAAutomationAdapters
from qlda.runtime_core.autonomy_runtime import (
    SUPERVISOR_SCHEMA_VERSION,
    get_autonomy_platform,
    run_project_supervisor,
)
from qlda.runtime_core.project_store import CloudDatabase


class QLDAAutomationRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = CloudDatabase(Path(self.tmp.name) / "qlda.db")
        self.project_id = self.db.add_project(
            "P-AUTO",
            "Automation Test",
            "2026-09-01",
            "2026-12-31",
            "PM",
            "",
        )
        self.task_id = self.db.add_task(
            self.project_id,
            {
                "wbs": "1.1",
                "name": "MEP floor",
                "responsible": "MEP",
                "start_date": "2026-09-01",
                "end_date": "2026-10-01",
                "duration": 30,
                "planned_progress": 70,
                "actual_progress": 40,
                "source_type": "manual",
            },
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_native_status_and_schedule_update_reuse_project_store(self):
        adapters = QLDAAutomationAdapters(self.db)
        status = adapters.get_project_status(project_id=self.project_id, actor="ai")
        self.assertEqual(status["project"]["code"], "P-AUTO")
        self.assertEqual(status["schedule"]["task_count"], 1)
        self.assertEqual(status["schedule"]["actual_progress"], 40.0)

        updated = adapters.update_schedule_progress(
            project_id=self.project_id,
            actor="admin@example.com",
            task_id=self.task_id,
            actual_progress=55,
        )
        self.assertEqual(updated["actual_progress"], 55)
        self.assertEqual(int(self.db.task(self.task_id)["actual_progress"]), 55)

    def test_ai_document_generation_stays_draft_until_existing_workflow_is_used(self):
        adapters = QLDAAutomationAdapters(self.db)
        before = len(self.db.documents(self.project_id, "RFI"))
        draft = adapters.draft_rfi(
            project_id=self.project_id,
            actor="ai",
            subject="Clarify sleeve detail",
            assignee="Consultant",
        )
        after = len(self.db.documents(self.project_id, "RFI"))
        self.assertTrue(draft["draft"])
        self.assertEqual(before, after)
        self.assertIn("chưa", draft["note"].lower())

    def test_durable_repository_records_audit_and_approval(self):
        repo = AutomationRepository(self.db.connect)
        repo.ensure_schema()
        repo.audit({
            "project_id": self.project_id,
            "tool": "get_project_status",
            "actor": "ai",
            "role": "admin",
            "risk": "low",
            "mode": "read_only",
            "status": "SUCCESS",
            "arguments": {},
        })
        repo.request_approval(
            project_id=self.project_id,
            plan_id="P1",
            step_id="S1",
            tool_name="approve_ipc",
            requested_by="ai",
        )
        self.assertEqual(repo.approved_steps(project_id=self.project_id, plan_id="P1"), set())
        repo.decide_approval(
            project_id=self.project_id,
            plan_id="P1",
            step_id="S1",
            approved=True,
            approved_by="admin@example.com",
        )
        self.assertEqual(repo.approved_steps(project_id=self.project_id, plan_id="P1"), {"S1"})

    def test_payment_cumulative_is_not_summed_or_called_overdue_without_due_date(self):
        self.db.save_payment(
            self.project_id,
            {
                "payment_code": "IPC-01",
                "certified_cumulative": 100_000_000,
                "paid_amount": 30_000_000,
                "payment_status": "Đã duyệt",
            },
        )
        self.db.save_payment(
            self.project_id,
            {
                "payment_code": "IPC-02",
                "certified_cumulative": 150_000_000,
                "paid_amount": 20_000_000,
                "payment_status": "Đã duyệt",
            },
        )
        adapters = QLDAAutomationAdapters(self.db)
        status = adapters.get_project_status(project_id=self.project_id, actor="ai")
        payment = status["payment"]
        self.assertEqual(payment["certified"], 150_000_000)
        self.assertEqual(payment["paid"], 50_000_000)
        self.assertEqual(payment["overdue"], 0)
        self.assertFalse(payment["overdue_verified"])

        report = adapters.generate_report(project_id=self.project_id, actor="ai")
        self.assertNotIn("PAYMENT_OVERDUE", {x["code"] for x in report["findings"]})

    def test_runtime_supervisor_emits_findings_without_digital_twin(self):
        platform = get_autonomy_platform(self.db, force_rebuild=True)
        result = run_project_supervisor(
            self.db,
            self.project_id,
            extra_indicators={"ncr_overdue": 2, "production_progress": 72.0},
        )
        self.assertLess(result["health_score"], 100.0)
        self.assertIn("NCR_OVERDUE", {x["code"] for x in result["findings"]})
        self.assertEqual(result["supervisor_schema"], SUPERVISOR_SCHEMA_VERSION)
        self.assertNotIn("twin", result)
        self.assertFalse(hasattr(platform, "digital_twin"))

    def test_work_task_tool_uses_existing_work_task_service(self):
        adapters = QLDAAutomationAdapters(self.db)
        due = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
        row = adapters.create_work_task(
            project_id=self.project_id,
            actor="admin@example.com",
            title="Kiểm tra NCR",
            description="AI đề xuất sau supervisor",
            assignee_email="engineer@example.com",
            assignee_name="Engineer",
            priority="Quan trọng",
            due_at=due,
        )
        self.assertEqual(row["title"], "Kiểm tra NCR")
        self.assertEqual(row["source_module"], "AI_AUTOMATION")


if __name__ == "__main__":
    unittest.main()