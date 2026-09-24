from __future__ import annotations

import io
import re
from typing import Any
from urllib.parse import quote, urlparse

import requests

from .client import GoogleSheetsClient, GoogleSheetsConfigError


_FOLDER_RE = re.compile(r"/folders/([A-Za-z0-9_-]+)")
_FILE_RE = re.compile(r"/d/([A-Za-z0-9_-]+)")


def parse_drive_folder_id(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError("Chưa nhập link Google Drive folder.")
    match = _FOLDER_RE.search(text)
    if match:
        return match.group(1)
    try:
        parsed = urlparse(text)
        if parsed.netloc.endswith("drive.google.com"):
            from urllib.parse import parse_qs

            fid = (parse_qs(parsed.query).get("id") or [""])[0]
            if fid:
                return fid
    except Exception:
        pass
    if re.fullmatch(r"[A-Za-z0-9_-]{10,}", text):
        return text
    raise ValueError("Link / Folder ID Google Drive không hợp lệ.")


def parse_drive_file_id(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError("Chưa nhập link Google Drive file.")
    match = _FILE_RE.search(text)
    if match:
        return match.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{10,}", text):
        return text
    raise ValueError("Link / File ID Google Drive không hợp lệ.")


class GoogleWorkspaceClient(GoogleSheetsClient):
    """Google Sheets + Drive read-only client sharing the same OAuth token."""

    SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"
    DRIVE_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
    # GoogleSheetsClient.build_authorization_url is a classmethod and therefore
    # uses this combined scope when called on GoogleWorkspaceClient.
    SCOPE = f"{SHEETS_SCOPE} {DRIVE_SCOPE}"

    DRIVE_API = "https://www.googleapis.com/drive/v3/files"
    USERINFO_API = "https://www.googleapis.com/oauth2/v2/userinfo"

    def _drive_json(self, url: str, *, params: Any = None, timeout: int = 60) -> dict[str, Any]:
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
            raise GoogleSheetsConfigError(f"Không kết nối được Google Drive API: {exc}") from exc
        if response.status_code >= 400:
            try:
                payload = response.json()
                detail = (payload.get("error") or {}).get("message") or response.text
            except Exception:
                detail = response.text
            raise GoogleSheetsConfigError(
                f"Google Drive API HTTP {response.status_code}: {str(detail)[:500]}"
            )
        try:
            return dict(response.json())
        except Exception as exc:
            raise GoogleSheetsConfigError("Google Drive API trả về dữ liệu không hợp lệ.") from exc

    def account_profile(self) -> dict[str, Any]:
        data = self._drive_json(self.USERINFO_API)
        return {
            "email": str(data.get("email") or "").strip().lower(),
            "name": str(data.get("name") or "").strip(),
            "picture": str(data.get("picture") or "").strip(),
            "verified_email": bool(data.get("verified_email", False)),
        }

    def file_metadata(self, file_id: str) -> dict[str, Any]:
        fields = "id,name,mimeType,modifiedTime,createdTime,parents,size,md5Checksum,webViewLink,trashed"
        return self._drive_json(
            f"{self.DRIVE_API}/{quote(str(file_id), safe='')}",
            params={"fields": fields, "supportsAllDrives": "true"},
        )

    def list_children(self, folder_id: str, *, page_size: int = 200) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        token = ""
        fields = "nextPageToken,files(id,name,mimeType,modifiedTime,createdTime,parents,size,md5Checksum,webViewLink,trashed)"
        while True:
            params: dict[str, Any] = {
                "q": f"'{str(folder_id)}' in parents and trashed=false",
                "pageSize": max(1, min(int(page_size), 1000)),
                "fields": fields,
                "supportsAllDrives": "true",
                "includeItemsFromAllDrives": "true",
            }
            if token:
                params["pageToken"] = token
            data = self._drive_json(self.DRIVE_API, params=params, timeout=90)
            out.extend(dict(x) for x in (data.get("files") or []))
            token = str(data.get("nextPageToken") or "")
            if not token:
                break
        return out

    def list_folder_tree(
        self,
        folder_id: str,
        *,
        max_depth: int = 5,
        max_files: int = 1000,
    ) -> list[dict[str, Any]]:
        """Recursively list a contractor folder with bounded traversal."""
        root = str(folder_id)
        queue: list[tuple[str, int, str]] = [(root, 0, "")]
        seen_folders: set[str] = set()
        files: list[dict[str, Any]] = []
        while queue and len(files) < int(max_files):
            current, depth, parent_path = queue.pop(0)
            if current in seen_folders or depth > int(max_depth):
                continue
            seen_folders.add(current)
            for item in self.list_children(current):
                item = dict(item)
                path = f"{parent_path}/{item.get('name','')}".strip("/")
                item["relative_path"] = path
                item["root_folder_id"] = root
                if item.get("mimeType") == "application/vnd.google-apps.folder":
                    if depth < int(max_depth):
                        queue.append((str(item.get("id") or ""), depth + 1, path))
                    continue
                files.append(item)
                if len(files) >= int(max_files):
                    break
        return files

    def download_file(self, file_id: str, *, max_bytes: int = 30 * 1024 * 1024) -> bytes:
        if not self.authorized:
            raise GoogleSheetsConfigError("Chưa đăng nhập Google trong QLDA.")
        try:
            response = requests.get(
                f"{self.DRIVE_API}/{quote(str(file_id), safe='')}",
                params={"alt": "media", "supportsAllDrives": "true"},
                headers={"Authorization": f"Bearer {self._token()}"},
                timeout=120,
                stream=True,
            )
        except requests.RequestException as exc:
            raise GoogleSheetsConfigError(f"Không tải được file Google Drive: {exc}") from exc
        if response.status_code >= 400:
            raise GoogleSheetsConfigError(f"Google Drive download HTTP {response.status_code}.")
        buf = io.BytesIO()
        for chunk in response.iter_content(chunk_size=1024 * 256):
            if not chunk:
                continue
            buf.write(chunk)
            if buf.tell() > int(max_bytes):
                raise GoogleSheetsConfigError(
                    f"File vượt giới hạn đọc AI {int(max_bytes / 1024 / 1024)} MB."
                )
        return buf.getvalue()

    def export_google_file(
        self,
        file_id: str,
        mime_type: str,
        *,
        max_bytes: int = 30 * 1024 * 1024,
    ) -> bytes:
        if not self.authorized:
            raise GoogleSheetsConfigError("Chưa đăng nhập Google trong QLDA.")
        try:
            response = requests.get(
                f"{self.DRIVE_API}/{quote(str(file_id), safe='')}/export",
                params={"mimeType": str(mime_type)},
                headers={"Authorization": f"Bearer {self._token()}"},
                timeout=120,
                stream=True,
            )
        except requests.RequestException as exc:
            raise GoogleSheetsConfigError(f"Không export được file Google: {exc}") from exc
        if response.status_code >= 400:
            raise GoogleSheetsConfigError(f"Google Drive export HTTP {response.status_code}.")
        buf = io.BytesIO()
        for chunk in response.iter_content(chunk_size=1024 * 256):
            if not chunk:
                continue
            buf.write(chunk)
            if buf.tell() > int(max_bytes):
                raise GoogleSheetsConfigError(
                    f"File export vượt giới hạn đọc AI {int(max_bytes / 1024 / 1024)} MB."
                )
        return buf.getvalue()
