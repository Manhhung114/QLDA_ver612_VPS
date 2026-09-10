from __future__ import annotations

from datetime import date, datetime, time, timedelta
from html import escape
from typing import Any, Iterable
from zoneinfo import ZoneInfo

PATCH_MARKER = "V6.22 WORK TASKS V1"
TASKS_TABLE = "project_work_tasks"
COMMENTS_TABLE = "project_work_task_comments"
HISTORY_TABLE = "project_work_task_history"
FILES_TABLE = "project_work_task_files"

PRIORITIES = ("Bình thường", "Quan trọng", "Khẩn")
STATUSES = ("MỚI", "ĐÃ NHẬN", "ĐANG XỬ LÝ", "CHỜ XÁC NHẬN", "YÊU CẦU LÀM LẠI", "HOÀN THÀNH", "ĐÓNG")
TERMINAL_STATUSES = {"HOÀN THÀNH", "ĐÓNG", "HỦY"}
MANAGEMENT_APPROVAL_ROLES = {"SITE_MANAGEMENT", "PROJECT_MANAGEMENT"}
TZ = ZoneInfo("Asia/Ho_Chi_Minh")


def _now_dt() -> datetime:
    return datetime.now(TZ).replace(tzinfo=None)


def _now() -> str:
    return _now_dt().strftime("%Y-%m-%d %H:%M:%S")


def _rowdict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    try:
        return {str(k): row[k] for k in row.keys()}
    except Exception:
        try:
            return dict(row)
        except Exception:
            return {}


def _email(value: Any) -> str:
    return str(value or "").strip().lower()


def _text(value: Any) -> str:
    return str(value or "").strip()


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, date):
        return datetime.combine(value, time.min)
    text_value = _text(value)
    if not text_value:
        return None
    normalized = text_value.replace("T", " ").replace("Z", "").split("+", 1)[0].strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%d/%m/%Y %H:%M", "%d/%m/%Y"):
        try:
            return datetime.strptime(normalized, fmt)
        except Exception:
            pass
    return None


def _due_text(value: Any) -> str:
    dt = _parse_dt(value)
    return dt.strftime("%d/%m/%Y %H:%M") if dt else _text(value)


def _actor(identity: Any) -> dict[str, str]:
    row = _rowdict(identity)
    return {
        "email": _email(row.get("email")),
        "name": _text(row.get("name") or row.get("email") or "Người dùng"),
        "role": _text(row.get("role")).lower(),
        "approval_role": _text(row.get("approval_role")).upper(),
    }


def _is_manager(identity: Any, is_admin: bool = False) -> bool:
    user = _actor(identity)
    return bool(is_admin or user["role"] == "admin" or user["approval_role"] in MANAGEMENT_APPROVAL_ROLES)


def is_overdue(task: Any, now: datetime | None = None) -> bool:
    row = _rowdict(task)
    if _text(row.get("status")).upper() in TERMINAL_STATUSES:
        return False
    due = _parse_dt(row.get("due_at"))
    return bool(due and (now or _now_dt()) > due)


def effective_status(task: Any, now: datetime | None = None) -> str:
    return "QUÁ HẠN" if is_overdue(task, now=now) else (_text(_rowdict(task).get("status")) or "MỚI")


def ensure_schema_connection(connection) -> None:
    connection.executescript(f"""
    CREATE TABLE IF NOT EXISTS {TASKS_TABLE}(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_code TEXT DEFAULT NULL,
        master_project_id INTEGER NOT NULL,
        workspace_project_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        description TEXT DEFAULT '',
        assignee_email TEXT DEFAULT '',
        assignee_name TEXT DEFAULT '',
        priority TEXT NOT NULL DEFAULT 'Bình thường',
        status TEXT NOT NULL DEFAULT 'MỚI',
        progress_percent INTEGER NOT NULL DEFAULT 0,
        source_module TEXT DEFAULT '',
        source_type TEXT DEFAULT '',
        source_id TEXT DEFAULT '',
        source_code TEXT DEFAULT '',
        source_title TEXT DEFAULT '',
        created_by_email TEXT DEFAULT '',
        created_by_name TEXT DEFAULT '',
        start_at TEXT DEFAULT '',
        due_at TEXT NOT NULL,
        completion_requested_at TEXT DEFAULT '',
        completed_at TEXT DEFAULT '',
        closed_at TEXT DEFAULT '',
        created_at TEXT DEFAULT '',
        updated_at TEXT DEFAULT '',
        archived INTEGER NOT NULL DEFAULT 0,
        FOREIGN KEY(master_project_id) REFERENCES projects(id) ON DELETE CASCADE,
        FOREIGN KEY(workspace_project_id) REFERENCES projects(id) ON DELETE CASCADE,
        UNIQUE(workspace_project_id, task_code)
    );
    CREATE INDEX IF NOT EXISTS idx_project_work_tasks_workspace ON {TASKS_TABLE}(workspace_project_id, archived, status, due_at, id);
    CREATE INDEX IF NOT EXISTS idx_project_work_tasks_assignee ON {TASKS_TABLE}(workspace_project_id, assignee_email, status, due_at);
    CREATE INDEX IF NOT EXISTS idx_project_work_tasks_creator ON {TASKS_TABLE}(workspace_project_id, created_by_email, id);
    CREATE INDEX IF NOT EXISTS idx_project_work_tasks_source ON {TASKS_TABLE}(workspace_project_id, source_module, source_code);

    CREATE TABLE IF NOT EXISTS {COMMENTS_TABLE}(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id INTEGER NOT NULL,
        body TEXT NOT NULL,
        author_email TEXT DEFAULT '',
        author_name TEXT DEFAULT '',
        created_at TEXT DEFAULT '',
        FOREIGN KEY(task_id) REFERENCES {TASKS_TABLE}(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_project_work_task_comments ON {COMMENTS_TABLE}(task_id, id);

    CREATE TABLE IF NOT EXISTS {HISTORY_TABLE}(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id INTEGER NOT NULL,
        action TEXT NOT NULL,
        from_status TEXT DEFAULT '',
        to_status TEXT DEFAULT '',
        progress_before INTEGER DEFAULT 0,
        progress_after INTEGER DEFAULT 0,
        actor_email TEXT DEFAULT '',
        actor_name TEXT DEFAULT '',
        note TEXT DEFAULT '',
        created_at TEXT DEFAULT '',
        FOREIGN KEY(task_id) REFERENCES {TASKS_TABLE}(id) ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_project_work_task_history ON {HISTORY_TABLE}(task_id, id);

    CREATE TABLE IF NOT EXISTS {FILES_TABLE}(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        task_id INTEGER NOT NULL,
        file_id TEXT NOT NULL,
        file_name TEXT DEFAULT '',
        mime_type TEXT DEFAULT '',
        uploaded_by TEXT DEFAULT '',
        created_at TEXT DEFAULT '',
        FOREIGN KEY(task_id) REFERENCES {TASKS_TABLE}(id) ON DELETE CASCADE,
        UNIQUE(task_id, file_id)
    );
    CREATE INDEX IF NOT EXISTS idx_project_work_task_files ON {FILES_TABLE}(task_id, id);
    """)


def ensure_schema(db) -> None:
    with db.connect() as connection:
        ensure_schema_connection(connection)


def _audit_connection(connection, task_id: int, action: str, *, actor: Any, from_status: str = "", to_status: str = "", progress_before: int = 0, progress_after: int = 0, note: str = "") -> None:
    user = _actor(actor)
    connection.execute(
        f"""INSERT INTO {HISTORY_TABLE}(task_id,action,from_status,to_status,progress_before,progress_after,actor_email,actor_name,note,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (int(task_id), _text(action), _text(from_status), _text(to_status), max(0, min(100, _int(progress_before))), max(0, min(100, _int(progress_after))), user["email"], user["name"], _text(note), _now()),
    )


def create_work_task(db, *, master_project_id: int, workspace_project_id: int, title: str, description: str, assignee_email: str, assignee_name: str, priority: str, due_at: str | datetime, actor: Any, source_module: str = "", source_type: str = "", source_id: str = "", source_code: str = "", source_title: str = "") -> dict[str, Any]:
    title = _text(title)
    assignee_email = _email(assignee_email)
    due = _parse_dt(due_at)
    if not title:
        raise ValueError("Tiêu đề công việc không được để trống.")
    if not assignee_email:
        raise ValueError("Phải chọn người thực hiện có email hợp lệ.")
    if due is None:
        raise ValueError("Hạn hoàn thành không hợp lệ.")
    if due <= _now_dt():
        raise ValueError("Hạn hoàn thành phải lớn hơn thời điểm hiện tại.")
    priority = priority if priority in PRIORITIES else "Bình thường"
    user = _actor(actor)
    stamp = _now()
    ensure_schema(db)
    with db.connect() as connection:
        cursor = connection.execute(
            f"""INSERT INTO {TASKS_TABLE}(
                task_code,master_project_id,workspace_project_id,title,description,assignee_email,assignee_name,priority,status,progress_percent,
                source_module,source_type,source_id,source_code,source_title,created_by_email,created_by_name,start_at,due_at,created_at,updated_at,archived
            ) VALUES(NULL,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)""",
            (int(master_project_id), int(workspace_project_id), title, _text(description), assignee_email, _text(assignee_name) or assignee_email, priority, "MỚI", 0,
             _text(source_module), _text(source_type), _text(source_id), _text(source_code), _text(source_title), user["email"], user["name"], stamp, due.strftime("%Y-%m-%d %H:%M:%S"), stamp, stamp),
        )
        task_id = int(cursor.lastrowid or 0)
        if task_id <= 0:
            row = connection.execute(f"SELECT id FROM {TASKS_TABLE} WHERE workspace_project_id=? AND created_by_email=? ORDER BY id DESC LIMIT 1", (int(workspace_project_id), user["email"])).fetchone()
            task_id = int(row[0]) if row else 0
        if task_id <= 0:
            raise RuntimeError("Không xác định được ID công việc vừa tạo.")
        task_code = f"TASK-{task_id:05d}"
        connection.execute(f"UPDATE {TASKS_TABLE} SET task_code=?,updated_at=? WHERE id=?", (task_code, stamp, task_id))
        _audit_connection(connection, task_id, "CREATE", actor=user, to_status="MỚI", note=f"Giao cho {_text(assignee_name) or assignee_email}")
        return _rowdict(connection.execute(f"SELECT * FROM {TASKS_TABLE} WHERE id=?", (task_id,)).fetchone())


def get_work_task(db, task_id: int, *, workspace_project_id: int | None = None) -> dict[str, Any]:
    ensure_schema(db)
    with db.connect() as connection:
        if workspace_project_id:
            row = connection.execute(f"SELECT * FROM {TASKS_TABLE} WHERE id=? AND workspace_project_id=? LIMIT 1", (int(task_id), int(workspace_project_id))).fetchone()
        else:
            row = connection.execute(f"SELECT * FROM {TASKS_TABLE} WHERE id=? LIMIT 1", (int(task_id),)).fetchone()
        return _rowdict(row)


def list_work_tasks(db, workspace_project_id: int, *, include_archived: bool = False) -> list[dict[str, Any]]:
    ensure_schema(db)
    with db.connect() as connection:
        sql = f"SELECT * FROM {TASKS_TABLE} WHERE workspace_project_id=?"
        if not include_archived:
            sql += " AND archived=0"
        sql += " ORDER BY CASE priority WHEN 'Khẩn' THEN 0 WHEN 'Quan trọng' THEN 1 ELSE 2 END, due_at, id DESC"
        return [_rowdict(row) for row in connection.execute(sql, (int(workspace_project_id),)).fetchall()]


def work_task_summary(db, workspace_project_id: int, user_email: str = "") -> dict[str, int]:
    rows = list_work_tasks(db, int(workspace_project_id))
    email = _email(user_email)
    now = _now_dt()
    return {
        "total": len(rows),
        "mine": sum(1 for row in rows if email and _email(row.get("assignee_email")) == email),
        "active": sum(1 for row in rows if _text(row.get("status")).upper() in {"MỚI", "ĐÃ NHẬN", "ĐANG XỬ LÝ", "YÊU CẦU LÀM LẠI"}),
        "waiting_confirmation": sum(1 for row in rows if _text(row.get("status")).upper() == "CHỜ XÁC NHẬN"),
        "overdue": sum(1 for row in rows if is_overdue(row, now=now)),
        "completed": sum(1 for row in rows if _text(row.get("status")).upper() in {"HOÀN THÀNH", "ĐÓNG"}),
    }


def _require_task(connection, task_id: int, workspace_project_id: int) -> dict[str, Any]:
    task = _rowdict(connection.execute(f"SELECT * FROM {TASKS_TABLE} WHERE id=? AND workspace_project_id=? AND archived=0 LIMIT 1", (int(task_id), int(workspace_project_id))).fetchone())
    if not task:
        raise ValueError("Không tìm thấy công việc trong nhà thầu đang làm việc.")
    return task


def _can_assignee_act(task: Any, actor: Any, is_admin: bool = False) -> bool:
    user = _actor(actor)
    return bool(_is_manager(user, is_admin) or (user["email"] and user["email"] == _email(_rowdict(task).get("assignee_email"))))


def transition_work_task(db, task_id: int, workspace_project_id: int, action: str, *, actor: Any, is_admin: bool = False, note: str = "") -> dict[str, Any]:
    action = _text(action).upper()
    user = _actor(actor)
    ensure_schema(db)
    with db.connect() as connection:
        task = _require_task(connection, int(task_id), int(workspace_project_id))
        old_status = _text(task.get("status")).upper() or "MỚI"
        old_progress = max(0, min(100, _int(task.get("progress_percent"))))
        manager = _is_manager(user, is_admin)
        assignee = bool(user["email"] and user["email"] == _email(task.get("assignee_email")))
        if action == "ACCEPT":
            if not (assignee or manager) or old_status != "MỚI":
                raise PermissionError("Chỉ người được giao việc hoặc BĐH mới có thể nhận công việc này.")
            new_status, new_progress = "ĐÃ NHẬN", old_progress
        elif action == "START":
            if not (assignee or manager) or old_status not in {"MỚI", "ĐÃ NHẬN", "YÊU CẦU LÀM LẠI"}:
                raise PermissionError("Không thể chuyển công việc sang Đang xử lý.")
            new_status, new_progress = "ĐANG XỬ LÝ", old_progress
        elif action == "REQUEST_COMPLETION":
            if not (assignee or manager) or old_status in TERMINAL_STATUSES:
                raise PermissionError("Chỉ người thực hiện hoặc BĐH mới có thể báo hoàn thành.")
            new_status, new_progress = "CHỜ XÁC NHẬN", 100
        elif action == "CONFIRM":
            if not manager or old_status != "CHỜ XÁC NHẬN":
                raise PermissionError("Chỉ BĐH/Admin được xác nhận công việc đã hoàn thành.")
            new_status, new_progress = "HOÀN THÀNH", 100
        elif action == "REOPEN":
            if not manager or old_status not in {"CHỜ XÁC NHẬN", "HOÀN THÀNH"}:
                raise PermissionError("Chỉ BĐH/Admin được yêu cầu làm lại.")
            new_status, new_progress = "YÊU CẦU LÀM LẠI", min(99, old_progress)
        elif action == "CLOSE":
            if not manager or old_status != "HOÀN THÀNH":
                raise PermissionError("Chỉ BĐH/Admin được đóng công việc đã xác nhận hoàn thành.")
            new_status, new_progress = "ĐÓNG", 100
        else:
            raise ValueError("Thao tác trạng thái không hợp lệ.")
        stamp = _now()
        requested = _text(task.get("completion_requested_at"))
        completed = _text(task.get("completed_at"))
        closed = _text(task.get("closed_at"))
        if action == "REQUEST_COMPLETION": requested = stamp
        if action == "CONFIRM": completed = stamp
        if action == "CLOSE": closed = stamp
        if action == "REOPEN": requested = completed = closed = ""
        connection.execute(
            f"UPDATE {TASKS_TABLE} SET status=?,progress_percent=?,completion_requested_at=?,completed_at=?,closed_at=?,updated_at=? WHERE id=? AND workspace_project_id=?",
            (new_status, int(new_progress), requested, completed, closed, stamp, int(task_id), int(workspace_project_id)),
        )
        _audit_connection(connection, int(task_id), action, actor=user, from_status=old_status, to_status=new_status, progress_before=old_progress, progress_after=new_progress, note=note)
        return _rowdict(connection.execute(f"SELECT * FROM {TASKS_TABLE} WHERE id=?", (int(task_id),)).fetchone())


def update_work_task_progress(db, task_id: int, workspace_project_id: int, progress_percent: int, *, actor: Any, is_admin: bool = False, note: str = "") -> dict[str, Any]:
    progress = max(0, min(100, _int(progress_percent)))
    user = _actor(actor)
    ensure_schema(db)
    with db.connect() as connection:
        task = _require_task(connection, int(task_id), int(workspace_project_id))
        if not _can_assignee_act(task, user, is_admin):
            raise PermissionError("Chỉ người thực hiện hoặc BĐH/Admin được cập nhật tiến độ công việc.")
        old_status = _text(task.get("status")).upper() or "MỚI"
        if old_status in TERMINAL_STATUSES:
            raise ValueError("Công việc đã hoàn thành/đóng, không thể cập nhật tiến độ.")
        old_progress = max(0, min(100, _int(task.get("progress_percent"))))
        new_status = "ĐANG XỬ LÝ" if old_status in {"MỚI", "ĐÃ NHẬN", "YÊU CẦU LÀM LẠI"} and progress > 0 else old_status
        connection.execute(f"UPDATE {TASKS_TABLE} SET progress_percent=?,status=?,updated_at=? WHERE id=? AND workspace_project_id=?", (progress, new_status, _now(), int(task_id), int(workspace_project_id)))
        _audit_connection(connection, int(task_id), "PROGRESS", actor=user, from_status=old_status, to_status=new_status, progress_before=old_progress, progress_after=progress, note=note)
        return _rowdict(connection.execute(f"SELECT * FROM {TASKS_TABLE} WHERE id=?", (int(task_id),)).fetchone())


def add_work_task_comment(db, task_id: int, workspace_project_id: int, body: str, *, actor: Any) -> dict[str, Any]:
    body = _text(body)
    if not body:
        raise ValueError("Nội dung bình luận đang trống.")
    user = _actor(actor)
    ensure_schema(db)
    with db.connect() as connection:
        task = _require_task(connection, int(task_id), int(workspace_project_id))
        cursor = connection.execute(f"INSERT INTO {COMMENTS_TABLE}(task_id,body,author_email,author_name,created_at) VALUES(?,?,?,?,?)", (int(task_id), body, user["email"], user["name"], _now()))
        comment_id = int(cursor.lastrowid or 0)
        _audit_connection(connection, int(task_id), "COMMENT", actor=user, from_status=_text(task.get("status")), to_status=_text(task.get("status")), progress_before=_int(task.get("progress_percent")), progress_after=_int(task.get("progress_percent")), note=body[:500])
        return _rowdict(connection.execute(f"SELECT * FROM {COMMENTS_TABLE} WHERE id=?", (comment_id,)).fetchone()) if comment_id else {}


def list_work_task_comments(db, task_id: int) -> list[dict[str, Any]]:
    ensure_schema(db)
    with db.connect() as connection:
        return [_rowdict(row) for row in connection.execute(f"SELECT * FROM {COMMENTS_TABLE} WHERE task_id=? ORDER BY id", (int(task_id),)).fetchall()]


def list_work_task_history(db, task_id: int) -> list[dict[str, Any]]:
    ensure_schema(db)
    with db.connect() as connection:
        return [_rowdict(row) for row in connection.execute(f"SELECT * FROM {HISTORY_TABLE} WHERE task_id=? ORDER BY id DESC", (int(task_id),)).fetchall()]


def register_work_task_file(db, task_id: int, workspace_project_id: int, file_info: Any, *, actor: Any) -> None:
    item = _rowdict(file_info)
    file_id = _text(item.get("id"))
    if not file_id:
        return
    user = _actor(actor)
    ensure_schema(db)
    with db.connect() as connection:
        task = _require_task(connection, int(task_id), int(workspace_project_id))
        connection.execute(f"INSERT OR IGNORE INTO {FILES_TABLE}(task_id,file_id,file_name,mime_type,uploaded_by,created_at) VALUES(?,?,?,?,?,?)", (int(task_id), file_id, _text(item.get("name")), _text(item.get("mime_type")), user["email"], _now()))
        _audit_connection(connection, int(task_id), "FILE_UPLOAD", actor=user, from_status=_text(task.get("status")), to_status=_text(task.get("status")), progress_before=_int(task.get("progress_percent")), progress_after=_int(task.get("progress_percent")), note=_text(item.get("name")))


def _safe_query(connection, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
    try:
        return [_rowdict(row) for row in connection.execute(sql, tuple(params)).fetchall()]
    except Exception:
        return []


def source_catalog(db, workspace_project_id: int, source_module: str) -> list[dict[str, str]]:
    module, pid, out = _text(source_module), int(workspace_project_id), []
    with db.connect() as connection:
        if module == "Tiến độ":
            for row in _safe_query(connection, "SELECT id,wbs,name FROM tasks WHERE project_id=? ORDER BY id DESC LIMIT 500", (pid,)):
                out.append({"id": _text(row.get("id")), "code": _text(row.get("wbs")) or f"MPP-{row.get('id')}", "title": _text(row.get("name")), "type": "schedule_task"})
        elif module == "Hồ sơ":
            for row in _safe_query(connection, "SELECT id,doc_type,code,subject FROM documents WHERE project_id=? ORDER BY id DESC LIMIT 500", (pid,)):
                out.append({"id": _text(row.get("id")), "code": _text(row.get("code")), "title": _text(row.get("subject")), "type": _text(row.get("doc_type"))})
        elif module == "Bản vẽ":
            for row in _safe_query(connection, "SELECT id,drawing_type,drawing_no,title,revision FROM drawings WHERE project_id=? ORDER BY id DESC LIMIT 500", (pid,)):
                code = _text(row.get("drawing_no")); rev = _text(row.get("revision")); code = f"{code} / {rev}" if rev else code
                out.append({"id": _text(row.get("id")), "code": code, "title": _text(row.get("title")), "type": _text(row.get("drawing_type"))})
        elif module == "BOQ":
            for row in _safe_query(connection, "SELECT id,task_ref,boq_item FROM cost_budgets WHERE project_id=? ORDER BY id DESC LIMIT 500", (pid,)):
                out.append({"id": _text(row.get("id")), "code": _text(row.get("task_ref")) or f"BOQ-{row.get('id')}", "title": _text(row.get("boq_item")), "type": "boq"})
        elif module == "IPC/Claim":
            rows = _safe_query(connection, "SELECT claim_id,claim_code,filename FROM payment_claims WHERE project_id=? ORDER BY claim_code DESC LIMIT 200", (pid,))
            for row in rows:
                out.append({"id": _text(row.get("claim_id")), "code": _text(row.get("claim_code")), "title": _text(row.get("filename")), "type": "ipc_claim"})
            for row in _safe_query(connection, "SELECT id,payment_code,installment FROM payment_tracking WHERE project_id=? ORDER BY id DESC LIMIT 200", (pid,)):
                code = _text(row.get("payment_code"))
                if code and not any(item["code"] == code for item in out): out.append({"id": _text(row.get("id")), "code": code, "title": _text(row.get("installment")), "type": "payment"})
        elif module == "VO":
            for row in _safe_query(connection, "SELECT vo_id,vo_code,filename FROM variation_orders WHERE project_id=? ORDER BY vo_no DESC LIMIT 200", (pid,)):
                out.append({"id": _text(row.get("vo_id")), "code": _text(row.get("vo_code")), "title": _text(row.get("filename")), "type": "vo_excel"})
            for row in _safe_query(connection, "SELECT id,vo_code,description FROM cost_variations WHERE project_id=? ORDER BY id DESC LIMIT 200", (pid,)):
                code = _text(row.get("vo_code"))
                if code and not any(item["code"] == code for item in out): out.append({"id": _text(row.get("id")), "code": code, "title": _text(row.get("description")), "type": "vo"})
    return out


def _workspace_users(db, master_project_id: int, workspace_project_id: int, users: Iterable[Any], identity: Any) -> list[dict[str, str]]:
    current, out, seen = _actor(identity), [], set()
    try:
        from contractor_access_control_v622 import CONTRACTOR, get_user_project_access
    except Exception:
        CONTRACTOR, get_user_project_access = "CONTRACTOR", None
    for raw in list(users or []):
        row = _rowdict(raw); email = _email(row.get("email"))
        if not email or email in seen or not bool(row.get("active", True)): continue
        approval = _text(row.get("approval_role")).upper()
        if approval == CONTRACTOR and callable(get_user_project_access):
            try:
                if _int(get_user_project_access(db, int(master_project_id), email).get("workspace_project_id")) != int(workspace_project_id): continue
            except Exception:
                continue
        seen.add(email); out.append({"email": email, "name": _text(row.get("name")) or email, "approval_role": approval})
    if current["email"] and current["email"] not in seen:
        out.append({"email": current["email"], "name": current["name"], "approval_role": current["approval_role"]})
    return sorted(out, key=lambda item: (item["name"].lower(), item["email"]))


def _source_navigation(source_module: str) -> tuple[str, str] | None:
    return {"Tiến độ": ("🏗️ Thi công", "📅 Tiến độ"), "Hồ sơ": ("📁 Hồ sơ", "📁 Hồ sơ"), "Bản vẽ": ("📁 Hồ sơ", "📐 Bản vẽ"), "BOQ": ("💰 Tài chính", "💰 Chi phí"), "IPC/Claim": ("💰 Tài chính", "💰 Chi phí"), "VO": ("💰 Tài chính", "💰 Chi phí")}.get(_text(source_module))


def _rerun(st) -> None:
    fn = getattr(st, "rerun", None) or getattr(st, "experimental_rerun", None)
    if callable(fn): fn()


def _render_create_panel(st, db, pid: int, master_pid: int, identity: Any, *, is_admin: bool, users: Iterable[Any]) -> None:
    actor = _actor(identity)
    if not _is_manager(actor, is_admin): return
    user_rows = _workspace_users(db, master_pid, pid, users, identity)
    key = f"work_v1_new_{pid}"
    with st.expander("+ Giao việc", expanded=False):
        title = st.text_input("Tiêu đề *", key=f"{key}_title")
        description = st.text_area("Nội dung yêu cầu", height=110, key=f"{key}_description")
        if user_rows:
            options = [row["email"] for row in user_rows]
            selected_email = st.selectbox("Người thực hiện *", options, format_func=lambda email: next((f"{row['name']} · {row['email']}" for row in user_rows if row["email"] == email), email), key=f"{key}_assignee")
            assignee_name = next(row["name"] for row in user_rows if row["email"] == selected_email)
        else:
            selected_email = st.text_input("Email người thực hiện *", key=f"{key}_assignee_email"); assignee_name = st.text_input("Tên người thực hiện", key=f"{key}_assignee_name")
        c1, c2, c3 = st.columns(3)
        priority = c1.selectbox("Ưu tiên", list(PRIORITIES), key=f"{key}_priority")
        due_date = c2.date_input("Hạn hoàn thành *", value=date.today() + timedelta(days=1), key=f"{key}_due_date")
        due_time = c3.time_input("Giờ", value=time(17, 0), key=f"{key}_due_time")
        source_module = st.selectbox("Liên kết nguồn", ["Không liên kết", "Tiến độ", "Hồ sơ", "Bản vẽ", "BOQ", "IPC/Claim", "VO", "Khác"], key=f"{key}_source_module")
        source_type = source_id = source_code = source_title = ""
        if source_module not in {"Không liên kết", "Khác"}:
            catalog = source_catalog(db, pid, source_module)
            if catalog:
                idx = st.selectbox("Hồ sơ/công việc nguồn", list(range(len(catalog))), format_func=lambda i: f"{catalog[i]['code']} · {catalog[i]['title']}"[:180], key=f"{key}_source_record")
                picked = catalog[int(idx)]; source_type, source_id, source_code, source_title = picked["type"], picked["id"], picked["code"], picked["title"]
            else:
                st.info("Chưa có dữ liệu nguồn trong nhà thầu đang làm việc. Có thể nhập mã nguồn thủ công.")
                source_code = st.text_input("Mã nguồn", key=f"{key}_source_code_empty"); source_title = st.text_input("Tên nguồn", key=f"{key}_source_title_empty")
        elif source_module == "Khác":
            source_code = st.text_input("Mã nguồn", key=f"{key}_source_code_other"); source_title = st.text_input("Tên nguồn", key=f"{key}_source_title_other"); source_type = "other"
        if st.button("Giao việc", type="primary", key=f"{key}_submit", use_container_width=True):
            try:
                created = create_work_task(db, master_project_id=master_pid, workspace_project_id=pid, title=title, description=description, assignee_email=selected_email, assignee_name=assignee_name, priority=priority, due_at=datetime.combine(due_date, due_time), actor=actor, source_module="" if source_module == "Không liên kết" else source_module, source_type=source_type, source_id=source_id, source_code=source_code, source_title=source_title)
                st.success(f"Đã tạo {created.get('task_code')}."); st.session_state[f"work_v1_selected_{pid}"] = int(created.get("id") or 0); _rerun(st)
            except Exception as exc:
                st.error(str(exc))


def _render_task_table(st, rows: list[dict[str, Any]], pid: int) -> dict[str, Any] | None:
    if not rows:
        st.info("Không có công việc phù hợp bộ lọc."); return None
    now = _now_dt()
    st.dataframe([{"Mã": _text(r.get("task_code")), "Công việc": _text(r.get("title")), "Người thực hiện": _text(r.get("assignee_name")) or _text(r.get("assignee_email")), "Nguồn": _text(r.get("source_code")) or "—", "Hạn": _due_text(r.get("due_at")), "Trạng thái": "🔴 QUÁ HẠN" if is_overdue(r, now=now) else _text(r.get("status")), "%": _int(r.get("progress_percent")), "Ưu tiên": _text(r.get("priority"))} for r in rows], use_container_width=True, hide_index=True)
    by_id = {int(r["id"]): r for r in rows}; ids = list(by_id); state_key = f"work_v1_selected_{pid}"; current = _int(st.session_state.get(state_key), ids[0]); current = current if current in by_id else ids[0]
    selected_id = st.selectbox("Mở công việc", ids, index=ids.index(current), format_func=lambda task_id: f"{by_id[task_id].get('task_code')} · {by_id[task_id].get('title')}", key=f"work_v1_open_{pid}")
    st.session_state[state_key] = int(selected_id); return by_id[int(selected_id)]


def _render_actions(st, db, task: dict[str, Any], pid: int, identity: Any, *, is_admin: bool) -> None:
    actor = _actor(identity); manager = _is_manager(actor, is_admin); assignee = bool(actor["email"] and actor["email"] == _email(task.get("assignee_email"))); status = _text(task.get("status")).upper(); task_id = int(task["id"]); buttons = []
    if (assignee or manager) and status == "MỚI": buttons.append(("Đã nhận việc", "ACCEPT"))
    if (assignee or manager) and status in {"MỚI", "ĐÃ NHẬN", "YÊU CẦU LÀM LẠI"}: buttons.append(("Bắt đầu xử lý", "START"))
    if (assignee or manager) and status not in TERMINAL_STATUSES and status != "CHỜ XÁC NHẬN": buttons.append(("Báo hoàn thành", "REQUEST_COMPLETION"))
    if manager and status == "CHỜ XÁC NHẬN": buttons += [("Xác nhận hoàn thành", "CONFIRM"), ("Yêu cầu làm lại", "REOPEN")]
    if manager and status == "HOÀN THÀNH": buttons += [("Đóng công việc", "CLOSE"), ("Mở lại", "REOPEN")]
    if buttons:
        cols = st.columns(min(3, len(buttons)))
        for idx, (label, action) in enumerate(buttons):
            if cols[idx % len(cols)].button(label, key=f"work_v1_action_{task_id}_{action}", use_container_width=True):
                try: transition_work_task(db, task_id, pid, action, actor=actor, is_admin=is_admin); _rerun(st)
                except Exception as exc: st.error(str(exc))


def _render_detail(st, db, task: dict[str, Any], pid: int, master_pid: int, identity: Any, *, can_update: bool, is_admin: bool, gateway: Any = None, session_token: str = "") -> None:
    actor = _actor(identity); manager = _is_manager(actor, is_admin); assignee = bool(actor["email"] and actor["email"] == _email(task.get("assignee_email"))); task_id = int(task["id"])
    st.markdown(f"### {escape(_text(task.get('task_code')))} · {escape(_text(task.get('title')))}")
    c1, c2, c3, c4 = st.columns(4); c1.metric("Trạng thái", effective_status(task)); c2.metric("Tiến độ", f"{_int(task.get('progress_percent'))}%"); c3.metric("Ưu tiên", _text(task.get("priority")) or "Bình thường"); c4.metric("Hạn", _due_text(task.get("due_at")))
    st.markdown(f"**Người thực hiện:** {escape(_text(task.get('assignee_name')) or _text(task.get('assignee_email')))}")
    if _text(task.get("description")): st.write(_text(task.get("description")))
    if _text(task.get("source_module")) or _text(task.get("source_code")):
        sc1, sc2 = st.columns([5, 1]); label = " · ".join(x for x in (_text(task.get("source_module")), _text(task.get("source_code")), _text(task.get("source_title"))) if x); sc1.markdown(f"**Nguồn:** {escape(label)}")
        nav = _source_navigation(_text(task.get("source_module")))
        if nav and sc2.button("Mở nguồn", key=f"work_v1_source_{task_id}", use_container_width=True):
            group, section = nav; st.session_state[f"qlda_v7_group_{master_pid}"] = group; st.session_state[f"qlda_v7_section_{master_pid}_{group}"] = section; _rerun(st)
    if can_update: _render_actions(st, db, task, pid, actor, is_admin=is_admin)
    if can_update and (assignee or manager) and _text(task.get("status")).upper() not in TERMINAL_STATUSES:
        with st.expander("Cập nhật tiến độ", expanded=False):
            progress = st.slider("% hoàn thành", 0, 100, max(0, min(100, _int(task.get("progress_percent")))), 5, key=f"work_v1_progress_{task_id}"); note = st.text_input("Ghi chú cập nhật", key=f"work_v1_progress_note_{task_id}")
            if st.button("Lưu tiến độ", key=f"work_v1_progress_save_{task_id}", use_container_width=True):
                try: update_work_task_progress(db, task_id, pid, progress, actor=actor, is_admin=is_admin, note=note); _rerun(st)
                except Exception as exc: st.error(str(exc))
    st.markdown("#### File")
    if can_update and gateway is not None and session_token:
        uploads = st.file_uploader("Đính kèm file", accept_multiple_files=True, key=f"work_v1_files_{task_id}", label_visibility="collapsed")
        if uploads and st.button("Lưu file", key=f"work_v1_files_save_{task_id}"):
            project = db.project(pid); errors = []
            if not project: st.error("Không tìm thấy workspace dự án.")
            else:
                for uploaded in uploads:
                    try:
                        info = gateway.upload_bytes(session_token, project_code=_text(project["code"]), kind="task", subtype="WORK", record_code=_text(task.get("task_code")), name=_text(uploaded.name), content=uploaded.getvalue(), mime_type=_text(getattr(uploaded, "type", "")), upload_purpose="work_task_attachment")
                        register_work_task_file(db, task_id, pid, info, actor=actor)
                    except Exception as exc: errors.append(f"{uploaded.name}: {exc}")
                if errors: st.error("; ".join(errors))
                else: st.success("Đã lưu file vào kho dự án."); _rerun(st)
    if gateway is not None and session_token:
        try:
            project = db.project(pid); listing = gateway.list_record_files(session_token, project_code=_text(project["code"]) if project else "", kind="task", subtype="WORK", record_code=_text(task.get("task_code")), include_history=False) if project else {"files": []}; file_rows = list(listing.get("files") or [])
        except Exception: file_rows = []
        for item_raw in file_rows:
            item = _rowdict(item_raw); fc1, fc2 = st.columns([5, 1]); fc1.write(f"📎 {_text(item.get('name'))}"); url = _text(item.get("download_url") or item.get("webViewLink") or item.get("url"))
            if url: fc2.link_button("Tải", url, use_container_width=True)
    st.markdown("#### Bình luận")
    for comment in list_work_task_comments(db, task_id)[-20:]:
        st.markdown(f"**{escape(_text(comment.get('author_name')) or _text(comment.get('author_email')))}** · {escape(_text(comment.get('created_at')))}  \n{escape(_text(comment.get('body')))}")
    if can_update:
        with st.form(f"work_v1_comment_form_{task_id}", clear_on_submit=True):
            body = st.text_area("Bình luận mới", height=80, label_visibility="collapsed"); submit = st.form_submit_button("Gửi bình luận")
        if submit:
            try: add_work_task_comment(db, task_id, pid, body, actor=actor); _rerun(st)
            except Exception as exc: st.error(str(exc))
    with st.expander("Lịch sử xử lý", expanded=False):
        history = [{"Thời gian": _text(x.get("created_at")), "Người thực hiện": _text(x.get("actor_name")) or _text(x.get("actor_email")), "Thao tác": _text(x.get("action")), "Từ": _text(x.get("from_status")), "Đến": _text(x.get("to_status")), "%": _int(x.get("progress_after")), "Ghi chú": _text(x.get("note"))} for x in list_work_task_history(db, task_id)]
        if history: st.dataframe(history, use_container_width=True, hide_index=True)
        else: st.info("Chưa có lịch sử.")


def render_work_tasks_v1(st, db, workspace_project_id: int, *, master_project_id: int, identity: Any, can_update: bool, is_admin: bool, users: Iterable[Any] = (), gateway: Any = None, session_token: str = "") -> None:
    ensure_schema(db); actor = _actor(identity); pid = int(workspace_project_id); rows = list_work_tasks(db, pid); summary = work_task_summary(db, pid, actor["email"])
    st.subheader("📋 Công việc")
    c1, c2, c3, c4, c5 = st.columns(5); c1.metric("Việc của tôi", summary["mine"]); c2.metric("Đang xử lý", summary["active"]); c3.metric("Chờ xác nhận", summary["waiting_confirmation"]); c4.metric("Quá hạn", summary["overdue"]); c5.metric("Hoàn thành", summary["completed"])
    _render_create_panel(st, db, pid, int(master_project_id), actor, is_admin=is_admin, users=users)
    mode = st.radio("Phạm vi", ["Tất cả", "Việc của tôi", "Tôi đã giao", "Quá hạn", "Chờ xác nhận", "Hoàn thành"], horizontal=True, key=f"work_v1_scope_{pid}", label_visibility="collapsed")
    f1, f2, f3 = st.columns([2.3, 1, 1]); search = f1.text_input("Tìm công việc", placeholder="Mã, tiêu đề, người thực hiện, nguồn...", key=f"work_v1_search_{pid}"); status_filter = f2.selectbox("Trạng thái", ["Tất cả"] + list(STATUSES), key=f"work_v1_status_{pid}"); priority_filter = f3.selectbox("Ưu tiên", ["Tất cả"] + list(PRIORITIES), key=f"work_v1_priority_{pid}")
    query, now, filtered = _text(search).lower(), _now_dt(), []
    for row in rows:
        status = _text(row.get("status")).upper()
        if mode == "Việc của tôi" and _email(row.get("assignee_email")) != actor["email"]: continue
        if mode == "Tôi đã giao" and _email(row.get("created_by_email")) != actor["email"]: continue
        if mode == "Quá hạn" and not is_overdue(row, now=now): continue
        if mode == "Chờ xác nhận" and status != "CHỜ XÁC NHẬN": continue
        if mode == "Hoàn thành" and status not in {"HOÀN THÀNH", "ĐÓNG"}: continue
        if status_filter != "Tất cả" and status != status_filter: continue
        if priority_filter != "Tất cả" and _text(row.get("priority")) != priority_filter: continue
        if query and query not in " ".join(_text(row.get(k)).lower() for k in ("task_code", "title", "description", "assignee_name", "assignee_email", "source_module", "source_code", "source_title")): continue
        filtered.append(row)
    selected = _render_task_table(st, filtered, pid)
    if selected:
        st.divider(); current = get_work_task(db, int(selected["id"]), workspace_project_id=pid) or selected
        _render_detail(st, db, current, pid, int(master_project_id), actor, can_update=bool(can_update), is_admin=bool(is_admin), gateway=gateway, session_token=session_token)


def install_work_tasks_v1() -> None:
    import cloud_db
    base = getattr(cloud_db, "SQLiteCloudDatabase", None) or cloud_db.CloudDatabase
    if not getattr(base, "_qlda_work_tasks_v1_installed", False):
        original_create_tables, original_migrate = base.create_tables, base.migrate
        def create_tables_with_work_tasks(self):
            original_create_tables(self)
            try: ensure_schema(self)
            except Exception: pass
        def migrate_with_work_tasks(self):
            original_migrate(self); ensure_schema(self)
        base.create_tables = create_tables_with_work_tasks; base.migrate = migrate_with_work_tasks; base._qlda_work_tasks_v1_installed = True
    try:
        import postgres_backend_v622 as pg
        order = list(pg.TABLE_ORDER); additions = [TASKS_TABLE, COMMENTS_TABLE, HISTORY_TABLE, FILES_TABLE]; insert_at = order.index("tasks") + 1 if "tasks" in order else (order.index("projects") + 1 if "projects" in order else len(order))
        for table in reversed(additions):
            if table not in order: order.insert(insert_at, table)
        pg.TABLE_ORDER = tuple(order); pg._ID_TABLES = set(pg.TABLE_ORDER)
    except Exception: pass
    active = cloud_db.CloudDatabase; active._qlda_work_tasks_v1_installed = True; active._qlda_work_tasks_v1_marker = PATCH_MARKER
