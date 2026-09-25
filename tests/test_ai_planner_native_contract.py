from __future__ import annotations

import unittest
from pathlib import Path

from qlda.application.ai import ToolChoice
from qlda.application.ai.tool_schemas import tool_parameters_schema
from qlda.autonomy.ai_planner import StructuredAIPlanner
from qlda.autonomy.models import ActionMode, RiskLevel, ToolSpec


class _Telemetry:
    def __init__(self) -> None:
        self.events = []

    def record(self, event):
        self.events.append(dict(event))


class NativePlannerContractTests(unittest.TestCase):
    def test_canonical_schema_requires_business_arguments(self):
        task = tool_parameters_schema("create_work_task")
        progress = tool_parameters_schema("update_schedule_progress")
        self.assertIn("title", task["required"])
        self.assertEqual(task["additionalProperties"], False)
        self.assertEqual(set(progress["required"]), {"task_id", "actual_progress"})
        self.assertEqual(progress["properties"]["actual_progress"]["maximum"], 100)

    def test_native_choice_becomes_audited_plan_with_plan_id(self):
        telemetry = _Telemetry()
        spec = ToolSpec(
            "create_work_task",
            "create one task",
            RiskLevel.LOW,
            ActionMode.AUTO,
            ("admin", "update"),
        )

        def choose(project_id, objective, tools, context):
            self.assertEqual(project_id, 101)
            self.assertEqual(tools[0].parameters_schema["required"], ["title"])
            return [ToolChoice("create_work_task", {"title": "Kiểm tra NCR"}, "finding requires owner")]

        planner = StructuredAIPlanner(None, (spec,), tool_caller=choose, telemetry=telemetry)
        plan = planner.plan(101, "giao việc xử lý NCR", {"finding": "NCR_OVERDUE"})
        self.assertTrue(plan.plan_id.startswith("PLAN-"))
        self.assertEqual(plan.created_by, "AI Native Tool Planner")
        self.assertEqual(plan.steps[0].tool_name, "create_work_task")
        self.assertEqual(telemetry.events[0]["plan_id"], plan.plan_id)
        self.assertEqual(telemetry.events[0]["tool_name"], "create_work_task")

    def test_regex_json_extraction_cannot_return(self):
        source = (Path(__file__).resolve().parents[1] / "src/qlda/autonomy/ai_planner.py").read_text(encoding="utf-8")
        self.assertNotIn("_extract_json", source)
        self.assertNotIn("re.search", source)


if __name__ == "__main__":
    unittest.main()
