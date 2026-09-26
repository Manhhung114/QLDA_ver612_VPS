from __future__ import annotations

import hashlib
import json
import uuid
from contextlib import contextmanager
from typing import Any, Callable, Iterator

from qlda.application.ai.tool_schemas import tool_parameters_schema

from .models import ActionMode, RiskLevel


LOOP_STAGES = ("SENSE", "ANALYZE", "RECOMMEND", "APPROVE", "ACT", "VERIFY", "LEARN", "CLOSED")
_FEEDBACK_VALUES = {"EFFECTIVE", "NEUTRAL", "INEFFECTIVE"}

_LOOP_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS qlda_engineering_loops (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        loop_id TEXT NOT NULL UNIQUE,
        project_id INTEGER NOT NULL,
        workspace_project_id INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'OPEN',
        current_stage TEXT NOT NULL DEFAULT 'SENSE',
        actor TEXT NOT NULL DEFAULT 'system',
        baseline_health REAL NOT NULL DEFAULT 0,
        latest_health REAL NOT NULL DEFAULT 0,
        cycle_count INTEGER NOT NULL DEFAULT 1,
        sensed_json TEXT NOT NULL DEFAULT '{}',
        analysis_json TEXT NOT NULL DEFAULT '{}',
        recommendations_json TEXT NOT NULL DEFAULT '[]',
        actions_json TEXT NOT NULL DEFAULT '[]',
        verification_json TEXT NOT NULL DEFAULT '{}',
        learning_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        closed_at TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS qlda_loop_feedback (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        loop_id TEXT NOT NULL,
        project_id INTEGER NOT NULL,
        finding_code TEXT NOT NULL DEFAULT '',
        tool_name TEXT NOT NULL DEFAULT '',
        rating TEXT NOT NULL,
        note TEXT NOT NULL DEFAULT '',
        actor TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )""",
    "CREATE INDEX IF NOT EXISTS idx_qlda_loop_project ON qlda_engineering_loops(project_id,status,updated_at)",
    "CREATE INDEX IF NOT EXISTS idx_qlda_loop_feedback_project ON qlda_loop_feedback(project_id,tool_name,created_at)",
)

_JSON_FIELDS = {
    "sensed_json": ("sensed", {}),
    "analysis_json": ("analysis", {}),
    "recommendations_json": ("recommendations", []),
    "actions_json": ("actions", []),
    "verification_json": ("verification", {}),
    "learning_json": ("learning", {}),
}


def _rowdict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    try:
        return {str(k): row[k] for k in row.keys()}
    except Exception:
        try:
            return dict(row)
        except Exception:
            return {}


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _decode_loop(row: Any) -> dict[str, Any]:
    item = _rowdict(row)
    for column, (target, default) in _JSON_FIELDS.items():
        try:
            item[target] = json.loads(str(item.get(column) or _json(default)))
        except Exception:
            item[target] = default.copy() if isinstance(default, dict) else list(default)
    return item


def _finding_codes(result: dict[str, Any]) -> list[str]:
    return [str(x.get("code") or "") for x in list(result.get("findings") or []) if str(x.get("code") or "")]


def _finding_by_code(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(x.get("code") or ""): dict(x)
        for x in list(result.get("findings") or [])
        if str(x.get("code") or "")
    }


class ClosedLoopRepository:
    """Durable persistence for Sense→Analyze→Recommend→Approve→Act→Verify→Learn.

    This repository is deliberately separate from the legacy autonomy tables so
    rollout does not mutate existing automation records. All reads are scoped by
    project/workspace id; a loop can never be resolved by id alone for execution.
    """

    def __init__(self, connect: Callable[[], Any]) -> None:
        self._connect = connect

    @contextmanager
    def _conn(self) -> Iterator[Any]:
        resource = self._connect()
        if hasattr(resource, "__enter__"):
            with resource as conn:
                yield conn
            return
        try:
            yield resource
        finally:
            close = getattr(resource, "close", None)
            if callable(close):
                close()

    def ensure_schema(self) -> None:
        with self._conn() as conn:
            for ddl in _LOOP_SCHEMA:
                conn.execute(ddl)
            commit = getattr(conn, "commit", None)
            if callable(commit):
                commit()

    def save_loop(self, row: dict[str, Any]) -> str:
        self.ensure_schema()
        loop_id = str(row.get("loop_id") or "").strip()
        if not loop_id:
            raise ValueError("loop_id là bắt buộc")
        project_id = int(row.get("project_id") or 0)
        if project_id <= 0:
            raise ValueError("project_id không hợp lệ")
        workspace_id = int(row.get("workspace_project_id") or project_id)
        values = (
            project_id,
            workspace_id,
            str(row.get("status") or "OPEN"),
            str(row.get("current_stage") or "SENSE"),
            str(row.get("actor") or "system"),
            float(row.get("baseline_health") or 0),
            float(row.get("latest_health") or 0),
            max(1, int(row.get("cycle_count") or 1)),
            _json(row.get("sensed") or {}),
            _json(row.get("analysis") or {}),
            _json(row.get("recommendations") or []),
            _json(row.get("actions") or []),
            _json(row.get("verification") or {}),
            _json(row.get("learning") or {}),
        )
        with self._conn() as conn:
            existing = conn.execute(
                "SELECT id FROM qlda_engineering_loops WHERE loop_id=? AND project_id=? LIMIT 1",
                (loop_id, project_id),
            ).fetchone()
            if existing:
                conn.execute(
                    """UPDATE qlda_engineering_loops SET
                    workspace_project_id=?,status=?,current_stage=?,actor=?,baseline_health=?,latest_health=?,cycle_count=?,
                    sensed_json=?,analysis_json=?,recommendations_json=?,actions_json=?,verification_json=?,learning_json=?,
                    updated_at=CURRENT_TIMESTAMP,
                    closed_at=CASE WHEN ?='CLOSED' THEN COALESCE(closed_at,CURRENT_TIMESTAMP) ELSE NULL END
                    WHERE loop_id=? AND project_id=?""",
                    (
                        workspace_id,
                        values[2], values[3], values[4], values[5], values[6], values[7],
                        values[8], values[9], values[10], values[11], values[12], values[13],
                        values[2], loop_id, project_id,
                    ),
                )
            else:
                conn.execute(
                    """INSERT INTO qlda_engineering_loops
                    (loop_id,project_id,workspace_project_id,status,current_stage,actor,baseline_health,latest_health,cycle_count,
                    sensed_json,analysis_json,recommendations_json,actions_json,verification_json,learning_json,closed_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,CASE WHEN ?='CLOSED' THEN CURRENT_TIMESTAMP ELSE NULL END)""",
                    (
                        loop_id, project_id, workspace_id, values[2], values[3], values[4], values[5], values[6], values[7],
                        values[8], values[9], values[10], values[11], values[12], values[13], values[2],
                    ),
                )
            commit = getattr(conn, "commit", None)
            if callable(commit):
                commit()
        return loop_id

    def get_loop(self, *, project_id: int, loop_id: str) -> dict[str, Any]:
        self.ensure_schema()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM qlda_engineering_loops WHERE project_id=? AND loop_id=? LIMIT 1",
                (int(project_id), str(loop_id)),
            ).fetchone()
        return _decode_loop(row)

    def latest_loop(self, *, project_id: int, open_only: bool = False) -> dict[str, Any]:
        self.ensure_schema()
        suffix = " AND status='OPEN'" if open_only else ""
        with self._conn() as conn:
            row = conn.execute(
                f"SELECT * FROM qlda_engineering_loops WHERE project_id=?{suffix} ORDER BY updated_at DESC,id DESC LIMIT 1",
                (int(project_id),),
            ).fetchone()
        return _decode_loop(row)

    def list_loops(self, *, project_id: int, limit: int = 20) -> list[dict[str, Any]]:
        self.ensure_schema()
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM qlda_engineering_loops WHERE project_id=? ORDER BY updated_at DESC,id DESC LIMIT ?",
                (int(project_id), max(1, min(int(limit), 200))),
            ).fetchall()
        return [_decode_loop(row) for row in rows]

    def add_feedback(
        self,
        *,
        project_id: int,
        loop_id: str,
        actor: str,
        rating: str,
        note: str = "",
        finding_code: str = "",
        tool_name: str = "",
    ) -> int:
        self.ensure_schema()
        normalized = str(rating or "").strip().upper()
        if normalized not in _FEEDBACK_VALUES:
            raise ValueError("rating phải là EFFECTIVE, NEUTRAL hoặc INEFFECTIVE")
        if not self.get_loop(project_id=int(project_id), loop_id=str(loop_id)):
            raise ValueError("Không tìm thấy closed loop trong workspace hiện tại")
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO qlda_loop_feedback
                (loop_id,project_id,finding_code,tool_name,rating,note,actor)
                VALUES(?,?,?,?,?,?,?)""",
                (
                    str(loop_id), int(project_id), str(finding_code or ""), str(tool_name or ""),
                    normalized, str(note or ""), str(actor or "system"),
                ),
            )
            commit = getattr(conn, "commit", None)
            if callable(commit):
                commit()
            try:
                return int(cur.lastrowid or 0)
            except Exception:
                return 0

    def feedback(self, *, project_id: int, loop_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        self.ensure_schema()
        params: list[Any] = [int(project_id)]
        where = "project_id=?"
        if loop_id:
            where += " AND loop_id=?"
            params.append(str(loop_id))
        params.append(max(1, min(int(limit), 1000)))
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM qlda_loop_feedback WHERE {where} ORDER BY created_at DESC,id DESC LIMIT ?",
                tuple(params),
            ).fetchall()
        return [_rowdict(row) for row in rows]

    def learning_stats(self, *, project_id: int) -> dict[str, Any]:
        rows = self.feedback(project_id=int(project_id), limit=1000)
        by_tool: dict[str, dict[str, Any]] = {}
        counts = {"EFFECTIVE": 0, "NEUTRAL": 0, "INEFFECTIVE": 0}
        for row in rows:
            rating = str(row.get("rating") or "").upper()
            if rating in counts:
                counts[rating] += 1
            tool = str(row.get("tool_name") or "")
            if not tool:
                continue
            bucket = by_tool.setdefault(tool, {"total": 0, "effective": 0, "neutral": 0, "ineffective": 0})
            bucket["total"] += 1
            key = rating.lower()
            if key in bucket:
                bucket[key] += 1
        for bucket in by_tool.values():
            total = int(bucket.get("total") or 0)
            bucket["effectiveness_rate"] = round(100.0 * int(bucket.get("effective") or 0) / total, 1) if total else None
        total = len(rows)
        return {
            "feedback_total": total,
            "effective": counts["EFFECTIVE"],
            "neutral": counts["NEUTRAL"],
            "ineffective": counts["INEFFECTIVE"],
            "effectiveness_rate": round(100.0 * counts["EFFECTIVE"] / total, 1) if total else None,
            "by_tool": by_tool,
            "safety_note": "Learning chỉ xếp hạng hiệu quả; không tự hạ risk, bỏ approval hoặc thay đổi RBAC.",
        }


class ClosedLoopEngine:
    """Closed-loop project control over the existing guarded ToolRegistry.

    Sense/Analyze come from ProjectSupervisor. Recommend is deterministic from its
    findings. Approve/Act always reuse AutomationRepository + ToolRegistry. Verify
    compares the next real supervisor observation with the previous cycle. Learn
    summarizes verified outcomes and explicit human feedback only.
    """

    def __init__(self, *, platform: Any, automation_repository: Any, repository: ClosedLoopRepository) -> None:
        self.platform = platform
        self.automation_repository = automation_repository
        self.repository = repository
        self.repository.ensure_schema()

    @staticmethod
    def _recommendation_id(finding: str, tool: str) -> str:
        digest = hashlib.sha256(f"{finding}|{tool}".encode("utf-8")).hexdigest()[:12]
        return f"rec-{digest}"

    @staticmethod
    def _safe_arguments(action: dict[str, Any], finding: dict[str, Any]) -> dict[str, Any]:
        tool = str(action.get("tool") or "")
        if tool != "create_work_task":
            return {}
        severity = str(action.get("severity") or finding.get("severity") or "medium").lower()
        priority = "Khẩn cấp" if severity in {"critical", "high"} else "Cao" if severity == "medium" else "Bình thường"
        title = str(finding.get("title") or action.get("finding") or "Cảnh báo AI Supervisor").strip()
        return {
            "title": f"[AI Supervisor] {title}"[:200],
            "description": str(action.get("reason") or finding.get("recommended_action") or finding.get("detail") or ""),
            "priority": priority,
            "source_type": "AI_CLOSED_LOOP",
            "source_code": str(action.get("finding") or finding.get("code") or ""),
            "source_title": title,
        }

    def build_recommendations(self, result: dict[str, Any], *, role: str = "admin") -> list[dict[str, Any]]:
        findings = _finding_by_code(result)
        recommendations: list[dict[str, Any]] = []
        for action in list(result.get("proposed_actions") or []):
            tool_name = str(action.get("tool") or "").strip()
            finding_code = str(action.get("finding") or "").strip()
            if not tool_name:
                continue
            try:
                registered = self.platform.tools.get(tool_name)
            except Exception:
                recommendations.append({
                    "step_id": self._recommendation_id(finding_code, tool_name),
                    "finding": finding_code,
                    "tool": tool_name,
                    "reason": str(action.get("reason") or ""),
                    "severity": str(action.get("severity") or ""),
                    "status": "UNAVAILABLE",
                    "risk": "unknown",
                    "mode": "unknown",
                    "requires_approval": True,
                    "required_inputs": [],
                    "arguments": {},
                })
                continue
            spec = registered.spec
            schema = tool_parameters_schema(tool_name, spec.parameters_schema)
            required = [str(x) for x in list(schema.get("required") or [])]
            arguments = self._safe_arguments(action, findings.get(finding_code, {}))
            missing = [name for name in required if arguments.get(name) in (None, "")]
            normalized_role = str(role or "read").lower()
            allowed = normalized_role in {str(x).lower() for x in spec.allowed_roles}
            needs_approval = spec.mode == ActionMode.APPROVAL_REQUIRED or spec.risk in {RiskLevel.HIGH, RiskLevel.CRITICAL}
            status = "READY"
            if not allowed:
                status = "FORBIDDEN"
            elif missing:
                status = "NEEDS_INPUT"
            elif needs_approval:
                status = "PENDING_APPROVAL"
            recommendations.append({
                "step_id": self._recommendation_id(finding_code, tool_name),
                "finding": finding_code,
                "tool": tool_name,
                "reason": str(action.get("reason") or ""),
                "severity": str(action.get("severity") or ""),
                "status": status,
                "risk": spec.risk.value,
                "mode": spec.mode.value,
                "requires_approval": bool(needs_approval),
                "required_inputs": missing,
                "arguments": arguments,
            })
        return recommendations

    @staticmethod
    def _verification(previous_codes: list[str], current_codes: list[str], previous_health: float, current_health: float) -> dict[str, Any]:
        previous = set(previous_codes)
        current = set(current_codes)
        resolved = sorted(previous - current)
        persistent = sorted(previous & current)
        new = sorted(current - previous)
        delta = round(float(current_health) - float(previous_health), 1)
        if not current:
            outcome = "RESOLVED"
        elif delta > 0 or len(resolved) > len(new):
            outcome = "IMPROVED"
        elif delta < 0 or len(new) > len(resolved):
            outcome = "DEGRADED"
        else:
            outcome = "STABLE"
        return {
            "outcome": outcome,
            "health_delta": delta,
            "resolved_findings": resolved,
            "persistent_findings": persistent,
            "new_findings": new,
        }

    def capture_supervisor_result(self, result: dict[str, Any], *, actor: str = "AI Supervisor", role: str = "admin") -> dict[str, Any]:
        project_id = int(result.get("project_id") or result.get("workspace_project_id") or 0)
        if project_id <= 0:
            raise ValueError("Supervisor result thiếu project_id/workspace_project_id")
        workspace_id = int(result.get("workspace_project_id") or project_id)
        health = float(result.get("health_score") or 0)
        codes = _finding_codes(result)
        sensed = {
            "project_id": project_id,
            "workspace_project_id": workspace_id,
            "health_score": health,
            "integrity": result.get("integrity") or {},
            "indicators": result.get("indicators") or {},
            "local_day": result.get("local_day") or "",
            "supervisor_schema": result.get("supervisor_schema") or "",
        }
        analysis = {
            "finding_codes": codes,
            "findings": list(result.get("findings") or []),
            "health_score": health,
        }
        recommendations = self.build_recommendations(result, role=role)
        loop = self.repository.latest_loop(project_id=project_id, open_only=True)

        if not loop:
            loop = {
                "loop_id": f"loop-{project_id}-{uuid.uuid4().hex[:12]}",
                "project_id": project_id,
                "workspace_project_id": workspace_id,
                "status": "OPEN" if codes else "CLOSED",
                "current_stage": "RECOMMEND" if codes else "CLOSED",
                "actor": actor,
                "baseline_health": health,
                "latest_health": health,
                "cycle_count": 1,
                "sensed": sensed,
                "analysis": analysis,
                "recommendations": recommendations,
                "actions": [],
                "verification": {
                    "outcome": "BASELINE" if codes else "HEALTHY",
                    "health_delta": 0.0,
                    "resolved_findings": [],
                    "persistent_findings": codes,
                    "new_findings": [],
                },
                "learning": self.repository.learning_stats(project_id=project_id),
            }
        else:
            previous_analysis = dict(loop.get("analysis") or {})
            previous_codes = [str(x) for x in list(previous_analysis.get("finding_codes") or [])]
            previous_health = float(loop.get("latest_health") or loop.get("baseline_health") or 0)
            verification = self._verification(previous_codes, codes, previous_health, health)
            loop.update({
                "workspace_project_id": workspace_id,
                "status": "OPEN" if codes else "CLOSED",
                "current_stage": "RECOMMEND" if codes else "CLOSED",
                "actor": actor,
                "latest_health": health,
                "cycle_count": int(loop.get("cycle_count") or 1) + 1,
                "sensed": sensed,
                "analysis": analysis,
                "recommendations": recommendations,
                "verification": verification,
                "learning": self.repository.learning_stats(project_id=project_id),
            })
        self.repository.save_loop(loop)
        return self.repository.get_loop(project_id=project_id, loop_id=str(loop["loop_id"]))

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

        approved_steps = self.automation_repository.approved_steps(project_id=int(project_id), plan_id=str(loop_id))
        actions = list(loop.get("actions") or [])
        recommendations = [dict(x) for x in list(loop.get("recommendations") or [])]
        for recommendation in recommendations:
            step_id = str(recommendation.get("step_id") or "")
            tool_name = str(recommendation.get("tool") or "")
            status = str(recommendation.get("status") or "")
            if status in {"NEEDS_INPUT", "FORBIDDEN", "UNAVAILABLE"}:
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
                    arguments=dict(recommendation.get("arguments") or {}),
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
        pending = any(str(x.get("status") or "") == "PENDING_APPROVAL" for x in recommendations)
        loop["current_stage"] = "APPROVE" if pending else "VERIFY"
        loop["learning"] = self.repository.learning_stats(project_id=int(project_id))
        self.repository.save_loop(loop)
        return self.repository.get_loop(project_id=int(project_id), loop_id=str(loop_id))

    def add_feedback(
        self,
        *,
        project_id: int,
        loop_id: str,
        actor: str,
        rating: str,
        note: str = "",
        finding_code: str = "",
        tool_name: str = "",
    ) -> dict[str, Any]:
        self.repository.add_feedback(
            project_id=int(project_id), loop_id=str(loop_id), actor=str(actor), rating=str(rating), note=str(note),
            finding_code=str(finding_code), tool_name=str(tool_name),
        )
        loop = self.repository.get_loop(project_id=int(project_id), loop_id=str(loop_id))
        if loop:
            loop["learning"] = self.repository.learning_stats(project_id=int(project_id))
            if str(loop.get("status") or "") != "CLOSED":
                loop["current_stage"] = "LEARN"
            self.repository.save_loop(loop)
        return self.repository.get_loop(project_id=int(project_id), loop_id=str(loop_id))

    def learning_summary(self, *, project_id: int) -> dict[str, Any]:
        loops = self.repository.list_loops(project_id=int(project_id), limit=200)
        outcomes = {"RESOLVED": 0, "IMPROVED": 0, "STABLE": 0, "DEGRADED": 0}
        for loop in loops:
            outcome = str((loop.get("verification") or {}).get("outcome") or "")
            if outcome in outcomes:
                outcomes[outcome] += 1
        summary = self.repository.learning_stats(project_id=int(project_id))
        summary["verification_outcomes"] = outcomes
        summary["loop_count"] = len(loops)
        summary["closed_loops"] = sum(1 for x in loops if str(x.get("status") or "") == "CLOSED")
        return summary


__all__ = ["LOOP_STAGES", "ClosedLoopRepository", "ClosedLoopEngine"]
