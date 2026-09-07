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

    @classmethod
    def from_values(
        cls,
        webapp_url: str = "",
        api_token: str = "",
        timeout: int | str = 90,
        legacy_max_upload_mb: int | str = 30,
        direct_max_upload_mb: int | str = 2048,
        max_upload_mb: int | str | None = None,  # backward-compatible V4.x alias
    ) -> "DriveGatewayConfig":
        try:
            timeout_i = max(10, int(timeout))
        except Exception:
            timeout_i = 90
        if max_upload_mb is not None:
            legacy_max_upload_mb = max_upload_mb
        try:
            legacy_i = max(1, min(40, int(legacy_max_upload_mb)))
        except Exception:
            legacy_i = 30
        try:
            direct_i = max(1, min(2048, int(direct_max_upload_mb)))
        except Exception:
            direct_i = 2048
        return cls(
            str(webapp_url or "").strip(),
            str(api_token or "").strip(),
            timeout_i,
            legacy_i,
            direct_i,
        )

    @property
    def configured(self) -> bool:
        return self.webapp_url.startswith("https://script.google.com/") and bool(self.api_token)


class DriveGateway:
    """QLDA V6.0 Google Apps Script control gateway.

    File bytes for the new V6.0 attachment flow do NOT pass through this Python
    client. Streamlit asks Apps Script for a short-lived upload ticket, then an Apps Script-hosted uploader reads the local file in chunks. OAuth stays server-side in Apps Script; chunks are relayed into a Google Drive resumable-upload session. Streamlit never receives file bytes.

    The old base64 upload method remains only for backwards compatibility with
    older code paths and is intentionally capped at a small size.
    """

    def __init__(self, config: DriveGatewayConfig):
        self.config = config

    def _post(self, action: str, payload: dict[str, Any] | None = None, session_token: str = "") -> dict[str, Any]:
        if not self.config.configured:
            raise DriveGatewayError(
                "Chưa cấu hình QLDA_DRIVE_WEBAPP_URL / QLDA_DRIVE_API_TOKEN. Trên Render hãy đặt tại Service → Environment."
            )
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
                headers={"User-Agent": "QLDA-XayDung-V6.0-Render/1.0"},
            )
        except requests.RequestException as exc:
            raise DriveGatewayError(f"Không kết nối được Google Drive Gateway: {exc}") from exc
        if resp.status_code >= 400:
            raise DriveGatewayError(f"Google Drive Gateway HTTP {resp.status_code}: {resp.text[:500]}")
        try:
            data = resp.json()
        except Exception as exc:
            raise DriveGatewayError(
                "Google Drive Gateway trả về dữ liệu không phải JSON. Kiểm tra URL Web App phải kết thúc bằng /exec và deployment đang hoạt động."
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
        """Danh sách user đang hoạt động dùng để định tuyến phê duyệt.

        Khác list_users (chỉ Admin), endpoint này chỉ trả publicUser và được phép
        cho mọi tài khoản đã đăng nhập để Nhà thầu có thể tự trình hồ sơ.
        """
        return list(self._post("approval_users", session_token=session_token).get("users") or [])

    def set_user(self, session_token: str, email: str, name: str, role: str, password: str = "", approval_role: str = "") -> dict[str, Any]:
        # V6.2 compatibility: some V6.0/V6.1 Apps Script deployments used
        # ``approval_group`` while newer builds use ``approval_role``. Send both
        # so updating an existing deployment does not silently lose approval role.
        effective_approval_role = str(approval_role or "").strip().upper()
        # Theo quy ước QLDA: Admin tối thiểu có quyền phê duyệt cấp Ban QLDA.
        # Nếu Admin không chọn phân loại riêng, tự gán PROJECT_MANAGEMENT.
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
                # Legacy V6.0 field/value set. This is intentionally lowercase
                # because the old Apps Script validates: none/contractor/
                # site_management/tvgs/bqlda.
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

    # ---------- V6.0 direct-to-Drive upload ----------
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
        """Create a short-lived uploader URL.

        The returned page is hosted by Apps Script. The browser sends file chunks
        directly to a Google Drive resumable session, bypassing Streamlit and the
        Apps Script request-body size limit.
        """
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
        return {
            "files": list(data.get("files") or []),
            "folder": dict(data.get("folder") or {}),
        }

    def record_file_counts(
        self,
        session_token: str,
        *,
        project_code: str,
        kind: str,
        subtype: str,
        record_codes: list[str] | tuple[str, ...],
    ) -> dict[str, dict[str, Any]]:
        """Return current Google Drive file count for many records in one Gateway call."""
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

    # ---------- Legacy small-file compatibility ----------
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
        if len(content) > self.config.legacy_max_upload_mb * 1024 * 1024:
            size_mb = len(content) / (1024 * 1024)
            raise DriveGatewayError(
                f"File {name} ({size_mb:.1f} MB) vượt giới hạn legacy {self.config.legacy_max_upload_mb} MB. "
                "V6.0 yêu cầu dùng nút 'Tải trực tiếp lên Google Drive' cho file lớn."
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
        data = self._post("download_legacy", {"file_id": file_id}, session_token=session_token)
        item = dict(data.get("file") or {})
        try:
            raw = base64.b64decode(item.get("file_base64") or "")
        except Exception as exc:
            raise DriveGatewayError("Không giải mã được nội dung file từ Google Drive.") from exc
        return str(item.get("name") or "attachment"), str(item.get("mime_type") or "application/octet-stream"), raw

    def trash_file(self, session_token: str, file_id: str) -> dict[str, Any]:
        return self._post("trash_file", {"file_id": file_id}, session_token=session_token)


def config_from_streamlit(st_module) -> DriveGatewayConfig:
    def secret(name: str, default: str = "") -> str:
        try:
            if name in st_module.secrets:
                return str(st_module.secrets[name])
        except Exception:
            pass
        return str(os.environ.get(name, default) or default)

    return DriveGatewayConfig.from_values(
        secret("QLDA_DRIVE_WEBAPP_URL"),
        secret("QLDA_DRIVE_API_TOKEN"),
        secret("QLDA_DRIVE_TIMEOUT", "90"),
        secret("QLDA_DRIVE_LEGACY_MAX_UPLOAD_MB", "30"),
        secret("QLDA_DRIVE_DIRECT_MAX_UPLOAD_MB", "2048"),
    )
