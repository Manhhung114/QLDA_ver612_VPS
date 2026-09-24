from __future__ import annotations

import hashlib
from typing import Any, Iterable

from .repository import ContractorDataHubRepository as _BaseContractorDataHubRepository


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


def _norm(value: Any) -> str:
    return str(value or "").strip()


class ContractorDataHubRepository(_BaseContractorDataHubRepository):
    """Production facade for Contractor Data Hub persistence.

    The base repository keeps the stable database contract.  This facade adds
    three production guards:
    - exact project metrics without SQL join multiplication;
    - collision-safe record identities for complex contractor spreadsheets;
    - seamless upgrade from a failed PUBLIC_LINK Sheet source to Google OAuth.
    """

    def project_metrics(
        self,
        master_project_id: int,
        *,
        workspace_ids: Iterable[int] | None = None,
    ) -> list[dict[str, Any]]:
        ids = [int(x) for x in (workspace_ids or []) if int(x) > 0]
        sql = """SELECT
            sp.contractor_id,sp.contractor_code,sp.contractor_name,sp.workspace_project_id,
            sp.last_sync,sp.last_error,
            (SELECT COUNT(*) FROM contractor_data_sources d
                WHERE d.data_space_id=sp.data_space_id AND d.enabled=1) AS source_count,
            (SELECT COUNT(*) FROM contractor_data_records r
                WHERE r.data_space_id=sp.data_space_id) AS record_count,
            (SELECT COUNT(*) FROM contractor_data_records r
                WHERE r.data_space_id=sp.data_space_id AND r.record_type='PRODUCTION') AS production_points,
            (SELECT AVG(r.progress_percent) FROM contractor_data_records r
                WHERE r.data_space_id=sp.data_space_id AND r.record_type='PRODUCTION') AS avg_progress
            FROM contractor_data_spaces sp
            WHERE sp.master_project_id=?"""
        params: list[Any] = [int(master_project_id)]
        if ids:
            sql += " AND sp.workspace_project_id IN (" + ",".join("?" for _ in ids) + ")"
            params.extend(ids)
        sql += " ORDER BY sp.contractor_code,sp.contractor_name"
        with self.db.connect() as connection:
            rows = connection.execute(sql, params).fetchall()
        return [_rowdict(x) for x in rows]

    def save_source(
        self,
        data_space_id: str,
        *,
        source_id: str = "",
        source_kind: str,
        name: str,
        external_id: str = "",
        source_url: str = "",
        category: str = "AUTO",
        access_mode: str = "GOOGLE_OAUTH",
        worksheet_names: list[str] | None = None,
        discover_children: bool = False,
        enabled: bool = True,
    ) -> str:
        """Avoid duplicate cards when the same Sheet is re-added with OAuth.

        A failed anonymous/public source is upgraded in place once the project
        has a valid Google OAuth connection.  Existing OAuth sources with the
        same contractor + external ID + category are also reused.
        """
        sid = _norm(source_id)
        kind = _norm(source_kind).upper() or "SHEET"
        ext = _norm(external_id)
        cat = _norm(category).upper() or "AUTO"
        mode = _norm(access_mode).upper() or "GOOGLE_OAUTH"

        if not sid and ext:
            candidates = [
                item for item in super().list_sources(str(data_space_id))
                if _norm(item.get("source_kind")).upper() == kind
                and _norm(item.get("external_id")) == ext
                and _norm(item.get("category")).upper() == cat
            ]
            exact = next(
                (x for x in candidates if _norm(x.get("access_mode")).upper() == mode),
                None,
            )
            upgrade = next(
                (
                    x for x in candidates
                    if mode == "GOOGLE_OAUTH"
                    and _norm(x.get("access_mode")).upper() == "PUBLIC_LINK"
                    and (
                        not _norm(x.get("last_sync"))
                        or "401" in _norm(x.get("last_error"))
                        or "403" in _norm(x.get("last_error"))
                        or "ẩn danh" in _norm(x.get("last_error")).lower()
                    )
                ),
                None,
            )
            chosen = exact or upgrade
            if chosen:
                sid = _norm(chosen.get("source_id"))

        return super().save_source(
            str(data_space_id),
            source_id=sid,
            source_kind=kind,
            name=name,
            external_id=ext,
            source_url=source_url,
            category=cat,
            access_mode=mode,
            worksheet_names=worksheet_names,
            discover_children=discover_children,
            enabled=enabled,
        )

    @staticmethod
    def _record_identity(source_id: str, row: dict[str, Any]) -> str:
        """Build a stable identity rich enough for real-world Sheets.

        The original key omitted record_ref/work_item/content.  Complex Sheets
        can therefore legitimately yield two different records on the same row
        and Zone, causing a PostgreSQL primary-key collision.  Include the
        semantic fields and a short content digest instead.
        """
        explicit = _norm(row.get("record_key"))
        content = _norm(row.get("content"))
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()[:20]
        if explicit:
            return f"{explicit}|{content_hash}"
        return "|".join([
            _norm(source_id),
            _norm(row.get("external_item_id")),
            _norm(row.get("external_path")),
            _norm(row.get("worksheet")),
            _norm(row.get("record_type") or "TEXT").upper(),
            _norm(row.get("record_ref")),
            _norm(row.get("source_row")),
            _norm(row.get("zone")),
            _norm(row.get("work_item")),
            content_hash,
        ])

    def _cleanup_failed_public_duplicates(self, source: dict[str, Any]) -> int:
        """Remove only failed, never-synced PUBLIC_LINK duplicates after OAuth succeeds."""
        if _norm(source.get("source_kind")).upper() != "SHEET":
            return 0
        if _norm(source.get("access_mode")).upper() != "GOOGLE_OAUTH":
            return 0
        ext = _norm(source.get("external_id"))
        if not ext:
            return 0
        current_id = _norm(source.get("source_id"))
        category = _norm(source.get("category")).upper()
        removed = 0
        for candidate in super().list_sources(_norm(source.get("data_space_id"))):
            if _norm(candidate.get("source_id")) == current_id:
                continue
            if _norm(candidate.get("source_kind")).upper() != "SHEET":
                continue
            if _norm(candidate.get("external_id")) != ext:
                continue
            if _norm(candidate.get("category")).upper() != category:
                continue
            if _norm(candidate.get("access_mode")).upper() != "PUBLIC_LINK":
                continue
            err = _norm(candidate.get("last_error")).lower()
            failed_anonymous = (
                not _norm(candidate.get("last_sync"))
                and ("401" in err or "403" in err or "ẩn danh" in err or "anonymous" in err)
            )
            if not failed_anonymous:
                continue
            super().delete_source(_norm(candidate.get("source_id")))
            removed += 1
        return removed

    def replace_records(
        self,
        source: dict[str, Any],
        records: list[dict[str, Any]],
        *,
        snapshot_payload: Any = None,
    ) -> dict[str, Any]:
        """Persist one source atomically without duplicate primary-key failures."""
        sid = _norm(source.get("source_id"))
        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        duplicates_skipped = 0

        for raw in records:
            row = dict(raw or {})
            identity = self._record_identity(sid, row)
            if identity in seen:
                duplicates_skipped += 1
                continue
            seen.add(identity)
            # The base repository hashes record_key before INSERT. Supplying our
            # semantic identity here keeps its schema/transaction behavior intact.
            row["record_key"] = identity
            normalized.append(row)

        result = super().replace_records(
            source,
            normalized,
            snapshot_payload=snapshot_payload,
        )
        cleaned = self._cleanup_failed_public_duplicates(source)
        result["duplicates_skipped"] = duplicates_skipped
        result["legacy_public_sources_removed"] = cleaned
        return result


__all__ = ["ContractorDataHubRepository"]
