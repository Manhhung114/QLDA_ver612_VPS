from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any

from qlda.infrastructure.postgres import connect


_SENSITIVE = ("key", "token", "password", "secret", "authorization", "cookie")


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if any(word in str(key).lower() for word in _SENSITIVE):
                out[str(key)] = "***"
            else:
                out[str(key)] = _redact(item)
        return out
    if isinstance(value, (list, tuple)):
        return [_redact(x) for x in value]
    text = str(value) if value is not None else ""
    return text[:12000]


def content_hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class PostgresAITelemetry:
    """Fail-open AI audit sink. Telemetry failure must never break project work."""

    def __init__(self) -> None:
        self.enabled = str(os.environ.get("QLDA_AI_TELEMETRY_ENABLED", "1")).strip().lower() in {"1", "true", "yes", "on"}
        self._ready = False

    def ensure_schema(self) -> None:
        if not self.enabled or self._ready:
            return
        with connect(autocommit=True) as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS qlda_ai_audit (
                    id BIGSERIAL PRIMARY KEY,
                    workspace_project_id BIGINT NOT NULL,
                    request_id TEXT DEFAULT '',
                    plan_id TEXT DEFAULT '',
                    event_type TEXT NOT NULL,
                    provider TEXT DEFAULT '',
                    model TEXT DEFAULT '',
                    input_hash TEXT DEFAULT '',
                    context_json TEXT DEFAULT '{}',
                    source_refs_json TEXT DEFAULT '[]',
                    tool_name TEXT DEFAULT '',
                    tool_arguments_json TEXT DEFAULT '{}',
                    decision_reason TEXT DEFAULT '',
                    latency_ms INTEGER DEFAULT 0,
                    input_tokens INTEGER DEFAULT 0,
                    output_tokens INTEGER DEFAULT 0,
                    estimated_cost_usd DOUBLE PRECISION DEFAULT 0,
                    fallback_used INTEGER DEFAULT 0,
                    success INTEGER DEFAULT 1,
                    error_code TEXT DEFAULT '',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )"""
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_qlda_ai_audit_workspace_time ON qlda_ai_audit(workspace_project_id, created_at DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_qlda_ai_audit_plan ON qlda_ai_audit(plan_id)"
            )
        self._ready = True

    def record(self, event: dict[str, Any]) -> None:
        if not self.enabled:
            return
        try:
            self.ensure_schema()
            tenant = int(event.get("workspace_project_id") or event.get("project_id") or 0)
            if tenant <= 0:
                return
            context = _redact(event.get("context") or {})
            tool_args = _redact(event.get("tool_arguments") or {})
            source_refs = list(event.get("source_refs") or [])[:100]
            with connect(autocommit=True) as conn:
                conn.execute(
                    """INSERT INTO qlda_ai_audit(
                        workspace_project_id,request_id,plan_id,event_type,provider,model,input_hash,
                        context_json,source_refs_json,tool_name,tool_arguments_json,decision_reason,
                        latency_ms,input_tokens,output_tokens,estimated_cost_usd,fallback_used,success,error_code,created_at
                    ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        tenant,
                        str(event.get("request_id") or ""),
                        str(event.get("plan_id") or ""),
                        str(event.get("event_type") or "AI_REQUEST"),
                        str(event.get("provider") or ""),
                        str(event.get("model") or ""),
                        str(event.get("input_hash") or content_hash(event.get("input") or "")),
                        json.dumps(context, ensure_ascii=False, default=str),
                        json.dumps(source_refs, ensure_ascii=False, default=str),
                        str(event.get("tool_name") or ""),
                        json.dumps(tool_args, ensure_ascii=False, default=str),
                        str(event.get("decision_reason") or "")[:4000],
                        int(event.get("latency_ms") or 0),
                        int(event.get("input_tokens") or 0),
                        int(event.get("output_tokens") or 0),
                        float(event.get("estimated_cost_usd") or 0.0),
                        1 if event.get("fallback_used") else 0,
                        1 if event.get("success", True) else 0,
                        str(event.get("error_code") or ""),
                        str(event.get("created_at") or _utcnow()),
                    ),
                )
        except Exception:
            return


_DEFAULT = PostgresAITelemetry()


def record_ai_event(event: dict[str, Any]) -> None:
    _DEFAULT.record(event)


__all__ = ["PostgresAITelemetry", "record_ai_event", "content_hash"]
