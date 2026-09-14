from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from qlda.runtime import legacy_import


def make_database() -> Any:
    """Create the PostgreSQL-backed CloudDatabase compatibility object.

    V7.5 removes ``qlda.shared.legacy``; the remaining compatibility runtime is
    centralized here because AI and the proven import semantic helpers still use
    the historical CloudDatabase API while PostgreSQL is the durable backend.
    """
    postgres = legacy_import("postgres_backend_v622")
    postgres.install_postgres_backend()
    cloud_database = legacy_import("cloud_db").CloudDatabase
    label = Path(
        os.environ.get(
            "QLDA_WORKER_DB_LABEL",
            "/opt/qlda/shared/qlda-worker.db",
        )
    )
    return cloud_database(label)
