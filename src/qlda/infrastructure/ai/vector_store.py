from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Any, Callable, Sequence

from qlda.application.ai.ports import AIChunk, ContextBundle
from qlda.infrastructure.postgres import connect

EmbeddingFn = Callable[[Sequence[str]], list[list[float]]]


def _checksum(*parts: Any) -> str:
    raw = "|".join(str(x or "") for x in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _chunk_id(chunk: AIChunk, index: int = 0) -> str:
    source_ref = str(chunk.source_ref or f"chunk:{index}")
    return _checksum(int(chunk.workspace_project_id), chunk.source_kind, source_ref)


def _vector_literal(values: Sequence[float]) -> str:
    return "[" + ",".join(f"{float(x):.9g}" for x in values) + "]"


def _tokens(text: str) -> list[str]:
    return [
        x
        for x in re.findall(r"[\wÀ-ỹĐđ]+", str(text or "").lower(), flags=re.UNICODE)
        if len(x) >= 2
    ][:24]


class PostgresVectorContextStore:
    """Hybrid pgvector/lexical retrieval isolated by ``workspace_project_id``.

    Indexing is content-addressed: unchanged chunks are skipped before embedding,
    so periodic Contractor Data Hub syncs do not pay embedding cost again. pgvector
    remains optional; lexical retrieval stays available when extension/model access
    is unavailable.
    """

    def __init__(self, embed: EmbeddingFn | None = None) -> None:
        self.embed = embed
        self.enabled = str(os.environ.get("QLDA_AI_RAG_ENABLED", "1")).strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
        self._ready = False
        self._vector_ready = False

    def ensure_schema(self) -> None:
        if not self.enabled or self._ready:
            return
        with connect(autocommit=True) as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS qlda_ai_chunks (
                    chunk_id TEXT PRIMARY KEY,
                    workspace_project_id BIGINT NOT NULL,
                    source_kind TEXT NOT NULL,
                    source_name TEXT DEFAULT '',
                    source_ref TEXT NOT NULL,
                    domain TEXT DEFAULT '',
                    content TEXT NOT NULL,
                    checksum TEXT NOT NULL,
                    metadata_json TEXT DEFAULT '{}',
                    embedding_text TEXT DEFAULT '',
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )"""
            )
            try:
                conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
                conn.execute("ALTER TABLE qlda_ai_chunks ADD COLUMN IF NOT EXISTS embedding vector")
                self._vector_ready = True
            except Exception:
                self._vector_ready = False
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_qlda_ai_chunks_workspace_domain "
                "ON qlda_ai_chunks(workspace_project_id, domain)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_qlda_ai_chunks_workspace_updated "
                "ON qlda_ai_chunks(workspace_project_id, updated_at DESC)"
            )
        self._ready = True

    def _columns(self) -> set[str]:
        with connect() as conn:
            rows = conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name='qlda_ai_chunks'"
            ).fetchall()
        return {str(row["column_name"]) for row in rows}

    def _changed_chunks(self, chunks: Sequence[AIChunk]) -> list[tuple[str, str, AIChunk]]:
        prepared: list[tuple[str, str, AIChunk]] = []
        by_tenant: dict[int, list[str]] = {}
        checksums: dict[str, str] = {}
        for index, chunk in enumerate(chunks):
            tenant = int(chunk.workspace_project_id)
            content = str(chunk.content or "").strip()
            if tenant <= 0 or not content:
                continue
            cid = _chunk_id(chunk, index)
            source_ref = str(chunk.source_ref or f"chunk:{index}")
            checksum = str(chunk.checksum or _checksum(tenant, source_ref, content))
            prepared.append((cid, checksum, chunk))
            by_tenant.setdefault(tenant, []).append(cid)
            checksums[cid] = checksum

        existing: dict[str, str] = {}
        with connect() as conn:
            for tenant, ids in by_tenant.items():
                for start in range(0, len(ids), 500):
                    batch = ids[start : start + 500]
                    rows = conn.execute(
                        "SELECT chunk_id,checksum FROM qlda_ai_chunks "
                        "WHERE workspace_project_id=%s AND chunk_id=ANY(%s)",
                        (tenant, batch),
                    ).fetchall()
                    for row in rows:
                        existing[str(row["chunk_id"])] = str(row.get("checksum") or "")
        return [item for item in prepared if existing.get(item[0]) != item[1]]

    def upsert_chunks(self, chunks: Sequence[AIChunk], *, domain: str = "") -> int:
        if not self.enabled or not chunks:
            return 0
        self.ensure_schema()
        changed = self._changed_chunks(chunks)
        if not changed:
            return 0

        columns = self._columns()
        has_vector = self._vector_ready and "embedding" in columns
        vectors: list[list[float]] = []
        if has_vector and self.embed is not None:
            try:
                vectors = self.embed([str(item[2].content or "") for item in changed])
                if len(vectors) != len(changed):
                    vectors = []
            except Exception:
                vectors = []

        with connect() as conn:
            for index, (cid, checksum, chunk) in enumerate(changed):
                tenant = int(chunk.workspace_project_id)
                source_ref = str(chunk.source_ref or f"chunk:{index}")
                metadata = json.dumps(dict(chunk.metadata or {}), ensure_ascii=False, default=str)
                base = (
                    cid,
                    tenant,
                    str(chunk.source_kind or "TEXT"),
                    str(chunk.source_name or ""),
                    source_ref,
                    str(domain or chunk.metadata.get("domain") or ""),
                    str(chunk.content),
                    checksum,
                    metadata,
                )
                if has_vector and vectors and index < len(vectors):
                    conn.execute(
                        """INSERT INTO qlda_ai_chunks(
                            chunk_id,workspace_project_id,source_kind,source_name,source_ref,domain,
                            content,checksum,metadata_json,embedding,updated_at
                        ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::vector,NOW())
                        ON CONFLICT(chunk_id) DO UPDATE SET
                            workspace_project_id=EXCLUDED.workspace_project_id,
                            source_kind=EXCLUDED.source_kind,source_name=EXCLUDED.source_name,
                            source_ref=EXCLUDED.source_ref,domain=EXCLUDED.domain,content=EXCLUDED.content,
                            checksum=EXCLUDED.checksum,metadata_json=EXCLUDED.metadata_json,
                            embedding=EXCLUDED.embedding,updated_at=NOW()""",
                        base + (_vector_literal(vectors[index]),),
                    )
                else:
                    conn.execute(
                        """INSERT INTO qlda_ai_chunks(
                            chunk_id,workspace_project_id,source_kind,source_name,source_ref,domain,
                            content,checksum,metadata_json,updated_at
                        ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
                        ON CONFLICT(chunk_id) DO UPDATE SET
                            workspace_project_id=EXCLUDED.workspace_project_id,
                            source_kind=EXCLUDED.source_kind,source_name=EXCLUDED.source_name,
                            source_ref=EXCLUDED.source_ref,domain=EXCLUDED.domain,content=EXCLUDED.content,
                            checksum=EXCLUDED.checksum,metadata_json=EXCLUDED.metadata_json,updated_at=NOW()""",
                        base,
                    )
            conn.commit()
        return len(changed)

    def sync_contractor_data_hub(self, workspace_project_id: int, *, limit: int = 10000) -> int:
        """Index normalized Data Hub rows and prune only stale DATA_HUB-owned chunks."""
        tenant = int(workspace_project_id)
        if tenant <= 0:
            raise ValueError("workspace_project_id phải > 0")
        self.ensure_schema()
        with connect() as conn:
            try:
                rows = conn.execute(
                    """SELECT record_key,source_kind,source_name,source_id,external_path,worksheet,
                              record_type,record_ref,category,content,source_row,external_modified_time
                       FROM contractor_data_records
                       WHERE workspace_project_id=%s AND COALESCE(content,'')<>''
                       ORDER BY synced_at DESC LIMIT %s""",
                    (tenant, max(1, min(int(limit), 50000))),
                ).fetchall()
            except Exception:
                return 0

        chunks: list[AIChunk] = []
        active_ids: list[str] = []
        for row in rows:
            source_ref = "/".join(
                x
                for x in (
                    str(row.get("external_path") or row.get("source_name") or ""),
                    str(row.get("worksheet") or ""),
                    str(row.get("record_ref") or ""),
                )
                if x
            )
            chunk = AIChunk(
                workspace_project_id=tenant,
                source_kind=str(row.get("source_kind") or row.get("record_type") or "DATA_HUB"),
                source_name=str(row.get("source_name") or "Contractor Data Hub"),
                source_ref=source_ref or str(row.get("record_key") or ""),
                content=str(row.get("content") or ""),
                checksum=_checksum(
                    row.get("record_key"), row.get("content"), row.get("external_modified_time")
                ),
                metadata={
                    "domain": str(row.get("category") or "data_hub").lower(),
                    "owner": "DATA_HUB",
                    "source_id": str(row.get("source_id") or ""),
                    "record_key": str(row.get("record_key") or ""),
                    "record_type": str(row.get("record_type") or ""),
                    "source_row": int(row.get("source_row") or 0),
                },
            )
            chunks.append(chunk)
            active_ids.append(_chunk_id(chunk, len(chunks) - 1))

        changed = self.upsert_chunks(chunks)
        # Prune stale rows only for chunks explicitly tagged as DATA_HUB. Manual or
        # future native-document indexes in the same workspace are untouched.
        try:
            with connect() as conn:
                if active_ids:
                    conn.execute(
                        "DELETE FROM qlda_ai_chunks WHERE workspace_project_id=%s "
                        "AND metadata_json LIKE '%\"owner\": \"DATA_HUB\"%' AND NOT (chunk_id=ANY(%s))",
                        (tenant, active_ids),
                    )
                else:
                    conn.execute(
                        "DELETE FROM qlda_ai_chunks WHERE workspace_project_id=%s "
                        "AND metadata_json LIKE '%\"owner\": \"DATA_HUB\"%'",
                        (tenant,),
                    )
                conn.commit()
        except Exception:
            pass
        return changed

    def retrieve(
        self,
        workspace_project_id: int,
        query: str,
        *,
        domains: Sequence[str] | None = None,
        top_k: int = 8,
    ) -> ContextBundle:
        tenant = int(workspace_project_id)
        if tenant <= 0:
            raise ValueError("workspace_project_id phải > 0")
        if not self.enabled:
            return ContextBundle(tenant, str(query or ""), ())
        self.ensure_schema()
        wanted = [str(x).strip().lower() for x in (domains or []) if str(x).strip()]
        limit = max(1, min(int(top_k), 30))

        query_vector: list[float] | None = None
        if self._vector_ready and self.embed is not None and str(query or "").strip():
            try:
                embedded = self.embed([str(query)])
                query_vector = embedded[0] if embedded else None
            except Exception:
                query_vector = None

        rows = []
        if query_vector:
            try:
                with connect() as conn:
                    sql = (
                        "SELECT workspace_project_id,source_kind,source_name,source_ref,content,checksum,metadata_json,"
                        "1-(embedding <=> %s::vector) AS score FROM qlda_ai_chunks "
                        "WHERE workspace_project_id=%s AND embedding IS NOT NULL"
                    )
                    vector_params: list[Any] = [_vector_literal(query_vector), tenant]
                    if wanted:
                        sql += " AND LOWER(domain)=ANY(%s)"
                        vector_params.append(wanted)
                    sql += " ORDER BY embedding <=> %s::vector LIMIT %s"
                    vector_params.extend([_vector_literal(query_vector), limit])
                    rows = conn.execute(sql, vector_params).fetchall()
            except Exception:
                rows = []

        if not rows:
            terms = _tokens(query)
            with connect() as conn:
                sql = (
                    "SELECT workspace_project_id,source_kind,source_name,source_ref,content,checksum,metadata_json,"
                    "0.0 AS score FROM qlda_ai_chunks WHERE workspace_project_id=%s"
                )
                lexical_params: list[Any] = [tenant]
                if wanted:
                    sql += " AND LOWER(domain)=ANY(%s)"
                    lexical_params.append(wanted)
                if terms:
                    sql += " AND (" + " OR ".join("LOWER(content) LIKE %s" for _ in terms) + ")"
                    lexical_params.extend([f"%{term}%" for term in terms])
                sql += " ORDER BY updated_at DESC LIMIT %s"
                lexical_params.append(limit)
                rows = conn.execute(sql, lexical_params).fetchall()

        chunks: list[AIChunk] = []
        for row in rows:
            metadata: dict[str, Any] = {}
            try:
                metadata = json.loads(str(row.get("metadata_json") or "{}"))
            except Exception:
                pass
            chunks.append(
                AIChunk(
                    workspace_project_id=int(row.get("workspace_project_id") or 0),
                    source_kind=str(row.get("source_kind") or ""),
                    source_name=str(row.get("source_name") or ""),
                    source_ref=str(row.get("source_ref") or ""),
                    content=str(row.get("content") or ""),
                    checksum=str(row.get("checksum") or ""),
                    score=float(row.get("score") or 0.0),
                    metadata=metadata,
                )
            )
        return ContextBundle(tenant, str(query or ""), tuple(chunks))


__all__ = ["PostgresVectorContextStore"]
