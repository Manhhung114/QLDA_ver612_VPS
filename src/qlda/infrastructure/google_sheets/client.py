from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests


class GoogleSheetsConfigError(RuntimeError):
    pass


class GoogleSheetsClient:
    """Read-only Google Sheets client using one server-side service account.

    Configure GOOGLE_SERVICE_ACCOUNT_JSON (full JSON text) or
    GOOGLE_SERVICE_ACCOUNT_FILE. Share only the spreadsheets that QLDA must read
    with the service-account email and Viewer permission.
    """

    SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"
    API = "https://sheets.googleapis.com/v4/spreadsheets"

    def __init__(self):
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

    def _token(self) -> str:
        if self._credentials is None:
            try:
                from google.oauth2.service_account import Credentials
            except ImportError as exc:
                raise GoogleSheetsConfigError("Thiếu google-auth trong môi trường chạy.") from exc
            self._credentials = Credentials.from_service_account_info(
                self._credential_info(), scopes=[self.SCOPE]
            )
        if not self._credentials.valid or not self._credentials.token:
            try:
                from google.auth.transport.requests import Request
                self._credentials.refresh(Request())
            except Exception as exc:
                raise GoogleSheetsConfigError(f"Không lấy được Google access token: {exc}") from exc
        return str(self._credentials.token)

    def _get(self, url: str, *, params: Any = None, timeout: int = 60) -> dict[str, Any]:
        try:
            response = requests.get(
                url,
                params=params or {},
                headers={"Authorization": f"Bearer {self._token()}"},
                timeout=timeout,
            )
        except requests.RequestException as exc:
            raise GoogleSheetsConfigError(f"Không kết nối được Google Sheets API: {exc}") from exc
        if response.status_code >= 400:
            try:
                detail = response.json().get("error", {}).get("message", response.text)
            except Exception:
                detail = response.text
            raise GoogleSheetsConfigError(
                f"Google Sheets API HTTP {response.status_code}: {str(detail)[:500]}"
            )
        try:
            return dict(response.json())
        except Exception as exc:
            raise GoogleSheetsConfigError("Google Sheets API trả về dữ liệu không hợp lệ.") from exc

    def metadata(self, spreadsheet_id: str) -> dict[str, Any]:
        data = self._get(
            f"{self.API}/{quote(str(spreadsheet_id), safe='')}",
            params={"fields": "properties(title),sheets(properties(sheetId,title,index,hidden))"},
        )
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
        data = self._get(
            f"{self.API}/{quote(str(spreadsheet_id), safe='')}/values/{quote(str(a1_range), safe='')}",
            params={
                "valueRenderOption": "UNFORMATTED_VALUE",
                "dateTimeRenderOption": "FORMATTED_STRING",
            },
        )
        return list(data.get("values") or [])

    def batch_values(self, spreadsheet_id: str, ranges: list[str]) -> dict[str, list[list[Any]]]:
        if not ranges:
            return {}
        params: list[tuple[str, str]] = [("ranges", x) for x in ranges]
        params.extend([
            ("valueRenderOption", "UNFORMATTED_VALUE"),
            ("dateTimeRenderOption", "FORMATTED_STRING"),
        ])
        data = self._get(
            f"{self.API}/{quote(str(spreadsheet_id), safe='')}/values:batchGet",
            params=params,
            timeout=90,
        )
        return {
            str(item.get("range") or ""): list(item.get("values") or [])
            for item in (data.get("valueRanges") or [])
        }
