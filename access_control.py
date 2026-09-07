from __future__ import annotations

from dataclasses import dataclass

from settings_store import load_app_settings
from google_drive_service import desktop_drive_status


@dataclass
class AccessState:
    role: str = "admin"
    email: str = ""
    label: str = "Admin (Local)"
    drive_enabled: bool = False
    connected: bool = False

    @property
    def can_read(self):
        return self.role in {"read", "update", "admin"}

    @property
    def can_update(self):
        return self.role in {"update", "admin"}

    @property
    def can_admin(self):
        return self.role == "admin"


def desktop_access_state() -> AccessState:
    cfg = load_app_settings()
    if not cfg.get("drive_enabled"):
        return AccessState(role="admin", label="Admin (Local)", drive_enabled=False, connected=False)
    svc, identity, _root = desktop_drive_status(interactive=False)
    if svc is None:
        return AccessState(role="unknown", label="Drive chưa kết nối", drive_enabled=True, connected=False)
    return AccessState(
        role=identity.role,
        email=identity.email,
        label=f"{identity.label} • {identity.email or 'Google Drive'}",
        drive_enabled=True,
        connected=True,
    )
