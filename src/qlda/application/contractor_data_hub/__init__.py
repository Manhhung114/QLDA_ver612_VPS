from __future__ import annotations

from typing import Any

from qlda.infrastructure.google_sheets.client import GoogleSheetsClient

from .service import ContractorDataHubAI
from .service import ContractorDataHubService as _BaseContractorDataHubService


class ContractorDataHubService(_BaseContractorDataHubService):
    """Public application service with corrected anonymous-Sheet ingestion."""

    def _public_sheet_records(
        self,
        source: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], int, dict[str, Any]]:
        spreadsheet_id = str(source.get("external_id") or "")
        tabs = list(source.get("worksheet_names") or [])
        gids: list[int] = []
        for value in tabs:
            text = str(value or "")
            if text.startswith("__gid__:") and text.split(":", 1)[1].isdigit():
                gids.append(int(text.split(":", 1)[1]))
        if not gids:
            gids = [0]

        records: list[dict[str, Any]] = []
        production_points = 0
        for gid in gids:
            values = GoogleSheetsClient.public_values(spreadsheet_id, gid)
            worksheet = f"gid={gid}"
            generic = self._generic_sheet_records(
                source,
                worksheet,
                values,
                external_item_id=spreadsheet_id,
            )
            production = self._production_records(
                source,
                worksheet,
                values,
                external_item_id=spreadsheet_id,
            )
            category = str(source.get("category") or "AUTO").upper()
            if category == "PRODUCTION":
                records.extend(production or generic)
            elif category == "AUTO":
                records.extend(generic)
                records.extend(production)
            else:
                records.extend(generic)
            production_points += len(production)

        return records, production_points, {
            "spreadsheet_id": spreadsheet_id,
            "gids": gids,
            "record_count": len(records),
            "production_points": production_points,
        }


__all__ = ["ContractorDataHubService", "ContractorDataHubAI"]
