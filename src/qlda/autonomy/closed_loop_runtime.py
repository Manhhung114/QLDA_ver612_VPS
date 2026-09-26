from __future__ import annotations

from threading import RLock
from typing import Any

from qlda.application.ai.tool_schemas import tool_parameters_schema

from .loop_engine import ClosedLoopEngine, ClosedLoopRepository
from .models import ActionMode, RiskLevel
from .runtime import get_autonomy_platform, get_autonomy_repository, run_project_supervisor


class RuntimeClosedLoopRepository(ClosedLoopRepository):
    """Production repository guard: missing rows stay empty/fail-closed."""

    def get_loop(self, *, project_id: int, loop_id: str) -> dict[str, Any]:
        row = super().get_loop(project_id=int(project_id), loop_id=str(loop_id))
        return row if str(row.get("loop_id") or "") else {}

    def latest_loop(self, *, project_id: int, open_only: bool = False) -> dict[str, Any]:
        row = super().latest_loop(project_id=int(project_id), open_only=bool(open_only))
        return row if str(row.get("loop_id") or "") else {}


class RuntimeClosedLoopEngine(ClosedLoopEngine):
    """Production Act gate with schema validation, dedupe and Data Integrity fail-close."""

    _INTEGRITY_RECOVERY_TOOLS = {"check_data_integrity", "sync_google_data", "generate_report"}

    def recommendation_schema(self, tool_name: str) -> dict[str, Any]:
        registered = self.platform.tools.get(str(tool_name))
        return tool_parameters_schema(str(tool_name), registered.spec.parameters_schema)

    @staticmethod
    def _type_names(field_schema: dict[str, Any]) -> tuple[str, ...]:
        raw = field_schema.get("type")
        if isinstance(raw, (list, tuple)):
            return tuple(str(value) for value in raw)
        return (str(raw),) if raw else ()

    @classmethod
    def _coerce_value(cls, name: str, value: Any, field_schema: dict[str, Any]) -> Any:
        types = cls._type_names(field_schema)
        if value is None:
            if "null" in types:
                return None
            return value
        target_types = tuple(item for item in types if item != "null")
        target = target_types[0] if target_types else ""
        try:
            if target == "integer":
                if isinstance(value, bool):
                    raise ValueError
                number = float(value)
                if not number.is_integer():
                    raise ValueError
                return int(number)
            if target == "number":
                if isinstance(value, bool):
                    raise ValueError
                return float(value)
            if target == "boolean":
                if isinstance(value, bool):
                    return value
                normalized = str(value).strip().lower()
                if normalized in {"true", "1", "yes", "y", "có"}:
                    return True
                if normalized in {"false", "0", "no", "n", "không"}:
                    return False
                raise ValueError
            if target == "string":
                return str(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Tham số {name} không đúng kiểu {target or 'theo schema'}") from exc
        return value

    @classmethod
    def _validate_field(cls, name: str, value: Any, field_schema: dict[str, Any]) -> Any:
        value = cls._coerce_value(name, value, field_schema)
        types = cls._type_names(field_schema)
        if value is None:
            if "null" in types:
                return None
            raise ValueError(f"Tham số {name} không được để trống")

        if "string" in types:
            text = str(value)
            min_length = field_schema.get("minLength")
            max_length = field_schema.get("maxLength")
            if min_length is not None and len(text) < int(min_length):
                raise ValueError(f"Tham số {name} phải có ít nhất {int(min_length)} ký tự")
            if max_length is not None and len(text) > int(max_length):
                raise ValueError(f"Tham số {name} tối đa {int(max_length)} ký tự")

        if "integer" in types or "number" in types:
            number = float(value)
            minimum = field_schema.get("minimum")
            maximum = field_schema.get("maximum")
            exclusive_minimum = field_schema.get("exclusiveMinimum")
            exclusive_maximum = field_schema.get("exclusiveMaximum")
            if minimum is not None and number < float(minimum):
                raise ValueError(f"Tham số {name} phải >= {minimum}")
            if maximum is not None and number > float(maximum):
                raise ValueError(f"Tham số {name} phải <= {maximum}")
            if exclusive_minimum is not None and number <= float(exclusive_minimum):
                raise ValueError(f"Tham số {name} phải > {exclusive_minimum}")
            if exclusive_maximum is not None and number >= float(exclusive_maximum):
                raise ValueError(f"Tham số {name} phải < {exclusive_maximum}")

        enum = field_schema.get("enum")
        if isinstance(enum, list) and enum and value not in enum:
            raise ValueError(f"Tham số {name} phải thuộc: {', '.join(str(x) for x in enum)}")
        return value

    def _normalize_arguments(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None,
        *,
        allow_missing: bool,
    ) -> tuple[dict[str, Any], list[str]]:
        schema = self.recommendation_schema(tool_name)
        properties = dict(schema.get("properties") or {})
        required = [str(item) for item in list(schema.get("required") or [])]
        incoming = dict(arguments or {})
        if schema.get("additionalProperties") is False:
            unknown = sorted(set(incoming) - set(properties))
            if unknown:
                raise ValueError("Tham số không được phép: " + ", ".join(unknown))

        normalized: dict[str, Any] = {}
        for name, value in incoming.items():
            field_schema = dict(properties.get(name) or {})
            if not field_schema and name not in properties:
                continue
            if value == "" and name not in required:
                continue
            normalized[name] = self._validate_field(name, value, field_schema)

        missing = [name for name in required if normalized.get(name) in (None, "")]
        if missing and not allow_missing:
            raise ValueError("Thiếu tham số bắt buộc: " + ", ".join(missing))
        return normalized, missing

    def set_recommendation_inputs(
        self,
        *,
        project_id: int,
        loop_id: str,
        step_id: str,
        inputs: dict[str, Any],
        actor: str,
        role: str = "admin",
    ) -> dict[str, Any]:
        """Persist human-supplied parameters using the canonical tool schema."""
        loop = self.repository.get_loop(project_id=int(project_id), loop_id=str(loop_id))
        if not loop:
            raise ValueError("Không tìm thấy closed loop trong workspace hiện tại")
        recommendations = [dict(item) for item in list(loop.get("recommendations") or [])]
        target = next((item for item in recommendations if str(item.get("step_id") or "") == str(step_id)), None)
        if target is None:
            raise ValueError("Không tìm thấy recommendation trong loop hiện tại")
        if str(target.get("status") or "") in {"SUCCESS", "ALREADY_EXECUTED", "REJECTED"}:
            raise ValueError("Recommendation đã kết thúc, không thể sửa tham số")

        tool_name = str(target.get("tool") or "")
        registered = self.platform.tools.get(tool_name)
        normalized_role = str(role or "read").lower()
        if normalized_role not in {str(x).lower() for x in registered.spec.allowed_roles}:
            raise PermissionError(f"Role {role} không được cấu hình tool {tool_name}")

        merged = dict(target.get("arguments") or {})
        merged.update(dict(inputs or {}))
        normalized, missing = self._normalize_arguments(tool_name, merged, allow_missing=True)
        target["arguments"] = normalized
        target["required_inputs"] = missing
        needs_approval = (
            registered.spec.mode == ActionMode.APPROVAL_REQUIRED
            or registered.spec.risk in {RiskLevel.HIGH, RiskLevel.CRITICAL}
        )
        if missing:
            target["status"] = "NEEDS_INPUT"
        elif needs_approval:
            target["status"] = "PENDING_APPROVAL"
        else:
            target["status"] = "READY"

        loop["recommendations"] = recommendations
        loop["current_stage"] = "RECOMMEND" if missing else ("APPROVE" if needs_approval else "ACT")
        self.repository.save_loop(loop)
        self.automation_repository.audit({
            "project_id": int(project_id),
            "plan_id": str(loop_id),
            "step_id": str(step_id),
            "tool": tool_name,
            "actor": str(actor),
            "role": normalized_role,
            "risk": registered.spec.risk.value,
            "mode": registered.spec.mode.value,
            "status": "INPUTS_UPDATED",
            "arguments": normalized,
        })
        return self.repository.get_loop(project_id=int(project_id), loop_id=str(loop_id))

    def reject_recommendation(
        self,
        *,
        project_id: int,
        loop_id: str,
        step_id: str,
        actor: str,
        note: str = "",
    ) -> dict[str, Any]:
        loop = self.repository.get_loop(project_id=int(project_id), loop_id=str(loop_id))
        if not loop:
            raise ValueError("Không tìm thấy closed loop trong workspace hiện tại")
        recommendations = [dict(item) for item in list(loop.get("recommendations") or [])]
        target = next((item for item in recommendations if str(item.get("step_id") or "") == str(step_id)), None)
        if target is None:
            raise ValueError("Không tìm thấy recommendation trong loop hiện tại")
        target["status"] = "REJECTED"
        target["rejected_by"] = str(actor)
        target["rejection_note"] = str(note or "")
        loop["recommendations"] = recommendations
        if not any(str(item.get("status") or "") == "PENDING_APPROVAL" for item in recommendations):
            loop["current_stage"] = "VERIFY"
        self.repository.save_loop(loop)
        return self.repository.get_loop(project_id=int(project_id), loop_id=str(loop_id))

    def execute_ready(
        self,
        *,
        project_id: int,
        loop_id: str,
        actor: str,
        role: str = "admin",
        dry_run: bool = True,
    ) -> dict[str, Any]:
        loop = self.repository.get_loop(project_id=int(project_id), loop_id=str(loop_id))
        if not loop:
            raise ValueError("Không tìm thấy closed loop trong workspace hiện tại")
        if str(loop.get("status") or "") == "CLOSED":
            return loop

        approved_steps = self.automation_repository.approved_steps(
            project_id=int(project_id), plan_id=str(loop_id)
        )
        actions = list(loop.get("actions") or [])
        already_executed = {
            str(item.get("step_id") or "")
            for item in actions
            if str(item.get("status") or "") == "SUCCESS"
        }
        integrity = dict((loop.get("sensed") or {}).get("integrity") or {})
        integrity_valid = bool(integrity.get("valid", False))
        recommendations = [dict(x) for x in list(loop.get("recommendations") or [])]

        for recommendation in recommendations:
            step_id = str(recommendation.get("step_id") or "")
            tool_name = str(recommendation.get("tool") or "")
            status = str(recommendation.get("status") or "")

            if step_id in already_executed:
                recommendation["status"] = "ALREADY_EXECUTED"
                continue
            if status in {"NEEDS_INPUT", "FORBIDDEN", "UNAVAILABLE", "ALREADY_EXECUTED", "REJECTED"}:
                continue

            try:
                normalized_args, missing = self._normalize_arguments(
                    tool_name,
                    dict(recommendation.get("arguments") or {}),
                    allow_missing=False,
                )
            except Exception as exc:
                recommendation["status"] = "NEEDS_INPUT"
                recommendation["input_error"] = str(exc)
                try:
                    _, missing = self._normalize_arguments(
                        tool_name,
                        dict(recommendation.get("arguments") or {}),
                        allow_missing=True,
                    )
                except Exception:
                    missing = list(recommendation.get("required_inputs") or [])
                recommendation["required_inputs"] = missing
                continue
            recommendation["arguments"] = normalized_args
            recommendation["required_inputs"] = []

            if not dry_run and not integrity_valid and tool_name not in self._INTEGRITY_RECOVERY_TOOLS:
                recommendation["status"] = "BLOCKED_DATA_INTEGRITY"
                actions.append({
                    "step_id": step_id,
                    "tool": tool_name,
                    "status": "BLOCKED_DATA_INTEGRITY",
                    "dry_run": False,
                    "approved": False,
                    "error": "AI_DATA_VALID=FALSE; action ghi dữ liệu bị chặn.",
                })
                continue

            requires_approval = bool(recommendation.get("requires_approval"))
            approved = step_id in approved_steps
            if requires_approval and not approved:
                self.automation_repository.request_approval(
                    project_id=int(project_id),
                    plan_id=str(loop_id),
                    step_id=step_id,
                    tool_name=tool_name,
                    requested_by=str(actor),
                )
                recommendation["status"] = "PENDING_APPROVAL"
                continue

            try:
                output = self.platform.tools.execute(
                    tool_name,
                    project_id=int(project_id),
                    actor=str(actor),
                    role=str(role),
                    arguments=normalized_args,
                    approved=approved,
                    dry_run=bool(dry_run),
                )
                run_status = "DRY_RUN" if dry_run else "SUCCESS"
                recommendation["status"] = run_status
                actions.append({
                    "step_id": step_id,
                    "tool": tool_name,
                    "status": run_status,
                    "dry_run": bool(dry_run),
                    "approved": bool(approved),
                    "output": output,
                })
            except Exception as exc:
                recommendation["status"] = "FAILED"
                actions.append({
                    "step_id": step_id,
                    "tool": tool_name,
                    "status": "FAILED",
                    "dry_run": bool(dry_run),
                    "approved": bool(approved),
                    "error": str(exc),
                })

        loop["recommendations"] = recommendations
        loop["actions"] = actions[-200:]
        statuses = {str(x.get("status") or "") for x in recommendations}
        if "NEEDS_INPUT" in statuses:
            loop["current_stage"] = "RECOMMEND"
        elif "PENDING_APPROVAL" in statuses:
            loop["current_stage"] = "APPROVE"
        elif "BLOCKED_DATA_INTEGRITY" in statuses:
            loop["current_stage"] = "SENSE"
        else:
            loop["current_stage"] = "VERIFY"
        loop["learning"] = self.repository.learning_stats(project_id=int(project_id))
        self.repository.save_loop(loop)
        return self.repository.get_loop(project_id=int(project_id), loop_id=str(loop_id))


_LOCK = RLock()
_ENGINES: dict[int, RuntimeClosedLoopEngine] = {}
_REPOSITORIES: dict[int, RuntimeClosedLoopRepository] = {}


def get_closed_loop_repository(db) -> RuntimeClosedLoopRepository:
    key = id(db)
    with _LOCK:
        repository = _REPOSITORIES.get(key)
        if repository is None:
            repository = RuntimeClosedLoopRepository(db.connect)
            repository.ensure_schema()
            _REPOSITORIES[key] = repository
        return repository


def get_closed_loop_engine(db) -> RuntimeClosedLoopEngine:
    key = id(db)
    with _LOCK:
        engine = _ENGINES.get(key)
        if engine is None:
            engine = RuntimeClosedLoopEngine(
                platform=get_autonomy_platform(db),
                automation_repository=get_autonomy_repository(db),
                repository=get_closed_loop_repository(db),
            )
            _ENGINES[key] = engine
        return engine


def run_closed_loop_cycle(
    db,
    project_id: int,
    *,
    actor: str = "AI Supervisor",
    role: str = "admin",
    execute: bool = False,
    dry_run: bool = True,
    extra_indicators: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one complete observation cycle for exactly one workspace.

    ``run_project_supervisor`` is the single Sense/Analyze/Recommend capture point.
    This function therefore reads that just-captured loop instead of capturing a
    second time. Execution remains opt-in and passes RBAC/Approval/Audit/Data-
    Integrity gates. ``dry_run`` defaults to True on purpose.
    """
    tenant_id = int(project_id)
    result = run_project_supervisor(
        db,
        tenant_id,
        actor=str(actor),
        extra_indicators=extra_indicators,
    )
    engine = get_closed_loop_engine(db)
    loop = get_closed_loop_repository(db).latest_loop(project_id=tenant_id)
    if not loop:
        # Fail-soft fallback for callers that supply an older Supervisor runtime.
        loop = engine.capture_supervisor_result(result, actor=str(actor), role=str(role))
    if execute and str(loop.get("status") or "") != "CLOSED":
        loop = engine.execute_ready(
            project_id=tenant_id,
            loop_id=str(loop.get("loop_id") or ""),
            actor=str(actor),
            role=str(role),
            dry_run=bool(dry_run),
        )
    return {
        "project_id": tenant_id,
        "supervisor": result,
        "loop": loop,
        "learning": engine.learning_summary(project_id=tenant_id),
    }


def latest_closed_loop(db, project_id: int) -> dict[str, Any]:
    return get_closed_loop_repository(db).latest_loop(project_id=int(project_id))


def add_closed_loop_feedback(
    db,
    project_id: int,
    loop_id: str,
    *,
    actor: str,
    rating: str,
    note: str = "",
    finding_code: str = "",
    tool_name: str = "",
) -> dict[str, Any]:
    return get_closed_loop_engine(db).add_feedback(
        project_id=int(project_id),
        loop_id=str(loop_id),
        actor=str(actor),
        rating=str(rating),
        note=str(note),
        finding_code=str(finding_code),
        tool_name=str(tool_name),
    )


__all__ = [
    "RuntimeClosedLoopEngine",
    "RuntimeClosedLoopRepository",
    "add_closed_loop_feedback",
    "get_closed_loop_engine",
    "get_closed_loop_repository",
    "latest_closed_loop",
    "run_closed_loop_cycle",
]
