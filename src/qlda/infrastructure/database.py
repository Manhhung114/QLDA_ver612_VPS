from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from qlda.runtime_core.bootstrap import initialize_database_runtime
from qlda.runtime_core import project_store


def make_database() -> Any:
    """Create the packaged PostgreSQL-backed project database.

    V7.6 removes the repository-root compatibility loader. The stable project
    database API remains intentionally SQLite-shaped internally so proven business
    persistence code keeps its transaction semantics while PostgreSQL is the VPS
    durable backend.
    """
    initialize_database_runtime()
    label = Path(
        os.environ.get(
            "QLDA_WORKER_DB_LABEL",
            "/opt/qlda/shared/qlda-worker.db",
        )
    )
    return project_store.CloudDatabase(label)
