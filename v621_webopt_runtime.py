from __future__ import annotations

import copy
import json
import sqlite3
import threading
import time
from contextlib import contextmanager

import requests
from requests.adapters import HTTPAdapter

from v615_runtime_patch import install_db_patch as _install_workflow_compat


_TTLS = {
    "me": 15.0,
    "list_record_files": 5.0,
    "record_file_counts": 5.0,
    "file_info": 30.0,
    "approval_users": 30.0,
    "list_users": 20.0,
    "root_info": 60.0,
}
_FILE_ACTIONS = {"list_record_files", "record_file_counts", "file_info"}
_USER_ACTIONS = {"me", "approval_users", "list_users", "root_info"}


def _install_sqlite_fast_path() -> None:
    from cloud_db import CloudDatabase

    # V6.22: DATABASE_URL activates a PostgreSQL-compatible CloudDatabase before
    # this runtime is installed. Never overwrite its connection/schema methods
    # with SQLite PRAGMA/WAL hooks.
    if getattr(CloudDatabase, "_v622_postgres", False):
        return
    if getattr(CloudDatabase, "_v621_webopt_sqlite", False):
        return

    original_init = CloudDatabase.__init__
    original_create_tables = CloudDatabase.create_tables

    def fast_init(self, path):
        self._v621_pragma_lock = threading.Lock()
        self._v621_wal_ready = False
        original_init(self, path)

    @contextmanager
    def fast_connect(self):
        conn = sqlite3.connect(
            self.path,
            timeout=60,
            check_same_thread=False,
            cached_statements=512,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=60000")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.execute("PRAGMA cache_size=-20000")
        try:
            conn.execute("PRAGMA mmap_size=134217728")
        except sqlite3.DatabaseError:
            pass
        if not getattr(self, "_v621_wal_ready", False):
            with self._v621_pragma_lock:
                if not self._v621_wal_ready:
                    conn.execute("PRAGMA journal_mode=WAL")
                    self._v621_wal_ready = True
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def create_tables_fast(self):
        original_create_tables(self)
        with self.connect() as c:
            c.executescript(
                """
                CREATE INDEX IF NOT EXISTS idx_tasks_project_source_order
                    ON tasks(project_id, source_type, source_task_id, start_date, id);
                CREATE INDEX IF NOT EXISTS idx_documents_project_type_date
                    ON documents(project_id, doc_type, issue_date, id);
                CREATE INDEX IF NOT EXISTS idx_document_attachments_document
                    ON document_attachments(document_id, id);
                CREATE INDEX IF NOT EXISTS idx_drawings_project_type_date
                    ON drawings(project_id, drawing_type, received_date, id);
                CREATE INDEX IF NOT EXISTS idx_drawing_attachments_drawing
                    ON drawing_attachments(drawing_id, id);
                CREATE INDEX IF NOT EXISTS idx_approval_workflows_lookup
                    ON approval_workflows(project_id, record_kind, subtype, record_id);
                CREATE INDEX IF NOT EXISTS idx_approval_workflows_record_fast
                    ON approval_workflows(project_id, record_kind, record_id, current_stage, id);
                CREATE INDEX IF NOT EXISTS idx_approval_steps_workflow_order
                    ON approval_steps(workflow_id, stage_order, stage_code);
                """
            )

    def workflows_for_records(self, project_id, record_kind, subtype, record_ids):
        # V6.15 compatibility: lookup intentionally does not depend on subtype,
        # because legacy rows may contain old/case-drifted subtype values.
        ids = []
        seen = set()
        for value in record_ids or []:
            try:
                rid = int(value)
            except Exception:
                continue
            if rid > 0 and rid not in seen:
                seen.add(rid)
                ids.append(rid)
        if not ids:
            return {}
        out = {}
        with self.connect() as c:
            for start in range(0, len(ids), 400):
                chunk = ids[start:start + 400]
                marks = ",".join("?" for _ in chunk)
                rows = c.execute(
                    f"""SELECT * FROM approval_workflows
                           WHERE project_id=? AND LOWER(TRIM(record_kind))=?
                             AND record_id IN ({marks})
                           ORDER BY id""",
                    [int(project_id), str(record_kind).strip().lower(), *chunk],
                ).fetchall()
                for row in rows:
                    out[int(row["record_id"])] = row
        return out

    CloudDatabase.__init__ = fast_init
    CloudDatabase.connect = fast_connect
    CloudDatabase.create_tables = create_tables_fast
    CloudDatabase.approval_workflows_for_records = workflows_for_records
    CloudDatabase._v621_webopt_sqlite = True


def _install_drive_fast_path() -> None:
    from drive_gateway import DriveGateway, DriveGatewayError

    if getattr(DriveGateway, "_v621_webopt_drive", False):
        return

    original_init = DriveGateway.__init__

    def fast_init(self, config):
        original_init(self, config)
        self._v621_http = requests.Session()
        adapter = HTTPAdapter(pool_connections=20, pool_maxsize=20, max_retries=0)
        self._v621_http.mount("https://", adapter)
        self._v621_http.mount("http://", adapter)
        self._v621_http.headers.update({"User-Agent": "QLDA-XayDung-V6.22-PostgreSQL/1.0"})
        self._v621_cache = {}
        self._v621_cache_lock = threading.RLock()

    def clear_cache(self, scope: str = "all") -> None:
        scope = str(scope or "all").strip().lower()
        wanted = _FILE_ACTIONS if scope == "files" else _USER_ACTIONS if scope == "users" else None
        with self._v621_cache_lock:
            if wanted is None:
                self._v621_cache.clear()
                return
            for key in list(self._v621_cache):
                if key[0] in wanted:
                    self._v621_cache.pop(key, None)

    def close(self) -> None:
        try:
            self._v621_http.close()
        except Exception:
            pass

    def fast_post(self, action: str, payload=None, session_token: str = ""):
        if not self.config.configured:
            raise DriveGatewayError(
                "Chưa cấu hình QLDA_DRIVE_WEBAPP_URL / QLDA_DRIVE_API_TOKEN."
            )
        payload = dict(payload or {})
        ttl = float(_TTLS.get(str(action), 0.0))
        cache_key = None
        now = time.monotonic()
        if ttl > 0:
            payload_key = json.dumps(
                payload, ensure_ascii=False, sort_keys=True,
                separators=(",", ":"), default=str,
            )
            cache_key = (str(action), str(session_token or ""), payload_key)
            with self._v621_cache_lock:
                hit = self._v621_cache.get(cache_key)
                if hit and hit[0] > now:
                    return copy.deepcopy(hit[1])

        body = {"action": action, "api_token": self.config.api_token}
        if payload:
            body.update(payload)
        if session_token:
            body["session_token"] = session_token
        try:
            resp = self._v621_http.post(
                self.config.webapp_url,
                json=body,
                timeout=self.config.timeout,
                allow_redirects=True,
            )
        except requests.RequestException as exc:
            raise DriveGatewayError(f"Không kết nối được Google Drive Gateway: {exc}") from exc
        if resp.status_code >= 400:
            raise DriveGatewayError(
                f"Google Drive Gateway HTTP {resp.status_code}: {resp.text[:500]}"
            )
        try:
            data = resp.json()
        except Exception as exc:
            raise DriveGatewayError(
                "Google Drive Gateway trả về dữ liệu không phải JSON. Kiểm tra URL Web App /exec."
            ) from exc
        if not isinstance(data, dict):
            raise DriveGatewayError("Google Drive Gateway trả về dữ liệu không hợp lệ.")
        if not data.get("ok", False):
            raise DriveGatewayError(str(data.get("error") or "Google Drive Gateway báo lỗi không xác định."))

        if cache_key is not None:
            with self._v621_cache_lock:
                self._v621_cache[cache_key] = (now + ttl, copy.deepcopy(data))
                if len(self._v621_cache) > 256:
                    expired = [k for k, v in self._v621_cache.items() if v[0] <= now]
                    for k in expired:
                        self._v621_cache.pop(k, None)
                    while len(self._v621_cache) > 256:
                        self._v621_cache.pop(next(iter(self._v621_cache)), None)

        if action in {"trash_file", "upload_legacy"}:
            clear_cache(self, "files")
        elif action in {"set_user", "delete_user", "change_password"}:
            clear_cache(self, "users")
        return data

    DriveGateway.__init__ = fast_init
    DriveGateway._post = fast_post
    DriveGateway.clear_cache = clear_cache
    DriveGateway.close = close
    DriveGateway._v621_webopt_drive = True


def install_runtime() -> None:
    """Install workflow/Drive optimizations; SQLite tuning only on SQLite backend."""
    _install_workflow_compat()
    _install_sqlite_fast_path()
    _install_drive_fast_path()
