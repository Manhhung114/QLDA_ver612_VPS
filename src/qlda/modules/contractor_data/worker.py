from __future__ import annotations

import argparse
import logging
import os
import signal
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from qlda.runtime_core.bootstrap import initialize_database_runtime


LOG = logging.getLogger("qlda.contractor_data.worker")
_STOP = False
_VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def _signal_handler(signum, frame) -> None:  # pragma: no cover - OS integration
    del signum, frame
    global _STOP
    _STOP = True


def _db_path() -> Path:
    root = Path(os.environ.get("QLDA_RUNTIME_DATA_ROOT", "/opt/qlda/data/runtime"))
    return Path(os.environ.get("QLDA_DB_PATH", str(root / "qlda_cloud.db")))


def _env_bool(name: str, default: bool = False) -> bool:
    raw = str(os.environ.get(name, "") or "").strip().lower()
    if not raw:
        return bool(default)
    return raw in {"1", "true", "yes", "on"}


def _row_value(row: Any, key: str, index: int = 0):
    if row is None:
        return None
    try:
        return row[key]
    except Exception:
        try:
            return row[index]
        except Exception:
            return None


def build_db():
    initialize_database_runtime()
    from qlda.runtime_core.project_store import CloudDatabase

    return CloudDatabase(_db_path())


def project_ids_with_data_spaces(db) -> list[int]:
    """Master projects used only for source synchronization."""
    from qlda.infrastructure.contractor_data_hub import ContractorDataHubRepository

    ContractorDataHubRepository(db)  # idempotent schema bootstrap
    with db.connect() as connection:
        rows = connection.execute(
            "SELECT DISTINCT master_project_id FROM contractor_data_spaces WHERE enabled=1 ORDER BY master_project_id"
        ).fetchall()
    out: list[int] = []
    for row in rows:
        value = _row_value(row, "master_project_id", 0)
        try:
            pid = int(value or 0)
        except Exception:
            pid = 0
        if pid > 0:
            out.append(pid)
    return out


def project_ids_for_supervisor(db) -> list[int]:
    """Return one AI tenant id per active contractor workspace.

    A project with contractor workspaces never receives an aggregate background AI
    supervisor. Each contractor is evaluated independently. Standalone projects
    without contractors remain one valid AI tenant for backward compatibility.
    """
    with db.connect() as connection:
        try:
            rows = connection.execute(
                """SELECT workspace_project_id AS id
                FROM project_contractors
                WHERE status='Đang hoạt động'
                UNION
                SELECT p.id AS id
                FROM projects p
                WHERE NOT EXISTS (
                    SELECT 1 FROM project_contractors pc
                    WHERE pc.master_project_id=p.id
                )
                ORDER BY id"""
            ).fetchall()
        except Exception:
            rows = connection.execute("SELECT id FROM projects ORDER BY id").fetchall()
    out: list[int] = []
    for row in rows:
        try:
            pid = int(_row_value(row, "id", 0) or 0)
        except Exception:
            pid = 0
        if pid > 0 and pid not in out:
            out.append(pid)
    return out


def run_project(db, master_project_id: int) -> dict[str, Any]:
    from qlda.application.contractor_data_hub import ContractorDataHubService
    from qlda.infrastructure.google_sheets.drive import GoogleWorkspaceClient
    from qlda.runtime_core.google_connection_store import load_project_connection, save_project_connection
    from qlda.runtime_core.google_oauth_settings import apply_to_environment

    apply_to_environment()
    stored = load_project_connection(int(master_project_id))
    token_state = dict(stored.get("token_state") or {})
    client = GoogleWorkspaceClient(token_state) if token_state else None
    service = ContractorDataHubService(db, client=client)
    result = service.sync_due_spaces(int(master_project_id))

    if client is not None and client.authorized:
        save_project_connection(
            int(master_project_id),
            client.token_state(),
            account_email=str(stored.get("account_email") or ""),
            account_name=str(stored.get("account_name") or ""),
            scopes=[GoogleWorkspaceClient.SHEETS_SCOPE, GoogleWorkspaceClient.DRIVE_SCOPE],
        )
    return {
        "project_id": int(master_project_id),
        "oauth_connected": bool(token_state),
        **result,
    }


def run_daily_supervisor_pass(db) -> list[dict[str, Any]]:
    """Run each contractor AI independently once/day after 06:20 Vietnam time."""
    if not _env_bool("QLDA_AUTONOMY_SUPERVISOR_ENABLED", True):
        return []
    now = datetime.now(_VN_TZ)
    if (now.hour, now.minute) < (6, 20):
        return []

    from qlda.autonomy.runtime import run_daily_supervisor_if_due

    results: list[dict[str, Any]] = []
    for workspace_id in project_ids_for_supervisor(db):
        try:
            result = run_daily_supervisor_if_due(db, workspace_id, actor="AI Supervisor")
            results.append(result)
            if result.get("skipped"):
                LOG.debug("AI supervisor workspace=%s already ran today", workspace_id)
            else:
                LOG.info(
                    "AI supervisor workspace=%s health=%s findings=%s integrity=%s",
                    workspace_id,
                    result.get("health_score"),
                    len(result.get("findings") or []),
                    (result.get("integrity") or {}).get("score"),
                )
        except Exception as exc:
            LOG.exception("AI supervisor failed for workspace=%s: %s", workspace_id, exc)
            results.append({"workspace_project_id": workspace_id, "error": str(exc)})
    return results


def run_once(db=None) -> list[dict[str, Any]]:
    db = db or build_db()
    results: list[dict[str, Any]] = []
    for project_id in project_ids_with_data_spaces(db):
        try:
            result = run_project(db, project_id)
            results.append(result)
            if result.get("skipped"):
                LOG.info("project=%s skipped=%s", project_id, result.get("reason"))
            else:
                LOG.info(
                    "project=%s oauth=%s due=%s success=%s errors=%s records=%s",
                    project_id,
                    result.get("oauth_connected", False),
                    result.get("due", 0),
                    result.get("success", 0),
                    result.get("errors", 0),
                    result.get("records", 0),
                )
        except Exception as exc:
            LOG.exception("Contractor data sync failed for project=%s: %s", project_id, exc)
            results.append({"project_id": project_id, "error": str(exc)})

    # Source sync stays master-project aware; AI supervision is then fanned out to
    # isolated contractor workspaces so snapshots/events/twins never mix tenants.
    run_daily_supervisor_pass(db)
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="QLDA contractor data + isolated AI automation background worker")
    parser.add_argument(
        "--poll-seconds",
        type=int,
        default=int(os.environ.get("QLDA_CONTRACTOR_DATA_SYNC_POLL_SECONDS", "300") or 300),
        help="How often to check due contractor data spaces and automation jobs (minimum 60 seconds).",
    )
    parser.add_argument("--once", action="store_true", help="Run one due-sync/automation pass then exit.")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=os.environ.get("QLDA_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    poll_seconds = max(60, int(args.poll_seconds))
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    db = build_db()
    if args.once:
        run_once(db)
        return 0

    LOG.info("Contractor data + isolated AI automation worker started, poll=%ss", poll_seconds)
    while not _STOP:
        started = time.monotonic()
        run_once(db)
        elapsed = time.monotonic() - started
        remaining = max(1.0, poll_seconds - elapsed)
        deadline = time.monotonic() + remaining
        while not _STOP and time.monotonic() < deadline:
            time.sleep(min(1.0, deadline - time.monotonic()))
    LOG.info("Contractor data + AI automation worker stopped")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
