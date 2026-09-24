from __future__ import annotations

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


class ContractorDataHubRepository(_BaseContractorDataHubRepository):
    """Public repository with exact, non-multiplying project metrics."""

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


__all__ = ["ContractorDataHubRepository"]
