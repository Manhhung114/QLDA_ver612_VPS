from __future__ import annotations

import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from qlda.autonomy.advanced_automation import AdvancedAutomation
from qlda.autonomy.qlda_adapters import QLDAAutomationAdapters
from qlda.runtime_core.autonomy_runtime import get_autonomy_platform, run_project_supervisor
from qlda.runtime_core.project_store import CloudDatabase


class V9AdvancedAutomationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = CloudDatabase(Path(self.tmp.name) / "qlda.db")
        today = date.today()
        self.project_id = self.db.add_project(
            "P-V96",
            "V9.6 Advanced",
            (today - timedelta(days=30)).isoformat(),
            (today + timedelta(days=120)).isoformat(),
            "PM",
            "",
        )
        self.task_id = self.db.add_task(
            self.project_id,
            {
                "wbs": "1.1",
                "name": "Lắp đặt cáp điện tầng 10",
                "responsible": "Electrical",
                "resource_names": "electrical.engineer@example.com",
                "start_date": (today - timedelta(days=20)).isoformat(),
                "end_date": (today + timedelta(days=20)).isoformat(),
                "duration": 40,
                "planned_progress": 60,
                "actual_progress": 20,
                "critical": 1,
                "source_type": "manual",
            },
        )
        self.adapters = QLDAAutomationAdapters(self.db)
        self.advanced = AdvancedAutomation(self.adapters)

        with self.db.connect() as connection:
            connection.execute(
                """INSERT INTO cost_budgets(
                project_id,task_ref,boq_item,quantity,unit,unit_price,budget_total,contractor,note
                ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    self.project_id,
                    "1.1",
                    "Ống DN100",
                    100.0,
                    "m",
                    1000.0,
                    100000.0,
                    "SIGMA",
                    "test",
                ),
            )
            connection.execute(
                """INSERT INTO documents(
                project_id,doc_type,code,subject,discipline,contractor,issuer,assignee,
                issue_date,due_date,status,priority,description,note
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    self.project_id,
                    "RFI",
                    "RFI-EL-01",
                    "RFI cable điện tầng 10",
                    "ELECTRICAL",
                    "SIGMA",
                    "Site",
                    "electrical.engineer@example.com",
                    date.today().isoformat(),
                    (date.today() + timedelta(days=2)).isoformat(),
                    "Mở",
                    "Cao",
                    "Clarify cable route",
                    "",
                ),
            )

    def tearDown(self):
        self.tmp.cleanup()

    def _seed_ipc(self) -> str:
        from qlda.runtime_core import ipc_claim

        claim_id = "claim-v96"
        with self.db.connect() as connection:
            ipc_claim._ensure_tables(connection)
            connection.execute(
                """INSERT INTO payment_claims(claim_id,project_id,claim_no,claim_code,updated_at)
                VALUES(?,?,?,?,?)""",
                (claim_id, self.project_id, "1", "IPC-01", "2026-09-25 08:00:00"),
            )
            connection.execute(
                """INSERT INTO payment_claim_items(
                claim_id,project_id,sheet_name,row_no,boq_item,contract_qty,unit,
                material_unit_price,labor_unit_price,cumulative_value,completion_ratio
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    claim_id,
                    self.project_id,
                    "GTHT",
                    10,
                    "Ống DN100",
                    120.0,
                    "m",
                    1200.0,
                    0.0,
                    130000.0,
                    110.0,
                ),
            )
        return claim_id

    def test_platform_registers_all_v91_v96_tools(self):
        platform = get_autonomy_platform(self.db, force_rebuild=True)
        names = {spec.name for spec in platform.tools.list_specs()}
        self.assertTrue(
            {
                "route_work_task",
                "audit_contract_obligations",
                "reconcile_ipc_boq",
                "draft_vo_from_change",
                "forecast_project_risk",
                "analyze_site_progress",
                "run_advanced_supervision",
            }.issubset(names)
        )
        self.assertNotIn("digital_twin", names)

    def test_v91_task_routing_is_tenant_scoped_and_duplicate_safe(self):
        first = self.advanced.route_work_task(
            project_id=self.project_id,
            actor="ai",
            finding_code="SCHEDULE_DELAY",
            title="Chậm lắp đặt cable điện",
            detail="Công tác electrical cần xử lý",
            severity="high",
            source_ref="TASK-1",
        )
        second = self.advanced.route_work_task(
            project_id=self.project_id,
            actor="ai",
            finding_code="SCHEDULE_DELAY",
            title="Chậm lắp đặt cable điện",
            detail="Công tác electrical cần xử lý",
            severity="high",
            source_ref="TASK-1",
        )
        self.assertEqual(first["fingerprint"], second["fingerprint"])
        self.assertEqual(first["discipline"], "ELECTRICAL")
        self.assertEqual(first["assignee_email"], "electrical.engineer@example.com")
        with self.db.connect() as connection:
            count = connection.execute(
                "SELECT COUNT(*) FROM qlda_ai_task_routes WHERE project_id=?",
                (self.project_id,),
            ).fetchone()[0]
        self.assertEqual(int(count), 1)

    def test_v92_contract_audit_builds_due_date_ledger(self):
        from qlda.runtime_core import contract_management as cm

        cm.ensure_schema(self.db)
        expired = date.today() - timedelta(days=5)
        upcoming = date.today() + timedelta(days=12)
        cm.create_contract_record(
            self.db,
            workspace_project_id=self.project_id,
            record_type="Hợp đồng",
            record_no="HD-01",
            title="Hợp đồng MEP",
            expiry_date=expired,
            actor={"email": "admin@example.com", "name": "Admin", "role": "admin"},
        )
        cm.create_contract_record(
            self.db,
            workspace_project_id=self.project_id,
            record_type="Phụ lục",
            record_no="PL-01",
            title="Phụ lục gia hạn",
            expiry_date=upcoming,
            actor={"email": "admin@example.com", "name": "Admin", "role": "admin"},
        )
        report = self.advanced.audit_contract_obligations(project_id=self.project_id, actor="ai")
        self.assertGreaterEqual(report["overdue_count"], 1)
        self.assertGreaterEqual(report["due_30d_count"], 1)
        self.assertTrue(any("HD-01" in str(x.get("source_ref")) for x in report["ledger"]))

    def test_v93_ipc_boq_reconciliation_flags_verified_mismatches(self):
        claim_id = self._seed_ipc()
        report = self.advanced.reconcile_ipc_boq(
            project_id=self.project_id,
            actor="ai",
            claim_id=claim_id,
        )
        codes = {x["flag_code"] for x in report["flags"]}
        self.assertIn("CONTRACT_QTY_MISMATCH", codes)
        self.assertIn("UNIT_PRICE_MISMATCH", codes)
        self.assertIn("CUMULATIVE_EXCEEDS_BOQ", codes)
        self.assertIn("COMPLETION_OVER_100", codes)
        self.assertGreater(report["high_flag_count"], 0)

    def test_v94_vo_generation_stays_draft_and_uses_boq_rate(self):
        draft = self.advanced.draft_vo_from_change(
            project_id=self.project_id,
            actor="qs@example.com",
            source_type="RFI",
            source_ref="RFI-001",
            item_name="Ống DN100",
            unit="m",
            old_qty=100,
            new_qty=120,
        )
        self.assertTrue(draft["draft"])
        self.assertEqual(draft["rate_source"], "BOQ")
        self.assertEqual(draft["estimated_amount"], 20000.0)
        with self.db.connect() as connection:
            saved = connection.execute(
                "SELECT COUNT(*) FROM qlda_vo_drafts WHERE project_id=?",
                (self.project_id,),
            ).fetchone()[0]
            variation_count = 0
            try:
                variation_count = connection.execute(
                    "SELECT COUNT(*) FROM variation_orders WHERE project_id=?",
                    (self.project_id,),
                ).fetchone()[0]
            except Exception:
                variation_count = 0
        self.assertEqual(int(saved), 1)
        self.assertEqual(int(variation_count), 0)

    def test_v95_forecast_is_velocity_based_and_has_no_payment_overdue_alarm(self):
        report = self.advanced.forecast_project_risk(
            project_id=self.project_id,
            actor="ai",
            horizon_days=60,
        )
        self.assertEqual(report["method"], "deterministic_task_velocity_v1")
        self.assertIn("risk_score", report)
        self.assertIn("confidence", report)
        self.assertNotIn("payment_overdue", report)
        self.assertNotIn("PAYMENT_OVERDUE", str(report))
        self.assertTrue(report["tasks"])

    def test_v96_site_vision_proposes_progress_without_writing_task(self):
        before = float(self.db.task(self.task_id)["actual_progress"])
        proposal = self.advanced.analyze_site_progress(
            project_id=self.project_id,
            actor="ai",
            task_id=self.task_id,
            object_label="FCU",
            planned_quantity=10,
            observed_quantity=5,
            confidence=0.92,
            evidence_ref="site-photo-001.jpg",
        )
        after = float(self.db.task(self.task_id)["actual_progress"])
        self.assertEqual(proposal["proposed_progress"], 50.0)
        self.assertTrue(proposal["approval_required_for_update"])
        self.assertEqual(before, after)

    def test_supervisor_merges_advanced_findings_without_twin_or_payment_alarm(self):
        self._seed_ipc()
        result = run_project_supervisor(self.db, self.project_id)
        codes = {x["code"] for x in result["findings"]}
        self.assertIn("IPC_BOQ_MISMATCH", codes)
        self.assertIn("advanced_data", result)
        self.assertIn("task_routes", result)
        self.assertNotIn("twin", result)
        self.assertNotIn("PAYMENT_OVERDUE", codes)


if __name__ == "__main__":
    unittest.main()
