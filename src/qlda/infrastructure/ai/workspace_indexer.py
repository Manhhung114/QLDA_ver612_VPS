from __future__ import annotations

import hashlib
import json
import os
import time
from threading import Lock
from typing import Any, Iterable

from qlda.application.ai.ports import AIChunk
from qlda.infrastructure.postgres import connect


_LOCK = Lock()
_LAST_SYNC: dict[int, float] = {}

# Structured numeric facts remain authoritative in SQL/business engines. The RAG
# index intentionally focuses on narrative/identifying fields used to retrieve
# evidence, not on asking an LLM to recompute BOQ/IPC/VO totals.
_SOURCE_SPECS: dict[str, tuple[str, ...]] = {
    "projects": ("code", "name", "description", "start_date", "end_date", "status"),
    "tasks": (
        "wbs", "name", "description", "status", "start_date", "finish_date",
        "responsible", "resource_names", "discipline", "critical",
    ),
    "documents": (
        "doc_type", "code", "subject", "description", "discipline", "contractor",
        "issuer", "assignee", "issue_date", "due_date", "status", "response",
        "related_wbs",
    ),
    "drawings": (
        "code", "name", "title", "discipline", "revision", "status", "description",
        "issue_date",
    ),
    "payment_claims": (
        "claim_no", "claim_code", "title", "period", "status", "submitted_date",
        "approved_date", "payment_due_date", "note",
    ),
    "vo_claims": (
        "code", "title", "subject", "description", "status", "source_ref", "note",
    ),
}


def _checksum(*parts: Any) -> str:
    raw = "|".join(str(value or "") for value in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _table_columns(table: str) -> set[str]:
    with connect() as conn:
        rows = conn.execute(
            """SELECT column_name FROM information_schema.columns
            WHERE table_schema=current_schema() AND table_name=%s""",
            (table,),
        ).fetchall()
    return {str(row.get("column_name") or "") for row in rows}


def _existing_tables() -> set[str]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema=current_schema()"
        ).fetchall()
    return {str(row.get("table_name") or "") for row in rows}


def _rows(table: str, columns: Iterable[str], workspace_project_id: int, limit: int) -> list[dict[str, Any]]:
    available = _table_columns(table)
    selected = [name for name in columns if name in available]
    identity = [name for name in ("id", "project_id") if name in available]
    fields = list(dict.fromkeys(identity + selected))
    if not fields:
        return []
    quoted = ",".join(f'"{name}"' for name in fields)
    tenant = int(workspace_project_id)
    if table == "projects" and "id" in available:
        sql = f'SELECT {quoted} FROM "{table}" WHERE id=%s LIMIT 1'
        params = (tenant,)
    elif "project_id" in available:
        order = ' ORDER BY id DESC' if "id" in available else ""
        sql = f'SELECT {quoted} FROM "{table}" WHERE project_id=%s{order} LIMIT %s'
        params = (tenant, max(1, min(int(limit), 10000)))
    else:
        return []
    with connect() as conn:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _chunk_from_row(table: str, row: dict[str, Any], tenant: int) -> AIChunk | None:
    values: list[str] = []
    metadata: dict[str, Any] = {"domain": table, "table": table}
    for field in _SOURCE_SPECS[table]:
        value = row.get(field)
        if value in (None, ""):
            continue
        text = str(value).strip()
        if not text:
            continue
        values.append(f"{field}: {text}")
        if field in {"code", "claim_no", "claim_code", "wbs", "doc_type", "status", "discipline", "revision"}:
            metadata[field] = text
    if not values:
        return None
    row_id = row.get("id") or metadata.get("code") or metadata.get("claim_code") or metadata.get("wbs") or "row"
    label = str(
        metadata.get("code")
        or metadata.get("claim_code")
        or metadata.get("claim_no")
        or metadata.get("wbs")
        or row_id
    )
    source_ref = f"{table}:{row_id}:{label}"
    content = "\n".join(values)
    return AIChunk(
        workspace_project_id=tenant,
        source_kind=table.upper(),
        source_name=label,
        source_ref=source_ref,
        content=content,
        checksum=_checksum(tenant, table, row_id, content),
        metadata=metadata,
    )


def sync_workspace_sources(
    store,
    workspace_project_id: int,
    *,
    force: bool = False,
    limit_per_table: int | None = None,
) -> dict[str, int]:
    """Index Data Hub + operational narrative evidence for one contractor tenant.

    The operation is throttled in-process and fail-open. Chunks always carry a
    source_ref and workspace_project_id so retrieval can be audited and tenant
    leakage can be evaluated deterministically.
    """

    tenant = int(workspace_project_id)
    if tenant <= 0:
        raise ValueError("workspace_project_id phải > 0")
    interval = max(30, int(os.environ.get("QLDA_AI_RAG_SYNC_MIN_SECONDS", "300") or 300))
    now = time.monotonic()
    with _LOCK:
        last = float(_LAST_SYNC.get(tenant, 0.0) or 0.0)
        if not force and now - last < interval:
            return {"workspace_project_id": tenant, "data_hub": 0, "operational": 0, "skipped": 1}
        _LAST_SYNC[tenant] = now

    limit = max(1, min(int(limit_per_table or os.environ.get("QLDA_AI_RAG_OPERATIONAL_LIMIT", "2000") or 2000), 10000))
    data_hub = 0
    operational_chunks: list[AIChunk] = []
    try:
        data_hub = int(
            store.sync_contractor_data_hub(
                tenant,
                limit=int(os.environ.get("QLDA_AI_RAG_INDEX_LIMIT", "10000") or 10000),
            )
            or 0
        )
    except Exception:
        data_hub = 0

    try:
        tables = _existing_tables()
        for table in _SOURCE_SPECS:
            if table not in tables:
                continue
            try:
                for row in _rows(table, _SOURCE_SPECS[table], tenant, limit):
                    chunk = _chunk_from_row(table, row, tenant)
                    if chunk is not None:
                        operational_chunks.append(chunk)
            except Exception:
                continue
        indexed = int(store.upsert_chunks(operational_chunks) or 0) if operational_chunks else 0
    except Exception:
        indexed = 0

    return {
        "workspace_project_id": tenant,
        "data_hub": data_hub,
        "operational": indexed,
        "skipped": 0,
    }


__all__ = ["sync_workspace_sources"]
