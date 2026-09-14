from __future__ import annotations

"""QLDA modular monolith package.

V6.25 established module boundaries. V6.26 adds an application Service Layer
that is shared by background workers today and by HTTP adapters in V6.27.
"""

__version__ = "6.26"
ARCHITECTURE = "modular-monolith"
SERVICE_LAYER = "application-services"
