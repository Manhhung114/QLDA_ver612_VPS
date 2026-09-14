from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from qlda.shared.legacy import load_module, resolve


def make_database() -> Any:
    """Create the PostgreSQL-backed CloudDatabase used by QLDA services.

    The legacy adapter install is intentionally isolated here. Domain and
    presentation code must depend on this factory instead of installing
    PostgreSQL monkey patches themselves.
    """
    postgres = load_module("postgres_backend_v622")
    postgres.install_postgres_backend()
    cloud_database = resolve("cloud_db", "CloudDatabase")
    label = Path(
        os.environ.get(
            "QLDA_WORKER_DB_LABEL",
            "/opt/qlda/shared/qlda-worker.db",
        )
    )
    return cloud_database(label)
