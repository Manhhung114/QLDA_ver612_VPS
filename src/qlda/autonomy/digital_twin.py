from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(frozen=True, slots=True)
class TwinState:
    project_id: int
    schedule_progress: float = 0.0
    production_progress: float = 0.0
    cost_progress: float = 0.0
    quality_open_items: int = 0
    safety_open_items: int = 0
    cash_exposure: float = 0.0
    data_integrity_score: float = 100.0
    dimensions: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ScenarioResult:
    name: str
    projected_schedule_progress: float
    projected_cost_progress: float
    risk_score: float
    notes: tuple[str, ...] = ()


class ProjectDigitalTwin:
    """V9.0 lightweight project-state twin and scenario engine.

    The core is deterministic and model-agnostic. A learned predictor may be
    injected later, but baseline what-if calculations remain explainable.
    """

    def __init__(self, predictor: Callable[[TwinState, dict[str, Any]], ScenarioResult] | None = None) -> None:
        self._predictor = predictor
        self._states: dict[int, TwinState] = {}

    def update(self, state: TwinState) -> TwinState:
        self._states[int(state.project_id)] = state
        return state

    def get(self, project_id: int) -> TwinState | None:
        return self._states.get(int(project_id))

    def simulate(self, project_id: int, scenario: dict[str, Any]) -> ScenarioResult:
        state = self.get(project_id)
        if state is None:
            raise KeyError(f"Chưa có Digital Twin state cho project {project_id}.")
        if self._predictor is not None:
            return self._predictor(state, dict(scenario))

        productivity = float(scenario.get("productivity_multiplier", 1.0) or 1.0)
        days = max(0.0, float(scenario.get("days", 7.0) or 0.0))
        baseline_daily = float(scenario.get("baseline_daily_progress", 0.35) or 0.0)
        cost_daily = float(scenario.get("baseline_daily_cost_progress", 0.30) or 0.0)
        added_risk = float(scenario.get("added_risk", 0.0) or 0.0)

        projected_schedule = min(100.0, state.schedule_progress + days * baseline_daily * productivity)
        projected_cost = min(100.0, state.cost_progress + days * cost_daily * max(0.5, productivity))
        schedule_gap = max(0.0, projected_cost - projected_schedule)
        integrity_penalty = max(0.0, 100.0 - state.data_integrity_score) * 0.35
        quality_penalty = min(25.0, state.quality_open_items * 1.5)
        risk = min(100.0, schedule_gap * 1.5 + integrity_penalty + quality_penalty + added_risk)

        notes: list[str] = []
        if schedule_gap > 5:
            notes.append("Chi phí chạy nhanh hơn tiến độ; cần kiểm tra hiệu quả nguồn lực.")
        if state.data_integrity_score < 100:
            notes.append("Kết quả scenario bị giảm độ tin cậy do dữ liệu chưa đối soát 100%.")
        if productivity > 1.2:
            notes.append("Scenario giả định tăng năng suất đáng kể; cần xác minh nguồn lực thực tế.")

        return ScenarioResult(
            name=str(scenario.get("name") or "what-if"),
            projected_schedule_progress=round(projected_schedule, 2),
            projected_cost_progress=round(projected_cost, 2),
            risk_score=round(risk, 2),
            notes=tuple(notes),
        )
