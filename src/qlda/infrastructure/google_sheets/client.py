from __future__ import annotations

import base64
import csv
import hashlib
import hmac
import io
import json
import os
import secrets
import time
from typing import Any
from urllib.parse import quote, urlencode

import requests


class GoogleSheetsConfigError(RuntimeError):
    pass


class GoogleSheetsClient:
    """Read-only Google Sheets client without a service account.

    Supported access modes:
    1. Public/link-only: spreadsheet is shared as "Anyone with the link - Viewer".
       QLDA reads the selected worksheet anonymously through Google's CSV endpoints.
    2. Google OAuth: a QLDA user signs in with a Google account that already has
       access to the spreadsheet. OAuth tokens are intended to stay in the
       Streamlit session, not in the project database.
    """

    SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"
    API = "https://sheets.googleapis.com/v4/spreadsheets"
    AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
    TOKEN_URL = "https://oauth2.googleapis.com/token"
    PUBLIC_GVIZ_URL = "https://docs.google.com/spreadsheets/d/{spreadsheet_id}/gviz/tq"
    PUBLIC_EXPORT_URL = "https://docs.google.com/spreadsheets/d/{spreadsheet_id}/export"

    def __init__(self, token_state: dict[str, Any] | None = None):
        state = dict(token_state or {})
        self.access_token = str(state.get("access_token") or "").strip()
        self.refresh_token = str(state.get("refresh_token") or "").strip()
        self.expires_at = float(state.get("expires_at") or 0.0)
        self.token_type = str(state.get("token_type") or "Bearer")

    @staticmethod
    def _env(name: str) -> str:
        value = str(os.environ.get(name, "") or "").strip()
        if value:
            return value
        try:
            import streamlit as st

            return str(st.secrets.get(name, "") or "").strip()
        except Exception:
            return ""

    @classmethod
    def oauth_client_id(cls) -> str:
        return cls._env("GOOGLE_OAUTH_CLIENT_ID")

    @classmethod
    def oauth_client_secret(cls) -> str:
        return cls._env("GOOGLE_OAUTH_CLIENT_SECRET")

    @classmethod
    def oauth_redirect_uri(cls) -> str:
        explicit = cls._env("GOOGLE_OAUTH_REDIRECT_URI")
        if explicit:
            return explicit
        base = cls._env("QLDA_PUBLIC_BASE_URL").rstrip("/")
        return base or "http://localhost:8501"

    @classmethod
    def oauth_available(cls) -> bool:
        return bool(cls.oauth_client_id() and cls.oauth_client_secret())

    @classmethod
    def build_authorization_url(cls, state: str) -> str:
        client_id = cls.oauth_client_id()
        if not client_id:
            raise GoogleSheetsConfigError("Chưa cấu hình GOOGLE_OAUTH_CLIENT_ID.")
        params = {
            "client_id": client_id,
            "redirect_uri": cls.oauth_redirect_uri(),
            "response_type": "code",
            "scope": f"openid email {cls.SCOPE}",
            "access_type": "offline",
            "include_granted_scopes": "true",
            "prompt": "consent",
            "state": str(state),
        }
        return f"{cls.AUTH_URL}?{urlencode(params)}"

    @classmethod
    def exchange_code(cls, code: str) -> dict[str, Any]:
        if not cls.oauth_available():
            raise GoogleSheetsConfigError(
                "Google OAuth chưa được cấu hình trên VPS. Cần GOOGLE_OAUTH_CLIENT_ID và GOOGLE_OAUTH_CLIENT_SECRET."
            )
        try:
            response = requests.post(
                cls.TOKEN_URL,
                data={
                    "code": str(code),
                    "client_id": cls.oauth_client_id(),
                    "client_secret": cls.oauth_client_secret(),
                    "redirect_uri": cls.oauth_redirect_uri(),
                    "grant_type": "authorization_code",
                },
                timeout=30,
            )
        except requests.RequestException as exc:
            raise GoogleSheetsConfigError(f"Không kết nối được Google OAuth: {exc}") from exc
        return cls._token_response(response)

    @classmethod
    def _token_response(cls, response) -> dict[str, Any]:
        if response.status_code >= 400:
            try:
                payload = response.json()
                detail = payload.get("error_description") or payload.get("error") or response.text
            except Exception:
                detail = response.text
            raise GoogleSheetsConfigError(f"Google OAuth HTTP {response.status_code}: {str(detail)[:500]}")
        try:
            payload = dict(response.json())
        except Exception as exc:
            raise GoogleSheetsConfigError("Google OAuth trả về dữ liệu không hợp lệ.") from exc
        if not payload.get("access_token"):
            raise GoogleSheetsConfigError("Google OAuth không trả về access token.")
        expires_in = float(payload.get("expires_in") or 3600)
        payload["expires_at"] = time.time() + max(60.0, expires_in - 30.0)
        return payload

    def _refresh(self) -> None:
        if not self.refresh_token:
            raise GoogleSheetsConfigError("Phiên Google đã hết hạn. Vui lòng đăng nhập Google lại.")
        if not self.oauth_available():
            raise GoogleSheetsConfigError("Google OAuth chưa được cấu hình đầy đủ trên VPS.")
        try:
            response = requests.post(
                self.TOKEN_URL,
                data={
                    "client_id": self.oauth_client_id(),
                    "client_secret": self.oauth_client_secret(),
                    "refresh_token": self.refresh_token,
                    "grant_type": "refresh_token",
                },
                timeout=30,
            )
        except requests.RequestException as exc:
            raise GoogleSheetsConfigError(f"Không làm mới được phiên Google: {exc}") from exc
        payload = self._token_response(response)
        self.access_token = str(payload.get("access_token") or "")
        self.expires_at = float(payload.get("expires_at") or 0.0)
        self.token_type = str(payload.get("token_type") or "Bearer")

    @property
    def authorized(self) -> bool:
        return bool(self.access_token or self.refresh_token)

    def token_state(self) -> dict[str, Any]:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at,
            "token_type": self.token_type,
        }

    def _token(self) -> str:
        if self.access_token and (not self.expires_at or time.time() < self.expires_at):
            return self.access_token
        self._refresh()
        return self.access_token

    def _get(self, url: str, *, params: Any = None, timeout: int = 60) -> dict[str, Any]:
        if not self.authorized:
            raise GoogleSheetsConfigError("Chưa đăng nhập Google trong QLDA.")
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

    @staticmethod
    def _looks_like_google_login(response) -> bool:
        content_type = str(response.headers.get("content-type") or "").lower()
        head = str(response.text or "")[:1000].lower()
        final_url = str(getattr(response, "url", "") or "").lower()
        return (
            "accounts.google.com" in final_url
            or "serviceLogin".lower() in final_url
            or (
                "text/html" in content_type
                and (
                    "sign in" in head
                    or "đăng nhập" in head
                    or "accounts.google.com" in head
                    or "servicelogin" in head
                )
            )
        )

    @classmethod
    def public_values(cls, spreadsheet_id: str, gid: int = 0) -> list[list[Any]]:
        """Read one anonymously accessible worksheet without API credentials.

        Google currently exposes more than one anonymous CSV path. Some Workspace
        configurations return 401 on gviz while the standard export endpoint is
        still available, so QLDA tries both before declaring that authentication
        is required.
        """
        spreadsheet_id = quote(str(spreadsheet_id), safe="")
        attempts = [
            (
                cls.PUBLIC_GVIZ_URL.format(spreadsheet_id=spreadsheet_id),
                {"gid": int(gid), "tqx": "out:csv"},
                "gviz",
            ),
            (
                cls.PUBLIC_EXPORT_URL.format(spreadsheet_id=spreadsheet_id),
                {"format": "csv", "gid": int(gid)},
                "export",
            ),
        ]
        failures: list[str] = []

        for url, params, label in attempts:
            try:
                response = requests.get(
                    url,
                    params=params,
                    headers={
                        "User-Agent": "Mozilla/5.0 QLDA/7 GoogleSheetsLinkReader",
                        "Accept": "text/csv,text/plain,*/*",
                        "Cache-Control": "no-cache",
                    },
                    timeout=60,
                    allow_redirects=True,
                )
            except requests.RequestException as exc:
                failures.append(f"{label}: network {exc}")
                continue

            if response.status_code >= 400:
                failures.append(f"{label}: HTTP {response.status_code}")
                continue
            if cls._looks_like_google_login(response):
                failures.append(f"{label}: yêu cầu đăng nhập Google")
                continue

            try:
                rows = [list(row) for row in csv.reader(io.StringIO(response.text))]
            except Exception as exc:
                failures.append(f"{label}: CSV {exc}")
                continue
            if rows:
                return rows
            failures.append(f"{label}: dữ liệu rỗng")

        detail = "; ".join(failures[-2:])
        raise GoogleSheetsConfigError(
            "Google Sheet không cho phép QLDA đọc ẩn danh từ link"
            + (f" ({detail})" if detail else "")
            + ". Kiểm tra Share → General access phải là 'Anyone with the link' → Viewer. "
            "Nếu file thuộc Google Workspace bị chặn chia sẻ công khai hoặc chỉ chia sẻ cho email cụ thể, "
            "hãy chọn 'Đăng nhập Google' trong QLDA; không cần Service Account."
        )


def _state_secret() -> str:
    return (
        GoogleSheetsClient._env("QLDA_SETTINGS_MASTER_KEY")
        or GoogleSheetsClient._env("QLDA_LOCAL_UPLOAD_SECRET")
        or GoogleSheetsClient.oauth_client_secret()
    )


def make_oauth_state(project_id: int, actor: str = "") -> str:
    secret = _state_secret()
    if not secret:
        raise GoogleSheetsConfigError("Thiếu khóa máy chủ để bảo vệ Google OAuth state.")
    payload = {
        "p": int(project_id),
        "t": int(time.time()),
        "n": secrets.token_urlsafe(12),
        "a": hashlib.sha256(str(actor or "").strip().lower().encode("utf-8")).hexdigest()[:16],
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    body = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    sig = hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def verify_oauth_state(state: str, actor: str = "", *, max_age_seconds: int = 900) -> dict[str, Any]:
    secret = _state_secret()
    if not secret:
        raise GoogleSheetsConfigError("Thiếu khóa máy chủ để xác thực Google OAuth state.")
    try:
        body, supplied_sig = str(state).split(".", 1)
        expected = hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(supplied_sig, expected):
            raise ValueError("signature")
        padded = body + "=" * (-len(body) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    except Exception as exc:
        raise GoogleSheetsConfigError("Google OAuth state không hợp lệ.") from exc
    if abs(time.time() - float(payload.get("t") or 0)) > int(max_age_seconds):
        raise GoogleSheetsConfigError("Google OAuth state đã hết hạn. Vui lòng đăng nhập lại.")
    actor_hash = hashlib.sha256(str(actor or "").strip().lower().encode("utf-8")).hexdigest()[:16]
    if str(payload.get("a") or "") != actor_hash:
        raise GoogleSheetsConfigError("Phiên Google OAuth không khớp người dùng QLDA hiện tại.")
    return dict(payload)
