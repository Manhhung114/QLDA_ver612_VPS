from __future__ import annotations

import html
import json
import re
from typing import Any

import requests

from qlda.infrastructure.google_sheets.client import GoogleSheetsClient

from .service import ContractorDataHubAI
from .service import ContractorDataHubService as _BaseContractorDataHubService


class ContractorDataHubService(_BaseContractorDataHubService):
    """Public application service with resilient multi-tab Google Sheet ingestion.

    OAuth sources always rescan all visible tabs from Google metadata. Public-link
    sources prefer OAuth when available; without OAuth QLDA now also inspects the
    anonymously accessible workbook page to discover additional worksheet gids.
    """

    def _sheet_file_records(
        self,
        source: dict[str, Any],
        spreadsheet_id: str,
        *,
        external_path: str = "",
        modified_time: str = "",
        worksheet_names: list[str] | None = None,
    ) -> tuple[list[dict[str, Any]], int, dict[str, Any]]:
        # Do not let stale/legacy worksheet_names permanently pin an OAuth
        # spreadsheet to one tab. There is currently no UI that intentionally
        # configures a private source to a subset of worksheets, therefore the
        # authoritative list is the current Google metadata on every sync.
        records, production_points, snapshot = super()._sheet_file_records(
            source,
            spreadsheet_id,
            external_path=external_path,
            modified_time=modified_time,
            worksheet_names=None,
        )
        snapshot = dict(snapshot or {})
        snapshot["worksheet_discovery"] = "ALL_VISIBLE_TABS"
        if worksheet_names:
            snapshot["legacy_worksheet_filter_ignored"] = list(worksheet_names)
        return records, production_points, snapshot

    @staticmethod
    def _decode_js_string(value: str) -> str:
        raw = str(value or "")
        try:
            return str(json.loads('"' + raw.replace('"', '\\"') + '"'))
        except Exception:
            return raw.replace("\\u0027", "'").replace("\\u0026", "&").replace("\\/", "/")

    @classmethod
    def _discover_public_worksheets(cls, spreadsheet_id: str, source_url: str = "") -> dict[int, str]:
        """Best-effort anonymous worksheet discovery for public/link-only files.

        Google does not provide anonymous Sheets API metadata. The normal viewer
        page, however, contains bootstrapped sheet ids/titles for link-viewable
        workbooks. We parse several known representations and finally fall back to
        any gid references found in the page. Failure is non-fatal: the configured
        gid remains usable.
        """
        sid = str(spreadsheet_id or "").strip()
        if not sid:
            return {}
        url = str(source_url or "").strip()
        if not url or "/spreadsheets/d/" not in url:
            url = f"https://docs.google.com/spreadsheets/d/{sid}/edit"
        try:
            response = requests.get(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 QLDA/7 PublicSheetTabDiscovery",
                    "Accept": "text/html,application/xhtml+xml,*/*",
                    "Cache-Control": "no-cache",
                },
                timeout=30,
                allow_redirects=True,
            )
        except requests.RequestException:
            return {}
        if response.status_code >= 400 or GoogleSheetsClient._looks_like_google_login(response):
            return {}

        text = html.unescape(str(response.text or "")[:8_000_000])
        # Bootstrapped data is sometimes embedded as an escaped JSON string.
        normalized = text.replace('\\"', '"')
        found: dict[int, str] = {}

        patterns = (
            re.compile(r'"sheetId"\s*:\s*(\d+).{0,1200}?"title"\s*:\s*"((?:\\.|[^"\\])*)"', re.S),
            re.compile(r'"title"\s*:\s*"((?:\\.|[^"\\])*)".{0,1200}?"sheetId"\s*:\s*(\d+)', re.S),
        )
        for index, pattern in enumerate(patterns):
            for match in pattern.finditer(normalized):
                if index == 0:
                    gid_text, title_raw = match.group(1), match.group(2)
                else:
                    title_raw, gid_text = match.group(1), match.group(2)
                try:
                    gid = int(gid_text)
                except Exception:
                    continue
                title = cls._decode_js_string(title_raw).strip()
                found.setdefault(gid, title or f"gid={gid}")

        # Even if Google changes the title representation, gid references still
        # let QLDA read every public worksheet through the CSV endpoint.
        for gid_text in re.findall(r'(?:[?#&]|\\u0026|&amp;)gid(?:=|%3D)(\d+)', normalized, flags=re.I):
            try:
                gid = int(gid_text)
            except Exception:
                continue
            found.setdefault(gid, f"gid={gid}")
        return found

    def _public_sheet_records(
        self,
        source: dict[str, Any],
    ) -> tuple[list[dict[str, Any]], int, dict[str, Any]]:
        spreadsheet_id = str(source.get("external_id") or "")

        # If QLDA has an authorized Google session, prefer Sheets API metadata.
        # This discovers every visible worksheet and keeps the real worksheet
        # titles in the normalized records, even when the source was originally
        # saved from a public URL containing only one #gid.
        if self.client is not None and self.client.authorized:
            try:
                records, production_points, snapshot = self._sheet_file_records(
                    source,
                    spreadsheet_id,
                    worksheet_names=None,
                )
                snapshot = dict(snapshot or {})
                snapshot["access_mode"] = "PUBLIC_LINK_WITH_OAUTH_DISCOVERY"
                return records, production_points, snapshot
            except Exception:
                # A public source must remain usable anonymously if the connected
                # Google account cannot access it or its token is temporarily bad.
                pass

        tabs = list(source.get("worksheet_names") or [])
        configured_gids: list[int] = []
        for value in tabs:
            text = str(value or "")
            if text.startswith("__gid__:") and text.split(":", 1)[1].isdigit():
                configured_gids.append(int(text.split(":", 1)[1]))

        discovered = self._discover_public_worksheets(
            spreadsheet_id,
            str(source.get("source_url") or ""),
        )
        gids = list(dict.fromkeys([*configured_gids, *discovered.keys()]))
        if not gids:
            gids = [0]

        records: list[dict[str, Any]] = []
        production_points = 0
        worksheets: list[str] = []
        successful_gids: list[int] = []
        failures: list[str] = []
        for gid in gids:
            try:
                values = GoogleSheetsClient.public_values(spreadsheet_id, gid)
            except Exception as exc:
                failures.append(f"gid={gid}: {str(exc)[:180]}")
                continue
            worksheet = str(discovered.get(gid) or f"gid={gid}")
            worksheets.append(worksheet)
            successful_gids.append(gid)
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

        # If discovery produced only unusable ids, retry the originally configured
        # gid so a Google HTML-format change cannot break an existing source.
        if not successful_gids and configured_gids:
            gid = configured_gids[0]
            values = GoogleSheetsClient.public_values(spreadsheet_id, gid)
            worksheet = f"gid={gid}"
            generic = self._generic_sheet_records(source, worksheet, values, external_item_id=spreadsheet_id)
            production = self._production_records(source, worksheet, values, external_item_id=spreadsheet_id)
            category = str(source.get("category") or "AUTO").upper()
            if category == "PRODUCTION":
                records.extend(production or generic)
            elif category == "AUTO":
                records.extend(generic)
                records.extend(production)
            else:
                records.extend(generic)
            production_points += len(production)
            successful_gids = [gid]
            worksheets = [worksheet]

        return records, production_points, {
            "spreadsheet_id": spreadsheet_id,
            "gids": successful_gids,
            "worksheets": worksheets,
            "record_count": len(records),
            "production_points": production_points,
            "worksheet_discovery": (
                "PUBLIC_PAGE_ALL_TABS" if discovered else "CONFIGURED_GIDS_ONLY"
            ),
            "discovered_gid_count": len(discovered),
            "worksheet_failures": failures[:20],
            "access_mode": "PUBLIC_LINK_ANONYMOUS",
        }


__all__ = ["ContractorDataHubService", "ContractorDataHubAI"]
