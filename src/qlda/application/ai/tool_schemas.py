from __future__ import annotations

from typing import Any


_EMPTY: dict[str, Any] = {"type": "object", "properties": {}, "additionalProperties": False}


def _object(properties: dict[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = list(required)
    return schema


TOOL_PARAMETER_SCHEMAS: dict[str, dict[str, Any]] = {
    "get_project_status": dict(_EMPTY),
    "check_data_integrity": dict(_EMPTY),
    "generate_report": dict(_EMPTY),
    "audit_contract_obligations": dict(_EMPTY),
    "run_advanced_supervision": dict(_EMPTY),
    "sync_google_data": _object({}),
    "create_work_task": _object(
        {
            "title": {"type": "string", "minLength": 1, "maxLength": 200},
            "description": {"type": "string"},
            "assignee_email": {"type": "string"},
            "assignee_name": {"type": "string"},
            "priority": {"type": "string"},
            "due_at": {"type": "string"},
            "source_type": {"type": "string"},
            "source_id": {"type": "string"},
            "source_code": {"type": "string"},
            "source_title": {"type": "string"},
        },
        ("title",),
    ),
    "draft_rfi": _object(
        {
            "code": {"type": "string"},
            "subject": {"type": "string", "minLength": 1},
            "discipline": {"type": "string"},
            "contractor": {"type": "string"},
            "issuer": {"type": "string"},
            "assignee": {"type": "string"},
            "due_date": {"type": "string"},
            "priority": {"type": "string"},
            "description": {"type": "string"},
            "related_wbs": {"type": "string"},
        },
        ("subject",),
    ),
    "draft_ncr": _object(
        {
            "code": {"type": "string"},
            "subject": {"type": "string", "minLength": 1},
            "discipline": {"type": "string"},
            "contractor": {"type": "string"},
            "issuer": {"type": "string"},
            "assignee": {"type": "string"},
            "due_date": {"type": "string"},
            "priority": {"type": "string"},
            "description": {"type": "string"},
            "related_wbs": {"type": "string"},
        },
        ("subject",),
    ),
    "update_schedule_progress": _object(
        {
            "task_id": {"type": "integer", "minimum": 1},
            "actual_progress": {"type": "integer", "minimum": 0, "maximum": 100},
        },
        ("task_id", "actual_progress"),
    ),
    "reconcile_ipc_boq": _object(
        {"claim_id": {"type": "string"}},
    ),
    "forecast_project_risk": _object(
        {"horizon_days": {"type": "integer", "minimum": 1, "maximum": 365}},
    ),
    "route_work_task": _object(
        {
            "finding_code": {"type": "string"},
            "title": {"type": "string", "minLength": 1},
            "detail": {"type": "string"},
            "severity": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
            "discipline": {"type": "string"},
            "source_ref": {"type": "string"},
            "create_task": {"type": "boolean"},
        },
        ("title",),
    ),
    "draft_vo_from_change": _object(
        {
            "source_type": {"type": "string"},
            "source_document_id": {"type": "integer", "minimum": 0},
            "source_ref": {"type": "string"},
            "item_name": {"type": "string", "minLength": 1},
            "unit": {"type": "string"},
            "old_qty": {"type": "number"},
            "new_qty": {"type": "number"},
            "unit_price": {"type": "number", "minimum": 0},
        },
        ("item_name", "old_qty", "new_qty"),
    ),
    "analyze_site_progress": _object(
        {
            "task_id": {"type": "integer", "minimum": 0},
            "object_label": {"type": "string"},
            "planned_quantity": {"type": "number", "exclusiveMinimum": 0},
            "observed_quantity": {"type": ["number", "null"], "minimum": 0},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "evidence_ref": {"type": "string"},
            "notes": {"type": "string"},
            "attachment_id": {"type": "integer", "minimum": 0},
        },
        ("planned_quantity",),
    ),
}


def tool_parameters_schema(tool_name: str, declared: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a defensive copy of the strongest known schema for a registered tool."""

    canonical = TOOL_PARAMETER_SCHEMAS.get(str(tool_name))
    if canonical is not None:
        return _deepcopy(canonical)
    if declared:
        return _deepcopy(declared)
    return dict(_EMPTY)


def _deepcopy(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _deepcopy(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_deepcopy(item) for item in value]
    if isinstance(value, tuple):
        return [_deepcopy(item) for item in value]
    return value


__all__ = ["TOOL_PARAMETER_SCHEMAS", "tool_parameters_schema"]
