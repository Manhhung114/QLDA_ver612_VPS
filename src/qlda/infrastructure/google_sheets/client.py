from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


class GoogleSheetsConfigError(RuntimeError):
    pass


class GoogleSheetsClient:
    """Read-only Google Sheets client using one server-side service account.

    Credentials are never stored in the repository. Configure either
    GOOGLE_SERVICE_ACCOUNT_JSON (full JSON text) or GOOGLE_SERVICE_ACCOUNT_FILE.
    Share only the required spreadsheets with the service-account email.
    """

    SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"

    def __init__(self):
        self._service = None
        self._credentials = None

    def _credential_info(self) -> dict[str, Any]:
        raw = str(os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "") or "").strip()
        path = str(os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", "") or "").strip()
        if raw:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise GoogleSheetsConfigError("GOOGLE_SERVICE_ACCOUNT_JSON không phải JSON hợp lệ.") from exc
        elif path:
            p = Path(path)
            if not p.exists():
                raise GoogleSheetsConfigError(f"Không tìm thấy service-account file: {p}")
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
            except Exception as exc:
                raise GoogleSheetsConfigError("Không đọc được service-account file.") from exc
        else:
            raise GoogleSheetsConfigError(
                "Chưa cấu hình GOOGLE_SERVICE_ACCOUNT_JSON hoặc GOOGLE_SERVICE_ACCOUNT_FILE."
            )
        if str(data.get("type") or "") != "service_account":
            raise GoogleSheetsConfigError("Credential phải là Google service account.")
        return data

    @property
    def service_account_email(self) -> str:
        return str(self._credential_info().get("client_email") or "")

    def _api(self):
        if self._service is not None:
            return self._service
        try:
            from google.oauth2.service_account import Credentials
            from googleapiclient.discovery import build
        except ImportError as exc:
            raise GoogleSheetsConfigError(
                "Thiếu google-api-python-client/google-auth. Hãy cài requirements mới."
            ) from exc
        info = self._credential_info()
        self._credentials = Credentials.from_service_account_info(info, scopes=[self.SCOPE])
        self._service = build("sheets", "v4", credentials=self._credentials, cache_discovery=False)
        return self._service

    def metadata(self, spreadsheet_id: str) -> dict[str, Any]:
        data = self._api().spreadsheets().get(
            spreadsheetId=spreadsheet_id,
            fields="properties(title),sheets(properties(sheetId,title,index,hidden))",
        ).execute()
        return {
            "title": str((data.get("properties") or {}).get("title") or ""),
            "sheets": [
                {
                    "sheet_id": int((x.get("properties") or {}).get("sheetId") or 0),
                    "title": str((x.get("properties") or {}).get("title") or ""),
                    "index": int((x.get("properties") or {}).get("index") or 0),
                    "hidden": bool((x.get("properties") or {}).get("hidden", False)),
                }
                for x in (data.get("sheets") or [])
            ],
        }

    def values(self, spreadsheet_id: str, a1_range: str) -> list[list[Any]]:
        data = self._api().spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=a1_range,
            valueRenderOption="UNFORMATTED_VALUE",
            dateTimeRenderOption="FORMATTED_STRING",
        ).execute()
        return list(data.get("values") or [])

    def batch_values(self, spreadsheet_id: str, ranges: list[str]) -> dict[str, list[list[Any]]]:
        if not ranges:
            return {}
        data = self._api().spreadsheets().values().batchGet(
            spreadsheetId=spreadsheet_id,
            ranges=ranges,
            valueRenderOption="UNFORMATTED_VALUE",
            dateTimeRenderOption="FORMATTED_STRING",
        ).execute()
        out: dict[str, list[list[Any]]] = {}
        for item in data.get("valueRanges") or []:
            out[str(item.get("range") or "")] = list(item.get("values") or [])
        return out
