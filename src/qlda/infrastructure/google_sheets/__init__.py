from __future__ import annotations

# Admin-managed Google OAuth is stored encrypted outside the Git checkout.
# Re-apply it to the current QLDA process whenever this connector package loads,
# while keeping qlda.env/Streamlit Secrets as backward-compatible fallbacks.
try:
    from qlda.runtime_core.google_oauth_settings import apply_to_environment

    apply_to_environment()
except Exception:
    # Connector import must remain available for public-link mode even when the
    # managed OAuth store is absent, locked, or not yet configured.
    pass

from .client import GoogleSheetsClient, GoogleSheetsConfigError

__all__ = ["GoogleSheetsClient", "GoogleSheetsConfigError"]
