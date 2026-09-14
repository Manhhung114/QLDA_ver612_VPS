from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from qlda.infrastructure.database import make_database
from qlda.services.base import require_project_id


@dataclass(frozen=True)
class SearchSpec:
    kind: str
    table: str
    code_field: str
    title_field: str
    fields: tuple[str, ...]


SPECS: tuple[SearchSpec, ...] = (
    SearchSpec("task", "tasks", "wbs", "name", ("wbs", "name", "responsible", "note", "resource_names", "status")),
    SearchSpec("document", "documents", "code", "subject", ("doc_type", "code", "subject", "discipline", "contractor", "issuer", "assignee", "status", "description", "response", "note")),
    SearchSpec("drawing", "drawings", "drawing_no", "title", ("drawing_type", "drawing_no", "title", "discipline", "revision", "issuer", "receiver", "status", "description", "note")),
    SearchSpec("boq", "cost_budgets", "task_ref", "boq_item", ("task_ref", "boq_item", "unit", "contract_type", "contractor", "note")),
    SearchSpec("vo", "cost_variations", "vo_code", "description", ("vo_code", "task_ref", "description", "funding_source", "status", "note")),
    SearchSpec("payment", "payment_tracking", "payment_code", "installment", ("payment_code", "task_ref", "installment", "payment_status", "note")),
    SearchSpec("material", "material_master", "material_code", "material_name", ("material_code", "material_name", "spec_brand", "legal_ref", "supply_type", "task_ref", "note")),
    SearchSpec("procurement", "procurement_schedule", "material_code", "supplier", ("material_code", "task_ref", "supplier", "status", "note")),
    SearchSpec("inventory", "inventory_inspection", "slip_code", "material_code", ("slip_code", "material_code", "task_ref", "inspection_code", "material_status", "note")),
)


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


class SearchService:
    """Read-only project search across stable QLDA business tables."""

    @classmethod
    def search(
        cls,
        project_id: int,
        query: str,
        *,
        kinds: Iterable[str] | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        pid = require_project_id(project_id)
        needle = str(query or "").strip().lower()
        if len(needle) < 2:
            raise ValueError("Từ khóa tìm kiếm phải có ít nhất 2 ký tự.")
        cap = max(1, min(int(limit or 50), 200))
        selected = {str(x or "").strip().lower() for x in (kinds or []) if str(x or "").strip()}
        specs = [spec for spec in SPECS if not selected or spec.kind in selected]
        if selected and not specs:
            raise ValueError("Không có nhóm dữ liệu tìm kiếm hợp lệ.")

        db = make_database()
        out: list[dict[str, Any]] = []
        like = f"%{needle}%"
        with db.connect() as connection:
            for spec in specs:
                remaining = cap - len(out)
                if remaining <= 0:
                    break
                where = " OR ".join(
                    f"LOWER(COALESCE({field},'')) LIKE ?"
                    for field in spec.fields
                )
                sql = (
                    f"SELECT * FROM {spec.table} "
                    f"WHERE project_id=? AND ({where}) ORDER BY id DESC LIMIT ?"
                )
                params = [pid] + [like] * len(spec.fields) + [remaining]
                try:
                    rows = connection.execute(sql, params).fetchall()
                except Exception:
                    continue
                for row in rows:
                    data = _rowdict(row)
                    out.append(
                        {
                            "kind": spec.kind,
                            "id": int(data.get("id") or 0),
                            "code": str(data.get(spec.code_field) or ""),
                            "title": str(data.get(spec.title_field) or ""),
                            "payload": data,
                        }
                    )
                    if len(out) >= cap:
                        break
        return out
