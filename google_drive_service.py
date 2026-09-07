from __future__ import annotations

import io
import json
import mimetypes
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from settings_store import CONFIG_DIR, load_app_settings, save_app_settings

DRIVE_SCOPE = "https://www.googleapis.com/auth/drive"
DRIVE_TOKEN_FILE = CONFIG_DIR / "google_drive_token.json"
DRIVE_CLIENT_FILE = CONFIG_DIR / "google_drive_client.json"


class GoogleDriveError(RuntimeError):
    pass


def _imports():
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google.oauth2 import service_account
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload, MediaIoBaseUpload
        from googleapiclient.errors import HttpError
        return Request, Credentials, service_account, InstalledAppFlow, build, MediaFileUpload, MediaIoBaseUpload, HttpError
    except Exception as exc:
        raise GoogleDriveError(
            "Thiếu thư viện Google Drive. Cài: pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib"
        ) from exc


def extract_drive_id(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    # Common folder/file URLs: /folders/<id>, /d/<id>, ?id=<id>
    for pattern in (r"/folders/([A-Za-z0-9_-]+)", r"/d/([A-Za-z0-9_-]+)", r"[?&]id=([A-Za-z0-9_-]+)"):
        m = re.search(pattern, value)
        if m:
            return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{10,}", value):
        return value
    return ""


def is_drive_url(value: str) -> bool:
    s = str(value or "").lower()
    return s.startswith("https://drive.google.com/") or s.startswith("https://docs.google.com/")


def copy_oauth_client_json(source_path: str | Path) -> Path:
    source = Path(source_path)
    if not source.exists():
        raise GoogleDriveError(f"Không tìm thấy OAuth JSON: {source}")
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except Exception as exc:
        raise GoogleDriveError("File OAuth JSON không hợp lệ.") from exc
    if not isinstance(data, dict) or not (data.get("installed") or data.get("web")):
        raise GoogleDriveError("Đây không phải OAuth Client JSON của Google.")
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, DRIVE_CLIENT_FILE)
    save_app_settings({"drive_client_credentials_path": str(DRIVE_CLIENT_FILE)})
    return DRIVE_CLIENT_FILE


@dataclass
class DriveIdentity:
    email: str = ""
    name: str = ""
    role: str = "unknown"  # read/update/admin/unknown
    drive_role: str = ""
    shared_drive: bool = False
    owned_by_me: bool = False

    @property
    def label(self) -> str:
        return {"read": "Chỉ đọc", "update": "Cập nhật", "admin": "Admin", "unknown": "Chưa xác định"}.get(self.role, self.role)


class GoogleDriveService:
    """Google Drive backend.

    Desktop uses InstalledApp OAuth. Streamlit Community Cloud can use a Web OAuth
    access/refresh token for the *current user*, which makes My Drive sharing and
    Viewer/Editor/Owner RBAC work without a service account.
    """

    def __init__(self, service, actor_email: str = "", credentials=None):
        self.service = service
        self.actor_email = str(actor_email or "").strip().lower()
        self.credentials = credentials

    # ---------- Auth ----------
    @classmethod
    def desktop(cls, interactive: bool = False) -> "GoogleDriveService":
        Request, Credentials, _sa, InstalledAppFlow, build, *_ = _imports()
        cfg = load_app_settings()
        client_path = Path(str(cfg.get("drive_client_credentials_path") or DRIVE_CLIENT_FILE))
        creds = None
        if DRIVE_TOKEN_FILE.exists():
            try:
                creds = Credentials.from_authorized_user_file(str(DRIVE_TOKEN_FILE), [DRIVE_SCOPE])
            except Exception:
                creds = None
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                DRIVE_TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
            except Exception:
                creds = None
        if not creds or not creds.valid:
            if not interactive:
                raise GoogleDriveError("Google Drive chưa đăng nhập. Vào ⚙ Cài đặt → Google Drive → Kết nối Google.")
            if not client_path.exists():
                raise GoogleDriveError("Chưa có OAuth Client JSON. Hãy chọn file credentials JSON trong Cài đặt.")
            try:
                flow = InstalledAppFlow.from_client_secrets_file(str(client_path), [DRIVE_SCOPE])
                creds = flow.run_local_server(port=0, open_browser=True, prompt="consent")
            except Exception as exc:
                raise GoogleDriveError(f"Không hoàn tất đăng nhập Google: {exc}") from exc
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            DRIVE_TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
        try:
            service = build("drive", "v3", credentials=creds, cache_discovery=False)
            about = service.about().get(fields="user(displayName,emailAddress)").execute()
            user = about.get("user") or {}
            return cls(service, user.get("emailAddress", ""))
        except Exception as exc:
            raise GoogleDriveError(f"Không tạo được kết nối Google Drive API: {exc}") from exc


    @classmethod
    def user_oauth(cls, info: dict[str, Any]) -> "GoogleDriveService":
        """Create a Drive service from an authorized-user token dict.

        Used by Streamlit My Drive mode. The token is kept in the user's Streamlit
        session, refreshed when possible, and never committed to GitHub.
        """
        Request, Credentials, _sa, _InstalledAppFlow, build, *_ = _imports()
        try:
            creds = Credentials.from_authorized_user_info(info, scopes=[DRIVE_SCOPE])
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
            if not creds.valid:
                raise GoogleDriveError("Google OAuth token không còn hợp lệ. Hãy đăng nhập lại Google.")
            service = build("drive", "v3", credentials=creds, cache_discovery=False)
            about = service.about().get(fields="user(displayName,emailAddress,permissionId)").execute()
            user = about.get("user") or {}
            return cls(service, user.get("emailAddress", ""), credentials=creds)
        except GoogleDriveError:
            raise
        except Exception as exc:
            raise GoogleDriveError(f"Không kết nối được Google Drive bằng OAuth người dùng: {exc}") from exc

    def credentials_json(self) -> str:
        if self.credentials is None:
            return ""
        try:
            return self.credentials.to_json()
        except Exception:
            return ""

    def current_user(self) -> dict:
        try:
            about = self.service.about().get(fields="user(displayName,emailAddress,permissionId)").execute()
            return dict(about.get("user") or {})
        except Exception as exc:
            raise GoogleDriveError(f"Không đọc được tài khoản Google Drive hiện tại: {exc}") from exc

    @classmethod
    def service_account(cls, info: dict[str, Any], actor_email: str = "") -> "GoogleDriveService":
        _Request, _Credentials, service_account, _InstalledAppFlow, build, *_ = _imports()
        try:
            creds = service_account.Credentials.from_service_account_info(info, scopes=[DRIVE_SCOPE])
            service = build("drive", "v3", credentials=creds, cache_discovery=False)
            return cls(service, actor_email)
        except Exception as exc:
            raise GoogleDriveError(f"Không kết nối được Google Drive bằng service account: {exc}") from exc

    # ---------- Root / folders ----------
    def file_info(self, file_id: str) -> dict:
        try:
            return self.service.files().get(
                fileId=file_id,
                fields="id,name,mimeType,webViewLink,driveId,ownedByMe,parents,capabilities(canEdit,canShare,canDelete,canAddChildren,canDownload)",
                supportsAllDrives=True,
            ).execute()
        except Exception as exc:
            raise GoogleDriveError(f"Không đọc được thư mục/file Drive {file_id}: {exc}") from exc

    def ensure_root_folder(self, root_value: str = "", folder_name: str = "QLDA Xây dựng") -> dict:
        root_id = extract_drive_id(root_value)
        if root_id:
            info = self.file_info(root_id)
            if info.get("mimeType") != "application/vnd.google-apps.folder":
                raise GoogleDriveError("ID/URL đã nhập không phải thư mục Google Drive.")
            return info
        body = {"name": folder_name or "QLDA Xây dựng", "mimeType": "application/vnd.google-apps.folder"}
        try:
            created = self.service.files().create(body=body, fields="id,name,webViewLink,driveId,ownedByMe", supportsAllDrives=True).execute()
            if not created.get("webViewLink"):
                created["webViewLink"] = f"https://drive.google.com/drive/folders/{created['id']}"
            return created
        except Exception as exc:
            raise GoogleDriveError(f"Không tạo được thư mục gốc trên Google Drive: {exc}") from exc

    def _find_child_folder(self, parent_id: str, name: str) -> dict | None:
        safe = name.replace("'", "\\'")
        q = f"'{parent_id}' in parents and name='{safe}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
        try:
            res = self.service.files().list(
                q=q, spaces="drive", fields="files(id,name,webViewLink,driveId)", pageSize=20,
                includeItemsFromAllDrives=True, supportsAllDrives=True,
            ).execute()
            return (res.get("files") or [None])[0]
        except Exception:
            return None

    def ensure_folder(self, parent_id: str, name: str) -> dict:
        found = self._find_child_folder(parent_id, name)
        if found:
            return found
        body = {"name": name, "mimeType": "application/vnd.google-apps.folder", "parents": [parent_id]}
        try:
            return self.service.files().create(body=body, fields="id,name,webViewLink,driveId", supportsAllDrives=True).execute()
        except Exception as exc:
            raise GoogleDriveError(f"Không tạo được thư mục '{name}': {exc}") from exc

    def ensure_storage_path(self, root_id: str, project_code: str, category: str, record_code: str = "") -> dict:
        p = self.ensure_folder(root_id, _sanitize_name(project_code or "DU_AN"))
        c = self.ensure_folder(p["id"], _sanitize_name(category or "Tai_lieu"))
        if record_code:
            return self.ensure_folder(c["id"], _sanitize_name(record_code))
        return c

    # ---------- Upload ----------
    def upload_path(self, local_path: str | Path, parent_id: str) -> dict:
        _Request, _Credentials, _sa, _Flow, _build, MediaFileUpload, _MediaIoBaseUpload, *_ = _imports()
        path = Path(local_path)
        if not path.exists():
            raise GoogleDriveError(f"Không tìm thấy file để upload: {path}")
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        media = MediaFileUpload(str(path), mimetype=mime, resumable=True)
        body = {"name": path.name, "parents": [parent_id]}
        try:
            f = self.service.files().create(
                body=body, media_body=media, fields="id,name,webViewLink,webContentLink,size,mimeType", supportsAllDrives=True
            ).execute()
            f["webViewLink"] = f.get("webViewLink") or f"https://drive.google.com/open?id={f['id']}"
            return f
        except Exception as exc:
            raise GoogleDriveError(f"Upload Google Drive thất bại ({path.name}): {exc}") from exc

    def upload_bytes(self, name: str, content: bytes, parent_id: str, mime_type: str = "") -> dict:
        *_head, MediaFileUpload, MediaIoBaseUpload, _HttpError = _imports()
        mime = mime_type or mimetypes.guess_type(name)[0] or "application/octet-stream"
        media = MediaIoBaseUpload(io.BytesIO(content), mimetype=mime, resumable=True)
        body = {"name": name, "parents": [parent_id]}
        try:
            f = self.service.files().create(
                body=body, media_body=media, fields="id,name,webViewLink,webContentLink,size,mimeType", supportsAllDrives=True
            ).execute()
            f["webViewLink"] = f.get("webViewLink") or f"https://drive.google.com/open?id={f['id']}"
            return f
        except Exception as exc:
            raise GoogleDriveError(f"Upload Google Drive thất bại ({name}): {exc}") from exc

    # ---------- Permissions / RBAC ----------
    def permissions(self, root_id: str) -> list[dict]:
        try:
            res = self.service.permissions().list(
                fileId=root_id,
                fields="permissions(id,type,role,emailAddress,displayName,deleted,pendingOwner)",
                supportsAllDrives=True,
            ).execute()
            return list(res.get("permissions") or [])
        except Exception as exc:
            raise GoogleDriveError(f"Không đọc được danh sách phân quyền Drive: {exc}") from exc

    def current_identity(self, root_id: str, actor_email: str = "") -> DriveIdentity:
        info = self.file_info(root_id)
        user = {}
        try:
            user = self.current_user()
        except Exception:
            pass
        email = (actor_email or self.actor_email or user.get("emailAddress") or "").strip().lower()
        name = str(user.get("displayName") or "")
        shared = bool(info.get("driveId"))
        owned = bool(info.get("ownedByMe"))
        caps = info.get("capabilities") or {}

        # My Drive: ownership and edit capability are the most reliable way to
        # infer the authenticated user's role, including group/domain sharing.
        if not shared:
            if owned:
                return DriveIdentity(email=email, name=name, role="admin", drive_role="owner", shared_drive=False, owned_by_me=True)
            if bool(caps.get("canEdit") or caps.get("canAddChildren")):
                return DriveIdentity(email=email, name=name, role="update", drive_role="writer", shared_drive=False, owned_by_me=False)
            # If files.get succeeded, the user can at least read the root folder.
            return DriveIdentity(email=email, name=name, role="read", drive_role="reader", shared_drive=False, owned_by_me=False)

        # Shared Drive compatibility retained for Desktop/legacy setups.
        matched = None
        if email:
            try:
                for p in self.permissions(root_id):
                    if str(p.get("emailAddress") or "").strip().lower() == email:
                        matched = p
                        break
            except Exception:
                matched = None
        drive_role = str((matched or {}).get("role") or "")
        role = app_role_from_drive_role(drive_role)
        return DriveIdentity(email=email, name=name, role=role, drive_role=drive_role, shared_drive=True, owned_by_me=owned)

    def set_user_role(self, root_id: str, email: str, app_role: str) -> dict:
        email = str(email or "").strip().lower()
        if not email or "@" not in email:
            raise GoogleDriveError("Email người dùng không hợp lệ.")
        info = self.file_info(root_id)
        shared = bool(info.get("driveId"))
        app_role = app_role if app_role in {"read", "update", "admin"} else "read"
        if app_role == "admin" and not shared:
            raise GoogleDriveError(
                "My Drive chỉ có một Owner. Quyền Admin của app được dành cho Owner thư mục gốc. "
                "App không tự chuyển quyền sở hữu vì thao tác đó có thể làm mất quyền quản trị của chủ hiện tại."
            )
        target_role = {"read": "reader", "update": "writer", "admin": "organizer"}[app_role]
        existing = None
        for p in self.permissions(root_id):
            if str(p.get("emailAddress") or "").strip().lower() == email:
                existing = p
                break
        try:
            if existing:
                return self.service.permissions().update(
                    fileId=root_id, permissionId=existing["id"], body={"role": target_role},
                    fields="id,type,role,emailAddress,displayName", supportsAllDrives=True,
                ).execute()
            return self.service.permissions().create(
                fileId=root_id, body={"type": "user", "role": target_role, "emailAddress": email},
                sendNotificationEmail=True, fields="id,type,role,emailAddress,displayName", supportsAllDrives=True,
            ).execute()
        except Exception as exc:
            raise GoogleDriveError(f"Không cập nhật được quyền cho {email}: {exc}") from exc

    def remove_user(self, root_id: str, permission_id: str):
        try:
            self.service.permissions().delete(fileId=root_id, permissionId=permission_id, supportsAllDrives=True).execute()
        except Exception as exc:
            raise GoogleDriveError(f"Không xóa được quyền: {exc}") from exc


def app_role_from_drive_role(role: str) -> str:
    role = str(role or "")
    if role in {"owner", "organizer"}:
        return "admin"
    if role in {"writer", "fileOrganizer"}:
        return "update"
    if role in {"reader", "commenter"}:
        return "read"
    return "unknown"


def drive_role_label(role: str) -> str:
    return {
        "owner": "Owner", "organizer": "Manager", "fileOrganizer": "Content manager",
        "writer": "Editor/Contributor", "commenter": "Commenter", "reader": "Viewer",
    }.get(str(role or ""), str(role or ""))


def _sanitize_name(value: str) -> str:
    value = re.sub(r"[\\/:*?\"<>|]+", "_", str(value or "").strip())
    return value[:120] or "Tai_lieu"


def disconnect_desktop_drive():
    try:
        DRIVE_TOKEN_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def desktop_drive_status(interactive: bool = False) -> tuple[GoogleDriveService | None, DriveIdentity, dict]:
    cfg = load_app_settings()
    if not cfg.get("drive_enabled"):
        return None, DriveIdentity(role="admin"), {}
    root_id = extract_drive_id(str(cfg.get("drive_root_folder_id") or cfg.get("drive_root_folder_url") or ""))
    try:
        svc = GoogleDriveService.desktop(interactive=interactive)
        if not root_id:
            root = svc.ensure_root_folder("", str(cfg.get("drive_root_folder_name") or "QLDA Xây dựng"))
            root_id = root["id"]
            url = root.get("webViewLink") or f"https://drive.google.com/drive/folders/{root_id}"
            save_app_settings({"drive_root_folder_id": root_id, "drive_root_folder_url": url})
        root = svc.file_info(root_id)
        identity = svc.current_identity(root_id)
        return svc, identity, root
    except Exception:
        return None, DriveIdentity(role="unknown"), {}
