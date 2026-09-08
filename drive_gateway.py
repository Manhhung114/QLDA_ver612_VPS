from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from typing import Any

import requests


class DriveGatewayError(RuntimeError):
    pass


@dataclass
class DriveGatewayConfig:
    webapp_url: str
    api_token: str
    timeout: int = 90
    legacy_max_upload_mb: int = 30
    direct_max_upload_mb: int = 2048
    backend: str = "drive"
    local_storage_root: str = "/opt/qlda/data"
    public_base_url: str = ""
    local_upload_secret: str = ""

    @classmethod
    def from_values(
        cls,
        webapp_url: str = "",
        api_token: str = "",
        timeout: int | str = 90,
        legacy_max_upload_mb: int | str = 30,
        direct_max_upload_mb: int | str = 2048,
        max_upload_mb: int | str | None = None,
        *,
        backend: str = "drive",
        local_storage_root: str = "/opt/qlda/data",
        public_base_url: str = "",
        local_upload_secret: str = "",
    ) -> "DriveGatewayConfig":
        try:
            timeout_i = max(10, int(timeout))
        except Exception:
            timeout_i = 90
        if max_upload_mb is not None:
            legacy_max_upload_mb = max_upload_mb
        is_local = str(backend or "drive").strip().lower() == "local"
        try:
            legacy_cap = 1024 if is_local else 40
            legacy_i = max(1, min(legacy_cap, int(legacy_max_upload_mb)))
        except Exception:
            legacy_i = 200 if is_local else 30
        try:
            direct_i = max(1, min(4096 if is_local else 2048, int(direct_max_upload_mb)))
        except Exception:
            direct_i = 2048
        return cls(
            str(webapp_url or "").strip(),
            str(api_token or "").strip(),
            timeout_i,
            legacy_i,
            direct_i,
            "local" if is_local else "drive",
            str(local_storage_root or "/opt/qlda/data").strip(),
            str(public_base_url or "").strip().rstrip("/"),
            str(local_upload_secret or "").strip(),
        )

    @property
    def configured(self) -> bool:
        if self.backend == "local":
            return bool(self.local_storage_root and len(self.local_upload_secret) >= 32)
        return self.webapp_url.startswith("https://script.google.com/") and bool(self.api_token)

    @property
    def local(self) -> bool:
        return self.backend == "local"


class DriveGateway:
    """QLDA storage/auth gateway.

    ``backend=drive`` keeps the historical Google Apps Script implementation.
    ``backend=local`` routes the same API to PostgreSQL + VPS filesystem, so the
    rest of the QLDA application does not need two separate code paths.
    """

    def __init__(self, config: DriveGatewayConfig):
        self.config = config

    def _post(self, action: str, payload: dict[str, Any] | None = None, session_token: str = "") -> dict[str, Any]:
        if self.config.local:
            os.environ.setdefault("QLDA_LOCAL_STORAGE_ROOT", self.config.local_storage_root)
            os.environ.setdefault("QLDA_PUBLIC_BASE_URL", self.config.public_base_url)
            os.environ.setdefault("QLDA_LOCAL_UPLOAD_SECRET", self.config.local_upload_secret)
            os.environ.setdefault("QLDA_LOCAL_LEGACY_MAX_UPLOAD_MB", str(self.config.legacy_max_upload_mb))
            os.environ.setdefault("QLDA_LOCAL_DIRECT_MAX_UPLOAD_MB", str(self.config.direct_max_upload_mb))
            try:
                from local_vps_backend_v622 import LocalVPSError, dispatch
                return dispatch(action, payload, session_token)
            except LocalVPSError as exc:
                raise DriveGatewayError(str(exc)) from exc
            except DriveGatewayError:
                raise
            except Exception as exc:
                raise DriveGatewayError(f"VPS Local Storage lỗi: {exc}") from exc

        if not self.config.configured:
            raise DriveGatewayError("Chưa cấu hình QLDA_DRIVE_WEBAPP_URL / QLDA_DRIVE_API_TOKEN.")
        body: dict[str, Any] = {"action": action, "api_token": self.config.api_token}
        if payload:
            body.update(payload)
        if session_token:
            body["session_token"] = session_token
        try:
            resp = requests.post(
                self.config.webapp_url,
                json=body,
                timeout=self.config.timeout,
                allow_redirects=True,
                headers={"User-Agent": "QLDA-XayDung-V6.22/1.0"},
            )
        except requests.RequestException as exc:
            raise DriveGatewayError(f"Không kết nối được Google Drive Gateway: {exc}") from exc
        if resp.status_code >= 400:
            raise DriveGatewayError(f"Google Drive Gateway HTTP {resp.status_code}: {resp.text[:500]}")
        try:
            data = resp.json()
        except Exception as exc:
            raise DriveGatewayError(
                "Google Drive Gateway trả về dữ liệu không phải JSON. Kiểm tra URL Web App phải kết thúc bằng /exec."
            ) from exc
        if not isinstance(data, dict):
            raise DriveGatewayError("Google Drive Gateway trả về dữ liệu không hợp lệ.")
        if not data.get("ok", False):
            raise DriveGatewayError(str(data.get("error") or "Google Drive Gateway báo lỗi không xác định."))
        return data

    def health(self) -> dict[str, Any]:
        return self._post("health")

    def bootstrap_admin(self, email: str, name: str, password: str, bootstrap_code: str) -> dict[str, Any]:
        return self._post(
            "bootstrap",
            {"email": email, "name": name, "password": password, "bootstrap_code": bootstrap_code},
        )

    def login(self, email: str, password: str) -> dict[str, Any]:
        return self._post("login", {"email": email, "password": password})

    def me(self, session_token: str) -> dict[str, Any]:
        return self._post("me", session_token=session_token)

    def root_info(self, session_token: str) -> dict[str, Any]:
        return self._post("root_info", session_token=session_token)

    def list_users(self, session_token: str) -> list[dict[str, Any]]:
        return list(self._post("list_users", session_token=session_token).get("users") or [])

    def approval_users(self, session_token: str) -> list[dict[str, Any]]:
        return list(self._post("approval_users", session_token=session_token).get("users") or [])

    def set_user(self, session_token: str, email: str, name: str, role: str, password: str = "", approval_role: str = "") -> dict[str, Any]:
        effective_approval_role = str(approval_role or "").strip().upper()
        if str(role or "").strip().lower() == "admin" and not effective_approval_role:
            effective_approval_role = "PROJECT_MANAGEMENT"
        legacy_group = {
            "": "none",
            "CONTRACTOR": "contractor",
            "SITE_MANAGEMENT": "site_management",
            "CONSULTANT": "tvgs",
            "PROJECT_MANAGEMENT": "bqlda",
        }.get(effective_approval_role, "none")
        return self._post(
            "set_user",
            {
                "email": email,
                "name": name,
                "role": role,
                "password": password,
                "approval_role": effective_approval_role,
                "approval_group": legacy_group,
            },
            session_token=session_token,
        )

    def send_approval_email(self, session_token: str, *, to_email: str, subject: str, body: str, app_url: str = "") -> dict[str, Any]:
        return self._post(
            "send_approval_email",
            {"to_email": to_email, "subject": subject, "body": body, "app_url": app_url},
            session_token=session_token,
        )

    def delete_user(self, session_token: str, email: str) -> dict[str, Any]:
        return self._post("delete_user", {"email": email}, session_token=session_token)

    def change_password(self, session_token: str, old_password: str, new_password: str) -> dict[str, Any]:
        return self._post(
            "change_password",
            {"old_password": old_password, "new_password": new_password},
            session_token=session_token,
        )

    def create_upload_ticket(
        self,
        session_token: str,
        *,
        project_code: str,
        kind: str,
        subtype: str,
        record_code: str,
        upload_purpose: str = "",
    ) -> dict[str, Any]:
        data = self._post(
            "create_upload_ticket",
            {
                "project_code": project_code,
                "kind": kind,
                "subtype": subtype,
                "record_code": record_code,
                "upload_purpose": str(upload_purpose or ""),
                "max_bytes": self.config.direct_max_upload_mb * 1024 * 1024,
                "webapp_url": self.config.webapp_url,
            },
            session_token=session_token,
        )
        return dict(data.get("upload") or {})

    def list_record_files(
        self,
        session_token: str,
        *,
        project_code: str,
        kind: str,
        subtype: str,
        record_code: str,
        include_history: bool = False,
    ) -> dict[str, Any]:
        data = self._post(
            "list_record_files",
            {
                "project_code": project_code,
                "kind": kind,
                "subtype": subtype,
                "record_code": record_code,
                "include_history": bool(include_history),
            },
            session_token=session_token,
        )
        return {"files": list(data.get("files") or []), "folder": dict(data.get("folder") or {})}

    def record_file_counts(
        self,
        session_token: str,
        *,
        project_code: str,
        kind: str,
        subtype: str,
        record_codes: list[str] | tuple[str, ...],
    ) -> dict[str, dict[str, Any]]:
        clean_codes = [str(x or "").strip() for x in record_codes if str(x or "").strip()]
        if not clean_codes:
            return {}
        data = self._post(
            "record_file_counts",
            {
                "project_code": project_code,
                "kind": kind,
                "subtype": subtype,
                "record_codes": clean_codes[:500],
            },
            session_token=session_token,
        )
        raw = data.get("counts") or {}
        return {str(k): dict(v or {}) for k, v in raw.items()}

    def file_info(self, session_token: str, file_id: str) -> dict[str, Any]:
        data = self._post("file_info", {"file_id": file_id}, session_token=session_token)
        return dict(data.get("file") or {})

    def upload_bytes(
        self,
        session_token: str,
        *,
        project_code: str,
        kind: str,
        subtype: str,
        record_code: str,
        name: str,
        content: bytes,
        mime_type: str = "",
        upload_purpose: str = "",
    ) -> dict[str, Any]:
        if self.config.local:
            try:
                from local_vps_backend_v622 import LocalVPSError, save_bytes
                return save_bytes(
                    session_token,
                    project_code=project_code,
                    kind=kind,
                    subtype=subtype,
                    record_code=record_code,
                    name=name,
                    content=content,
                    mime_type=mime_type,
                    upload_purpose=upload_purpose,
                )
            except LocalVPSError as exc:
                raise DriveGatewayError(str(exc)) from exc
        if len(content) > self.config.legacy_max_upload_mb * 1024 * 1024:
            size_mb = len(content) / (1024 * 1024)
            raise DriveGatewayError(
                f"File {name} ({size_mb:.1f} MB) vượt giới hạn legacy {self.config.legacy_max_upload_mb} MB. "
                "Hãy dùng nút tải file lớn trực tiếp."
            )
        encoded = base64.b64encode(content).decode("ascii")
        data = self._post(
            "upload_legacy",
            {
                "project_code": project_code,
                "kind": kind,
                "subtype": subtype,
                "record_code": record_code,
                "file_name": name,
                "mime_type": mime_type or "application/octet-stream",
                "upload_purpose": str(upload_purpose or ""),
                "file_base64": encoded,
            },
            session_token=session_token,
        )
        return dict(data.get("file") or {})

    def download_bytes(self, session_token: str, file_id: str) -> tuple[str, str, bytes]:
        if self.config.local:
            try:
                from local_vps_backend_v622 import LocalVPSError, download_bytes
                return download_bytes(session_token, file_id)
            except LocalVPSError as exc:
                raise DriveGatewayError(str(exc)) from exc
        data = self._post("download_legacy", {"file_id": file_id}, session_token=session_token)
        item = dict(data.get("file") or {})
        try:
            raw = base64.b64decode(item.get("file_base64") or "")
        except Exception as exc:
            raise DriveGatewayError("Không giải mã được nội dung file từ Google Drive.") from exc
        return str(item.get("name") or "attachment"), str(item.get("mime_type") or "application/octet-stream"), raw

    def trash_file(self, session_token: str, file_id: str) -> dict[str, Any]:
        return self._post("trash_file", {"file_id": file_id}, session_token=session_token)

    def clear_cache(self, scope: str = "all") -> None:
        return None

    def close(self) -> None:
        return None


def config_from_streamlit(st_module) -> DriveGatewayConfig:
    def secret(name: str, default: str = "") -> str:
        try:
            if name in st_module.secrets:
                return str(st_module.secrets[name])
        except Exception:
            pass
        return str(os.environ.get(name, default) or default)

    backend = secret("QLDA_STORAGE_BACKEND", "drive").strip().lower()
    is_local = backend == "local"
    return DriveGatewayConfig.from_values(
        secret("QLDA_DRIVE_WEBAPP_URL"),
        secret("QLDA_DRIVE_API_TOKEN"),
        secret("QLDA_DRIVE_TIMEOUT", "90"),
        secret("QLDA_LOCAL_LEGACY_MAX_UPLOAD_MB", "200") if is_local else secret("QLDA_DRIVE_LEGACY_MAX_UPLOAD_MB", "30"),
        secret("QLDA_LOCAL_DIRECT_MAX_UPLOAD_MB", "2048") if is_local else secret("QLDA_DRIVE_DIRECT_MAX_UPLOAD_MB", "2048"),
        backend=backend,
        local_storage_root=secret("QLDA_LOCAL_STORAGE_ROOT", "/opt/qlda/data"),
        public_base_url=secret("QLDA_PUBLIC_BASE_URL", ""),
        local_upload_secret=secret("QLDA_LOCAL_UPLOAD_SECRET", ""),
    )
