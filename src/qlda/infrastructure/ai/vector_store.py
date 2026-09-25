from __future__ import annotations

import hashlib
import json
import math
import os
import re
from typing import Any, Callable, Sequence

from qlda.application.ai.ports import AIChunk, ContextBundle
from qlda.infrastructure.postgres import connect

EmbeddingFn = Callable[[Sequence[str]], list[list[float]]]


def _checksum(*parts: Any) -> str:
    raw = "|".join(str(x or "") for x in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _vector_literal(values: Sequence[float]) -> str:
    return "[" + ",".join(f"{float(x):.9g}" for x in values) + "]"


def _tokens(text: str) -> list[str]:
    return [x for x in re.findall(r"[\wÀ-ỹĐđ]+", str(text or "").lower(), flags=re.UNICODE) if len(x) >= 2][:24]


class PostgresVectorContextStore:
    """Hybrid pgvector/lexical retrieval isolated by workspace_project_id.

    Vector support is opportunistic: if the PostgreSQL pgvector extension cannot
    be created/used, the same API falls back to tenant-filtered lexical retrieval.
    This keeps production available while allowing pgvector to be enabled without
    another service.
    """

    def __init__(self, embed: EmbeddingFn | None = None) -> None:
        self.embed = embed
        self.enabled = str(os.environ.get("QLDA_AI_RAG_ENABLED", "1")).strip().lower() in {"1", "true", "yes", "on"}
        self._ready = False
        self._vector_ready = False

    def ensure_schema(self) -> None:
        if not self.enabled or self._ready:
            return
        with connect(autocommit=True) as conn:
            try:
                conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
                self._vector_ready = True
            except Exception:
                self._vector_ready = False
            vector_column = "embedding vector" if self._vector_ready else "embedding_text TEXT DEFAULT ''"
            conn.execute(
                f"""CREATE TABLE IF NOT EXISTS qlda_ai_chunks (
                    chunk_id TEXT PRIMARY KEY,
                    workspace_project_id BIGINT NOT NULL,
                    source_kind TEXT NOT NULL,
                    source_name TEXT DEFAULT '',
                    source_ref TEXT NOT NULL,
                    domain TEXT DEFAULT '',
                    content TEXT NOT NULL,
                    checksum TEXT NOT NULL,
                    metadata_json TEXT DEFAULT '{{}}',
                    {vector_column},
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )"""
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_qlda_ai_chunks_workspace_domain ON qlda_ai_chunks(workspace_project_id, domain)"
            )
        self._ready = True

    def _columns(self) -> set[str]:
        with connect() as conn:
            rows = conn.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name='qlda_ai_chunks'"
            ).fetchall()
        return {str(row["column_name"]) for row in rows}

    def upsert_chunks(self, chunks: Sequence[AIChunk], *, domain: str = "") -> int:
        if not self.enabled or not chunks:
            return 0
        self.ensure_schema()
        texts = [str(chunk.content or "") for chunk in chunks]
        vectors: list[list[float]] = []
        if self._vector_ready and self.embed is not None:
            try:
                vectors = self.embed(texts)
                if len(vectors) != len(chunks):
                    vectors = []
            except Exception:
                vectors = []

        columns = self._columns()
        has_vector = "embedding" in columns
        with connect() as conn:
            for index, chunk in enumerate(chunks):
                tenant = int(chunk.workspace_project_id)
                if tenant <= 0 or not str(chunk.content or "").strip():
                    continue
                source_ref = str(chunk.source_ref or f"chunk:{index}")
                checksum = str(chunk.checksum or _checksum(tenant, source_ref, chunk.content))
                chunk_id = _checksum(tenant, chunk.source_kind, source_ref)
                metadata = json.dumps(dict(chunk.metadata or {}), ensure_ascii=False, default=str)
                base = (
                    chunk_id,
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
                            source_name=EXCLUDED.source_name,domain=EXCLUDED.domain,content=EXCLUDED.content,
                            checksum=EXCLUDED.checksum,metadata_json=EXCLUDED.metadata_json,
                            embedding=EXCLUDED.embedding,updated_at=NOW()
                        WHERE qlda_ai_chunks.checksum<>EXCLUDED.checksum""",
                        base + (_vector_literal(vectors[index]),),
                    )
                else:
                    conn.execute(
                        """INSERT INTO qlda_ai_chunks(
                            chunk_id,workspace_project_id,source_kind,source_name,source_ref,domain,
                            content,checksum,metadata_json,updated_at
                        ) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW())
                        ON CONFLICT(chunk_id) DO UPDATE SET
                            source_name=EXCLUDED.source_name,domain=EXCLUDED.domain,content=EXCLUDED.content,
                            checksum=EXCLUDED.checksum,metadata_json=EXCLUDED.metadata_json,updated_at=NOW()
                        WHERE qlda_ai_chunks.checksum<>EXCLUDED.checksum""",
                        base,
                    )
            conn.commit()
        return len(chunks)

    def sync_contractor_data_hub(self, workspace_project_id: int, *, limit: int = 10000) -> int:
        """Index already-normalized Google/Drive records without reparsing source files."""
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
        for row in rows:
            source_ref = "/".join(
                x for x in (
                    str(row.get("external_path") or row.get("source_name") or ""),
                    str(row.get("worksheet") or ""),
                    str(row.get("record_ref") or ""),
                ) if x
            )
            chunks.append(
                AIChunk(
                    workspace_project_id=tenant,
                    source_kind=str(row.get("source_kind") or row.get("record_type") or "DATA_HUB"),
                    source_name=str(row.get("source_name") or "Contractor Data Hub"),
                    source_ref=source_ref or str(row.get("record_key") or ""),
                    content=str(row.get("content") or ""),
                    checksum=_checksum(row.get("record_key"), row.get("content"), row.get("external_modified_time")),
                    metadata={
                        "domain": str(row.get("category") or "data_hub").lower(),
                        "source_id": str(row.get("source_id") or ""),
                        "record_type": str(row.get("record_type") or ""),
                        "source_row": int(row.get("source_row") or 0),
                    },
                )
            )
        return self.upsert_chunks(chunks)

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

        params: list[Any] = [tenant]
        domain_sql = ""
        if wanted:
            domain_sql = " AND LOWER(domain)=ANY(%s)"
            params.append(wanted)

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
                    "SELECT workspace_project_id,source_kind,source_name,source_ref,content,checksum,metadata_json,0.0 AS score "
                    "FROM qlda_ai_chunks WHERE workspace_project_id=%s"
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
