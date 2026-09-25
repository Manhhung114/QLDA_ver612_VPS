from __future__ import annotations

import unittest
from pathlib import Path

from qlda.application.ai import AIChunk, AIContextService, ContextBundle, ToolChoice
from qlda.application.ai.tool_schemas import tool_parameters_schema
from qlda.autonomy.ai_planner import StructuredAIPlanner
from qlda.autonomy.models import ActionMode, ExecutionPlan, PlanStep, RiskLevel, ToolSpec


class _Retriever:
    def __init__(self, chunks):
        self.chunks = tuple(chunks)

    def retrieve(self, workspace_project_id, query, *, domains=None, top_k=8):
        del domains, top_k
        return ContextBundle(int(workspace_project_id), str(query), self.chunks)


class _Fallback:
    def plan(self, project_id, objective, context=None):
        del context
        return ExecutionPlan(
            project_id=int(project_id),
            objective=str(objective),
            steps=(PlanStep("S1", "check_data_integrity", {}, "deterministic fallback"),),
            created_by="TEST FALLBACK",
            plan_id="PLAN-FALLBACK",
        )


class _Telemetry:
    def __init__(self):
        self.rows = []

    def record(self, event):
        self.rows.append(dict(event))


class AIModernizationTests(unittest.TestCase):
    def test_context_service_rejects_cross_tenant_retrieval(self):
        service = AIContextService(
            _Retriever((AIChunk(202, "DOCUMENT", "NCR-B", "documents:2", "secret"),))
        )
        with self.assertRaises(RuntimeError):
            service.retrieve(101, "NCR")

    def test_grounded_context_preserves_provenance(self):
        chunk = AIChunk(101, "CONTRACT", "HD-01", "contract:1:page-47", "Điều 8.2: phản hồi 7 ngày")
        service = AIContextService(_Retriever((chunk,)))
        text, bundle = service.build_grounded_context(101, "hạn phản hồi")
        self.assertIn("[NGUỒN 1: contract:1:page-47]", text)
        self.assertEqual(bundle.citations, ("contract:1:page-47",))

    def test_native_planner_accepts_only_registered_tools_and_audits_plan_id(self):
        telemetry = _Telemetry()
        specs = (
            ToolSpec("check_data_integrity", "check", RiskLevel.LOW, ActionMode.READ_ONLY),
        )

        def caller(project_id, objective, tools, context):
            del project_id, objective, tools, context
            return (
                ToolChoice("direct_sql", {"sql": "DROP TABLE x"}, "unknown"),
                ToolChoice("check_data_integrity", {}, "safe"),
            )

        planner = StructuredAIPlanner(
            None,
            specs,
            fallback=_Fallback(),
            tool_caller=caller,
            telemetry=telemetry,
        )
        plan = planner.plan(101, "kiểm tra dữ liệu", {"scope": "tenant"})
        self.assertEqual([step.tool_name for step in plan.steps], ["check_data_integrity"])
        self.assertTrue(plan.plan_id.startswith("PLAN-"))
        rows = [row for row in telemetry.rows if row.get("event_type") == "PLANNER_DECISION"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].get("plan_id"), plan.plan_id)
        self.assertEqual(rows[0].get("workspace_project_id"), 101)

    def test_native_planner_failure_uses_deterministic_fallback_and_audits(self):
        telemetry = _Telemetry()

        def broken(*args, **kwargs):
            del args, kwargs
            raise RuntimeError("provider unavailable")

        planner = StructuredAIPlanner(
            None,
            (ToolSpec("check_data_integrity", "check"),),
            fallback=_Fallback(),
            tool_caller=broken,
            telemetry=telemetry,
        )
        plan = planner.plan(101, "kiểm tra")
        self.assertEqual(plan.created_by, "TEST FALLBACK")
        self.assertTrue(any(row.get("event_type") == "PLANNER_FALLBACK" for row in telemetry.rows))

    def test_high_value_tool_schemas_are_strict(self):
        progress = tool_parameters_schema("update_schedule_progress")
        self.assertEqual(set(progress.get("required") or []), {"task_id", "actual_progress"})
        self.assertFalse(progress.get("additionalProperties"))
        vo = tool_parameters_schema("draft_vo_from_change")
        self.assertIn("item_name", vo.get("properties") or {})
        vision = tool_parameters_schema("analyze_site_progress")
        self.assertIn("planned_quantity", vision.get("required") or [])

    def test_vector_store_source_keeps_tenant_filter_and_checksum_dedup(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "src/qlda/infrastructure/ai/vector_store.py").read_text(encoding="utf-8")
        self.assertIn("workspace_project_id=%s", source)
        self.assertIn("_changed_chunks", source)
        self.assertIn("if not changed", source)
        self.assertIn("checksum", source)

    def test_planner_source_has_no_regex_json_extractor(self):
        root = Path(__file__).resolve().parents[1]
        source = (root / "src/qlda/autonomy/ai_planner.py").read_text(encoding="utf-8")
        self.assertNotIn("_extract_json", source)
        self.assertNotIn("re.search(", source)
        self.assertIn("tool_caller", source)


if __name__ == "__main__":
    unittest.main()
