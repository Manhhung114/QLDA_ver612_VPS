from __future__ import annotations

"""Email notification for newly assigned QLDA work tasks.

The task remains the source of truth: creating a task must never be rolled back
because an SMTP/provider notification is temporarily unavailable.  Delivery
status is written to the task history and surfaced on the next Streamlit rerun.

Remote/legacy storage can keep using the existing DriveGateway mail action.
Local VPS storage sends through SMTP configured only on the server.
"""

import os
import smtplib
import ssl
from contextvars import ContextVar
from email.message import EmailMessage
from email.utils import formataddr
from html import escape
from typing import Any, Iterable


PATCH_MARKER = "V7.6 WORK TASK ASSIGNEE EMAIL V1"

_CTX_GATEWAY: ContextVar[Any | None] = ContextVar("qlda_work_mail_gateway", default=None)
_CTX_SESSION_TOKEN: ContextVar[str] = ContextVar("qlda_work_mail_session", default="")
_CTX_ST: ContextVar[Any | None] = ContextVar("qlda_work_mail_streamlit", default=None)
_CTX_WORKSPACE_ID: ContextVar[int] = ContextVar("qlda_work_mail_workspace", default=0)


def _env(*names: str, default: str = "") -> str:
    for name in names:
        value = str(os.environ.get(name, "") or "").strip()
        if value:
            return value
    return default


def _bool_env(*names: str, default: bool = False) -> bool:
    value = _env(*names)
    if not value:
        return bool(default)
    return value.lower() in {"1", "true", "yes", "on"}


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


def _text(value: Any) -> str:
    return str(value or "").strip()


def _safe_error(exc: Exception) -> str:
    text = str(exc or "").strip().replace("\n", " ")
    # Never echo credentials if a provider happens to include them in an error.
    password = _env("QLDA_SMTP_PASSWORD", "SMTP_PASSWORD", "MAIL_PASSWORD")
    user = _env("QLDA_SMTP_USER", "SMTP_USER", "MAIL_USERNAME")
    for secret in (password, user):
        if secret:
            text = text.replace(secret, "***")
    return text[:350] or exc.__class__.__name__


def _smtp_settings() -> dict[str, Any]:
    user = _env("QLDA_SMTP_USER", "SMTP_USER", "MAIL_USERNAME")
    password = _env("QLDA_SMTP_PASSWORD", "SMTP_PASSWORD", "MAIL_PASSWORD")
    host = _env("QLDA_SMTP_HOST", "SMTP_HOST", "MAIL_HOST")
    if not host and user.lower().endswith("@gmail.com"):
        host = "smtp.gmail.com"

    security = _env("QLDA_SMTP_SECURITY", "SMTP_SECURITY", default="starttls").lower()
    if security in {"tls", "start_tls", "start-tls"}:
        security = "starttls"
    if security not in {"starttls", "ssl", "none"}:
        security = "starttls"

    try:
        default_port = 465 if security == "ssl" else 587
        port = int(_env("QLDA_SMTP_PORT", "SMTP_PORT", "MAIL_PORT", default=str(default_port)))
    except Exception:
        port = 465 if security == "ssl" else 587

    from_email = _env("QLDA_SMTP_FROM_EMAIL", "QLDA_SMTP_FROM", "MAIL_FROM", default=user)
    from_name = _env("QLDA_SMTP_FROM_NAME", "MAIL_FROM_NAME", default="QLDA XÂY DỰNG")
    try:
        timeout = max(5, min(30, int(_env("QLDA_SMTP_TIMEOUT", default="12"))))
    except Exception:
        timeout = 12

    return {
        "host": host,
        "port": port,
        "security": security,
        "user": user,
        "password": password,
        "from_email": from_email,
        "from_name": from_name,
        "timeout": timeout,
    }


def _send_smtp(*, to_email: str, subject: str, body: str, app_url: str = "") -> dict[str, Any]:
    cfg = _smtp_settings()
    if not cfg["host"] or not cfg["from_email"]:
        return {
            "ok": True,
            "sent": False,
            "channel": "smtp",
            "reason": "SMTP chưa được cấu hình trên VPS.",
        }

    recipient = _text(to_email).lower()
    if "@" not in recipient:
        return {"ok": True, "sent": False, "channel": "smtp", "reason": "Email người nhận không hợp lệ."}

    message = EmailMessage()
    message["Subject"] = _text(subject) or "QLDA - Thông báo công việc"
    message["From"] = formataddr((_text(cfg["from_name"]), _text(cfg["from_email"])))
    message["To"] = recipient
    message.set_content(body)

    html_body = escape(body).replace("\n", "<br>")
    if app_url:
        safe_url = escape(app_url, quote=True)
        html_body += f'<br><br><a href="{safe_url}" style="display:inline-block;padding:10px 16px;background:#1d4ed8;color:#fff;text-decoration:none;border-radius:8px">Mở QLDA XÂY DỰNG</a>'
    message.add_alternative(
        "<html><body style='font-family:Arial,sans-serif;line-height:1.55;color:#14213d'>"
        f"{html_body}</body></html>",
        subtype="html",
    )

    try:
        if cfg["security"] == "ssl":
            smtp: smtplib.SMTP = smtplib.SMTP_SSL(
                cfg["host"], cfg["port"], timeout=cfg["timeout"], context=ssl.create_default_context()
            )
        else:
            smtp = smtplib.SMTP(cfg["host"], cfg["port"], timeout=cfg["timeout"])
        with smtp:
            smtp.ehlo()
            if cfg["security"] == "starttls":
                smtp.starttls(context=ssl.create_default_context())
                smtp.ehlo()
            if cfg["user"]:
                smtp.login(cfg["user"], cfg["password"])
            smtp.send_message(message)
        return {"ok": True, "sent": True, "channel": "smtp", "to": recipient}
    except Exception as exc:
        return {
            "ok": False,
            "sent": False,
            "channel": "smtp",
            "reason": _safe_error(exc),
        }


def _project_label(db, project_id: int) -> str:
    try:
        row = _rowdict(db.project(int(project_id)))
    except Exception:
        row = {}
    code = _text(row.get("code"))
    name = _text(row.get("name"))
    if code and name:
        return f"{code} - {name}"
    return code or name or f"Project #{int(project_id)}"


def _public_app_url(gateway: Any = None) -> str:
    value = _env("QLDA_PUBLIC_BASE_URL", "QLDA_APP_URL", "APP_PUBLIC_URL")
    if value:
        return value.rstrip("/")
    try:
        value = _text(getattr(getattr(gateway, "config", None), "public_base_url", ""))
    except Exception:
        value = ""
    return value.rstrip("/")


def _mail_payload(db, task: dict[str, Any], actor: Any, gateway: Any = None) -> tuple[str, str, str]:
    actor_row = _rowdict(actor)
    assigned_by = _text(actor_row.get("name") or actor_row.get("email") or task.get("created_by_name") or task.get("created_by_email"))
    assignee_name = _text(task.get("assignee_name") or task.get("assignee_email"))
    priority = _text(task.get("priority")) or "Bình thường"
    task_code = _text(task.get("task_code")) or f"TASK-{task.get('id', '')}"
    title = _text(task.get("title"))
    description = _text(task.get("description")) or "(Không có nội dung bổ sung)"
    due = _text(task.get("due_at"))
    try:
        from qlda.runtime_core import work_tasks_v1 as wt
        due = wt._due_text(due) or due
    except Exception:
        pass

    master_id = int(task.get("master_project_id") or 0)
    workspace_id = int(task.get("workspace_project_id") or master_id or 0)
    project = _project_label(db, master_id or workspace_id)
    workspace = _project_label(db, workspace_id)
    source_code = _text(task.get("source_code"))
    source_title = _text(task.get("source_title"))
    source = ""
    if source_code or source_title:
        source = " - ".join(x for x in (source_code, source_title) if x)

    urgent = "[KHẨN] " if priority == "Khẩn" else ""
    subject = f"[QLDA] {urgent}{task_code} - {title}"
    lines = [
        f"Xin chào {assignee_name},",
        "",
        "Bạn vừa được giao một công việc mới trên QLDA XÂY DỰNG.",
        "",
        f"Mã công việc: {task_code}",
        f"Dự án: {project}",
    ]
    if workspace and workspace != project:
        lines.append(f"Workspace/Nhà thầu: {workspace}")
    lines += [
        f"Tiêu đề: {title}",
        f"Nội dung yêu cầu: {description}",
        f"Người giao việc: {assigned_by}",
        f"Ưu tiên: {priority}",
        f"Hạn hoàn thành: {due}",
    ]
    if source:
        lines.append(f"Liên kết nguồn: {source}")
    app_url = _public_app_url(gateway)
    if app_url:
        lines += ["", f"Mở QLDA để nhận và xử lý công việc: {app_url}"]
    lines += ["", "Email này được gửi tự động từ QLDA XÂY DỰNG."]
    return subject, "\n".join(lines), app_url


def _notify_assignment(db, task: dict[str, Any], actor: Any) -> dict[str, Any]:
    gateway = _CTX_GATEWAY.get()
    session_token = _CTX_SESSION_TOKEN.get()
    to_email = _text(task.get("assignee_email")).lower()
    subject, body, app_url = _mail_payload(db, task, actor, gateway)

    try:
        is_local = bool(getattr(getattr(gateway, "config", None), "local", False)) if gateway is not None else True
        if gateway is not None and session_token and not is_local:
            result = dict(
                gateway.send_approval_email(
                    session_token,
                    to_email=to_email,
                    subject=subject,
                    body=body,
                    app_url=app_url,
                )
                or {}
            )
            result.setdefault("channel", "gateway")
            return result
        return _send_smtp(to_email=to_email, subject=subject, body=body, app_url=app_url)
    except Exception as exc:
        return {"ok": False, "sent": False, "channel": "mail", "reason": _safe_error(exc)}


def _audit_delivery(db, task: dict[str, Any], actor: Any, result: dict[str, Any]) -> None:
    try:
        from qlda.runtime_core import work_tasks_v1 as wt

        sent = bool(result.get("sent"))
        note = (
            f"Đã gửi email giao việc đến {_text(task.get('assignee_email'))}."
            if sent
            else "Email giao việc chưa gửi: " + _text(result.get("reason") or "dịch vụ email chưa sẵn sàng")
        )
        with db.connect() as connection:
            wt._audit_connection(
                connection,
                int(task.get("id") or 0),
                "EMAIL_SENT" if sent else "EMAIL_FAILED",
                actor=actor,
                from_status=_text(task.get("status")),
                to_status=_text(task.get("status")),
                progress_before=int(task.get("progress_percent") or 0),
                progress_after=int(task.get("progress_percent") or 0),
                note=note,
            )
    except Exception:
        pass


def _queue_ui_notice(task: dict[str, Any], result: dict[str, Any]) -> None:
    st = _CTX_ST.get()
    workspace_id = int(task.get("workspace_project_id") or _CTX_WORKSPACE_ID.get() or 0)
    if st is None or workspace_id <= 0:
        return
    sent = bool(result.get("sent"))
    task_code = _text(task.get("task_code"))
    recipient = _text(task.get("assignee_email"))
    if sent:
        message = f"{task_code}: đã gửi email giao việc đến {recipient}."
    else:
        message = f"{task_code}: công việc đã được tạo nhưng email chưa gửi — {_text(result.get('reason') or 'dịch vụ email chưa sẵn sàng')}."
    st.session_state[f"work_task_mail_notice_{workspace_id}"] = {"sent": sent, "message": message}


def _patch_local_gateway_mail() -> None:
    """Make the existing local DriveGateway mail action useful for other modules too."""
    try:
        from qlda.runtime_core import local_vps_backend as local
    except Exception:
        return
    if getattr(local, "_qlda_smtp_mail_installed", False):
        return

    def _local_send_approval_email(token: str, *, to_email: str = "", subject: str = "", body: str = "", app_url: str = "", **_: Any) -> dict[str, Any]:
        local._require_session(token)
        return _send_smtp(to_email=to_email, subject=subject, body=body, app_url=app_url)

    local.send_approval_email = _local_send_approval_email
    local._qlda_smtp_mail_installed = True
    local._qlda_smtp_mail_marker = PATCH_MARKER


def install_work_task_email_notification() -> None:
    """Send one assignment email after a new Công việc record is committed."""
    from qlda.runtime_core import work_tasks_v1 as wt

    if getattr(wt, "_qlda_work_task_email_installed", False):
        return

    _patch_local_gateway_mail()
    original_create = wt.create_work_task
    original_render = wt.render_work_tasks_v1

    def _create_with_email(db, *args, **kwargs):
        task = dict(original_create(db, *args, **kwargs) or {})
        actor = kwargs.get("actor")
        result = _notify_assignment(db, task, actor)
        _audit_delivery(db, task, actor, result)
        _queue_ui_notice(task, result)
        task["_email_notification"] = result
        return task

    def _render_with_email(
        st,
        db,
        workspace_project_id: int,
        *,
        master_project_id: int,
        identity: Any,
        can_update: bool,
        is_admin: bool,
        users: Iterable[Any] = (),
        gateway: Any = None,
        session_token: str = "",
    ) -> None:
        gateway_token = _CTX_GATEWAY.set(gateway)
        session_ctx_token = _CTX_SESSION_TOKEN.set(_text(session_token))
        st_token = _CTX_ST.set(st)
        workspace_token = _CTX_WORKSPACE_ID.set(int(workspace_project_id))
        try:
            notice = st.session_state.pop(f"work_task_mail_notice_{int(workspace_project_id)}", None)
            if isinstance(notice, dict) and notice.get("message"):
                if notice.get("sent"):
                    st.success(str(notice["message"]))
                else:
                    st.warning(str(notice["message"]))
            return original_render(
                st,
                db,
                int(workspace_project_id),
                master_project_id=int(master_project_id),
                identity=identity,
                can_update=bool(can_update),
                is_admin=bool(is_admin),
                users=users,
                gateway=gateway,
                session_token=session_token,
            )
        finally:
            _CTX_GATEWAY.reset(gateway_token)
            _CTX_SESSION_TOKEN.reset(session_ctx_token)
            _CTX_ST.reset(st_token)
            _CTX_WORKSPACE_ID.reset(workspace_token)

    wt.create_work_task = _create_with_email
    wt.render_work_tasks_v1 = _render_with_email
    wt._qlda_work_task_email_installed = True
    wt._qlda_work_task_email_marker = PATCH_MARKER


__all__ = ["PATCH_MARKER", "install_work_task_email_notification"]
