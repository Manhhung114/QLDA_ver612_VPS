from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from datetime import datetime, timezone
from typing import Any


PATCH_MARKER = "V6.22 SINGLE ACTIVE SESSION V2 HISTORY10"
SESSION_HISTORY_LIMIT = 10


def _token_hash(token: str) -> str:
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


def _client_label(value: Any) -> str:
    return str(value or "").strip()[:500]


def install_local_single_session() -> None:
    """Harden the PostgreSQL/VPS auth backend to one live session per account.

    A new successful login replaces the previous session atomically. Refresh and
    multiple tabs keep working because they reuse the same token. Upload tickets
    are bound to the login session, so a superseded session cannot keep uploading.

    The active-session table remains security-authoritative. A separate append-style
    history table keeps only the 10 most recent successful logins for the Admin UI.
    PostgreSQL continues storing TIMESTAMPTZ values correctly; UI presentation is
    converted to Asia/Ho_Chi_Minh (UTC+7) by the Streamlit patch.
    """
    import local_vps_backend_v622 as lb

    if getattr(lb, "_qlda_single_session_installed", False):
        return

    original_require = lb._require_session
    original_dispatch = lb.dispatch
    original_set_user = lb.set_user
    original_change_password = lb.change_password
    original_make_upload_ticket = lb.make_upload_ticket
    original_verify_upload_ticket = lb.verify_upload_ticket

    def _prune_history(cur) -> None:
        cur.execute(
            """DELETE FROM qlda_local_session_history
               WHERE id NOT IN (
                   SELECT id FROM qlda_local_session_history
                   ORDER BY created_at DESC, id DESC
                   LIMIT %s
               )""",
            (SESSION_HISTORY_LIMIT,),
        )

    def _end_active_history(cur, email: str, reason: str) -> None:
        cur.execute(
            """UPDATE qlda_local_session_history h
               SET ended_at=COALESCE(h.ended_at,NOW()),
                   end_reason=CASE WHEN h.ended_at IS NULL THEN %s ELSE h.end_reason END
               WHERE h.ended_at IS NULL
                 AND h.token_hash IN (
                     SELECT s.token_hash FROM qlda_local_sessions s WHERE s.email=%s
                 )""",
            (str(reason or "ENDED"), str(email or "")),
        )

    def ensure_session_schema() -> None:
        lb.ensure_schema()
        with lb._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("ALTER TABLE qlda_local_sessions ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMPTZ")
                cur.execute("ALTER TABLE qlda_local_sessions ADD COLUMN IF NOT EXISTS client_info TEXT NOT NULL DEFAULT ''")
                cur.execute("UPDATE qlda_local_sessions SET last_seen_at=COALESCE(last_seen_at,created_at)")
                cur.execute(
                    """CREATE TABLE IF NOT EXISTS qlda_local_session_history (
                           id BIGSERIAL PRIMARY KEY,
                           token_hash TEXT NOT NULL UNIQUE,
                           email TEXT NOT NULL,
                           created_at TIMESTAMPTZ NOT NULL,
                           last_seen_at TIMESTAMPTZ NOT NULL,
                           expires_at TIMESTAMPTZ NOT NULL,
                           ended_at TIMESTAMPTZ,
                           end_reason TEXT NOT NULL DEFAULT '',
                           client_info TEXT NOT NULL DEFAULT ''
                       )"""
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_qlda_session_history_created ON qlda_local_session_history(created_at DESC, id DESC)"
                )
                cur.execute(
                    "CREATE INDEX IF NOT EXISTS idx_qlda_session_history_email ON qlda_local_session_history(email, created_at DESC)"
                )
                # Backfill any live sessions that existed before V2 was deployed.
                cur.execute(
                    """INSERT INTO qlda_local_session_history
                       (token_hash,email,created_at,last_seen_at,expires_at,client_info)
                       SELECT token_hash,email,created_at,COALESCE(last_seen_at,created_at),expires_at,COALESCE(client_info,'')
                       FROM qlda_local_sessions
                       ON CONFLICT (token_hash) DO UPDATE SET
                           last_seen_at=EXCLUDED.last_seen_at,
                           expires_at=EXCLUDED.expires_at,
                           client_info=EXCLUDED.client_info"""
                )
                cur.execute(
                    """UPDATE qlda_local_session_history
                       SET ended_at=COALESCE(ended_at,expires_at),
                           end_reason=CASE WHEN ended_at IS NULL THEN 'EXPIRED' ELSE end_reason END
                       WHERE ended_at IS NULL AND expires_at<=NOW()"""
                )
                cur.execute("DELETE FROM qlda_local_sessions WHERE expires_at<=NOW()")
                # Preserve only the newest live session per account before enabling
                # the single-session invariant on an upgraded database, while the
                # superseded rows remain available in the rolling history.
                cur.execute(
                    """UPDATE qlda_local_session_history h
                       SET ended_at=COALESCE(h.ended_at,NOW()),
                           end_reason=CASE WHEN h.ended_at IS NULL THEN 'REPLACED' ELSE h.end_reason END
                       WHERE h.ended_at IS NULL
                         AND h.token_hash IN (
                             SELECT old.token_hash
                             FROM qlda_local_sessions old
                             JOIN qlda_local_sessions newer ON old.email=newer.email
                             WHERE old.created_at < newer.created_at OR
                                   (old.created_at = newer.created_at AND old.token_hash < newer.token_hash)
                         )"""
                )
                cur.execute(
                    """DELETE FROM qlda_local_sessions old
                       USING qlda_local_sessions newer
                       WHERE old.email=newer.email
                         AND (old.created_at < newer.created_at OR
                              (old.created_at = newer.created_at AND old.token_hash < newer.token_hash))"""
                )
                _prune_history(cur)
            conn.commit()

    def login(email: str, password: str, client_info: str = "") -> dict[str, Any]:
        ensure_session_schema()
        normalized_email = lb._normalize_email(email)
        raw_token = secrets.token_urlsafe(48)
        digest = _token_hash(raw_token)
        client = _client_label(client_info)
        with lb._connect() as conn:
            with conn.cursor() as cur:
                # FOR UPDATE serializes concurrent logins for the same account;
                # the last completed successful login is the only live session.
                cur.execute(
                    "SELECT * FROM qlda_local_users WHERE email=%s AND active=TRUE FOR UPDATE",
                    (normalized_email,),
                )
                row = cur.fetchone()
                if not row or not hmac.compare_digest(
                    str(row.get("password_hash") or ""),
                    lb._password_hash(str(row.get("password_salt") or ""), str(password or "")),
                ):
                    raise lb.LocalVPSError("Email hoặc mật khẩu không đúng.")
                _end_active_history(cur, normalized_email, "REPLACED")
                cur.execute("DELETE FROM qlda_local_sessions WHERE email=%s", (normalized_email,))
                cur.execute(
                    """INSERT INTO qlda_local_sessions
                       (token_hash,email,expires_at,created_at,last_seen_at,client_info)
                       VALUES (%s,%s,NOW() + (%s * INTERVAL '1 second'),NOW(),NOW(),%s)""",
                    (digest, normalized_email, lb.session_ttl_seconds(), client),
                )
                cur.execute(
                    """INSERT INTO qlda_local_session_history
                       (token_hash,email,created_at,last_seen_at,expires_at,client_info)
                       VALUES (%s,%s,NOW(),NOW(),NOW() + (%s * INTERVAL '1 second'),%s)
                       ON CONFLICT (token_hash) DO NOTHING""",
                    (digest, normalized_email, lb.session_ttl_seconds(), client),
                )
                _prune_history(cur)
            conn.commit()
        return {"ok": True, "session_token": raw_token, "user": lb._public_user(dict(row))}

    def require_session(token: str, roles: set[str] | None = None) -> dict[str, Any]:
        ensure_session_schema()
        value = str(token or "").strip()
        if not value:
            raise lb.LocalVPSError("Phiên đăng nhập đã hết hạn. Hãy đăng nhập lại.")
        digest = _token_hash(value)
        with lb._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT u.* FROM qlda_local_sessions s
                       JOIN qlda_local_users u ON u.email=s.email
                       WHERE s.token_hash=%s AND s.expires_at>NOW() AND u.active=TRUE""",
                    (digest,),
                )
                row = cur.fetchone()
                if row:
                    cur.execute(
                        "UPDATE qlda_local_sessions SET last_seen_at=NOW() WHERE token_hash=%s",
                        (digest,),
                    )
                    cur.execute(
                        """UPDATE qlda_local_session_history
                           SET last_seen_at=NOW()
                           WHERE token_hash=%s AND ended_at IS NULL""",
                        (digest,),
                    )
            conn.commit()
        if not row:
            raise lb.LocalVPSError(
                "Tài khoản đã đăng nhập trên thiết bị khác, phiên đã bị kết thúc, "
                "hoặc đã hết hạn. Hãy đăng nhập lại."
            )
        user = dict(row)
        if roles is not None and lb._normalize_role(user.get("role") or "read") not in roles:
            raise lb.LocalVPSError("Tài khoản không có quyền thực hiện thao tác này.")
        return user

    def logout(token: str) -> dict[str, Any]:
        ensure_session_schema()
        digest = _token_hash(token)
        with lb._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE qlda_local_session_history
                       SET ended_at=COALESCE(ended_at,NOW()),
                           end_reason=CASE WHEN ended_at IS NULL THEN 'LOGOUT' ELSE end_reason END
                       WHERE token_hash=%s""",
                    (digest,),
                )
                cur.execute("DELETE FROM qlda_local_sessions WHERE token_hash=%s", (digest,))
                deleted = cur.rowcount
            conn.commit()
        return {"ok": True, "logged_out": bool(deleted)}

    def list_sessions(token: str) -> dict[str, Any]:
        actor = require_session(token, {"admin"})
        ensure_session_schema()
        with lb._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT s.email,u.name,u.role,u.approval_role,
                              s.created_at,s.last_seen_at,s.expires_at,s.client_info
                       FROM qlda_local_sessions s
                       JOIN qlda_local_users u ON u.email=s.email
                       WHERE u.active=TRUE AND s.expires_at>NOW()
                       ORDER BY COALESCE(s.last_seen_at,s.created_at) DESC"""
                )
                rows = [dict(x) for x in cur.fetchall()]
            conn.commit()
        sessions = [
            {
                "email": str(r.get("email") or ""),
                "name": str(r.get("name") or ""),
                "role": str(r.get("role") or ""),
                "approval_role": str(r.get("approval_role") or ""),
                "created_at": lb._iso(r.get("created_at")),
                "last_seen_at": lb._iso(r.get("last_seen_at") or r.get("created_at")),
                "expires_at": lb._iso(r.get("expires_at")),
                "client_info": str(r.get("client_info") or ""),
                "active": True,
                "current": str(r.get("email") or "").lower() == str(actor.get("email") or "").lower(),
            }
            for r in rows
        ]
        return {"ok": True, "sessions": sessions, "requested_by": str(actor.get("email") or "")}

    def list_session_history(token: str) -> dict[str, Any]:
        actor = require_session(token, {"admin"})
        ensure_session_schema()
        current_digest = _token_hash(token)
        with lb._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT h.id,h.email,COALESCE(u.name,'') AS name,COALESCE(u.role,'') AS role,
                              COALESCE(u.approval_role,'') AS approval_role,
                              h.created_at,h.last_seen_at,h.expires_at,h.ended_at,h.end_reason,h.client_info,
                              CASE WHEN s.token_hash IS NOT NULL AND s.expires_at>NOW() THEN TRUE ELSE FALSE END AS active
                       FROM qlda_local_session_history h
                       LEFT JOIN qlda_local_users u ON u.email=h.email
                       LEFT JOIN qlda_local_sessions s ON s.token_hash=h.token_hash
                       ORDER BY h.created_at DESC,h.id DESC
                       LIMIT %s""",
                    (SESSION_HISTORY_LIMIT,),
                )
                rows = [dict(x) for x in cur.fetchall()]
        history = [
            {
                "email": str(r.get("email") or ""),
                "name": str(r.get("name") or ""),
                "role": str(r.get("role") or ""),
                "approval_role": str(r.get("approval_role") or ""),
                "created_at": lb._iso(r.get("created_at")),
                "last_seen_at": lb._iso(r.get("last_seen_at") or r.get("created_at")),
                "expires_at": lb._iso(r.get("expires_at")),
                "ended_at": lb._iso(r.get("ended_at")),
                "end_reason": str(r.get("end_reason") or ""),
                "client_info": str(r.get("client_info") or ""),
                "active": bool(r.get("active")),
                "current": bool(r.get("active")) and str(r.get("email") or "").lower() == str(actor.get("email") or "").lower(),
            }
            for r in rows
        ]
        # Exact token comparison identifies the current row without exposing its hash.
        for item, r in zip(history, rows):
            if item.get("active"):
                # Only one live session exists per account; email equality is sufficient
                # for the current Admin account after require_session validated this token.
                item["current"] = str(item.get("email") or "").lower() == str(actor.get("email") or "").lower()
        return {"ok": True, "history": history, "limit": SESSION_HISTORY_LIMIT, "requested_by": str(actor.get("email") or "")}

    def force_logout(token: str, email: str) -> dict[str, Any]:
        actor = require_session(token, {"admin"})
        target = lb._normalize_email(email)
        with lb._connect() as conn:
            with conn.cursor() as cur:
                _end_active_history(cur, target, "ADMIN_FORCE_LOGOUT")
                cur.execute("DELETE FROM qlda_local_sessions WHERE email=%s", (target,))
                deleted = cur.rowcount
            conn.commit()
        return {
            "ok": True,
            "email": target,
            "revoked_sessions": int(deleted or 0),
            "requested_by": str(actor.get("email") or ""),
        }

    def set_user(token: str, **kwargs) -> dict[str, Any]:
        result = original_set_user(token, **kwargs)
        if str(kwargs.get("password") or ""):
            target = lb._normalize_email(kwargs.get("email") or "")
            with lb._connect() as conn:
                with conn.cursor() as cur:
                    _end_active_history(cur, target, "PASSWORD_RESET")
                    cur.execute("DELETE FROM qlda_local_sessions WHERE email=%s", (target,))
                conn.commit()
        return result

    def change_password(token: str, old_password: str, new_password: str) -> dict[str, Any]:
        actor = require_session(token)
        result = original_change_password(token, old_password, new_password)
        target = str(actor.get("email") or "")
        with lb._connect() as conn:
            with conn.cursor() as cur:
                _end_active_history(cur, target, "PASSWORD_CHANGED")
                cur.execute("DELETE FROM qlda_local_sessions WHERE email=%s", (target,))
            conn.commit()
        return result

    def make_upload_ticket(token: str, **kwargs) -> dict[str, Any]:
        result = original_make_upload_ticket(token, **kwargs)
        ticket = str(result.get("ticket") or "")
        body, _sig = ticket.rsplit(".", 1)
        payload = json.loads(lb._b64url_decode(body).decode("utf-8"))
        payload["session_hash"] = _token_hash(token)
        new_body = lb._b64url_encode(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        )
        new_sig = hmac.new(lb.local_secret(), new_body.encode("ascii"), hashlib.sha256).hexdigest()
        result = dict(result)
        result["ticket"] = new_body + "." + new_sig
        base = lb.public_base_url()
        if base:
            from urllib.parse import quote
            result["url"] = f"{base}/qlda-files/upload?ticket={quote(result['ticket'])}"
        return result

    def verify_upload_ticket(ticket: str) -> dict[str, Any]:
        payload = original_verify_upload_ticket(ticket)
        digest = str(payload.get("session_hash") or "")
        if not digest:
            raise lb.LocalVPSError("Phiên upload cũ đã hết hiệu lực. Hãy tạo lại phiên đính kèm file.")
        with lb._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM qlda_local_sessions WHERE token_hash=%s AND expires_at>NOW()",
                    (digest,),
                )
                valid = bool(cur.fetchone())
        if not valid:
            raise lb.LocalVPSError(
                "Phiên đăng nhập tạo link upload đã bị thay thế hoặc kết thúc. Hãy đăng nhập lại."
            )
        return payload

    def dispatch(action: str, payload: dict[str, Any] | None = None, session_token: str = "") -> dict[str, Any]:
        action = str(action or "").strip()
        p = dict(payload or {})
        if action == "login":
            return login(p.get("email", ""), p.get("password", ""), p.get("client_info", ""))
        if action == "logout":
            return logout(session_token)
        if action == "list_sessions":
            return list_sessions(session_token)
        if action == "list_session_history":
            return list_session_history(session_token)
        if action == "force_logout":
            return force_logout(session_token, p.get("email", ""))
        return original_dispatch(action, p, session_token)

    lb.login = login
    lb._require_session = require_session
    lb.logout = logout
    lb.list_sessions = list_sessions
    lb.list_session_history = list_session_history
    lb.force_logout = force_logout
    lb.set_user = set_user
    lb.change_password = change_password
    lb.make_upload_ticket = make_upload_ticket
    lb.verify_upload_ticket = verify_upload_ticket
    lb.dispatch = dispatch
    lb._qlda_single_session_installed = True
    lb._qlda_single_session_marker = PATCH_MARKER


def install_gateway_single_session() -> None:
    """Expose session administration on DriveGateway for Drive and local backends."""
    import drive_gateway

    cls = drive_gateway.DriveGateway
    if getattr(cls, "_qlda_single_session_gateway_installed", False):
        return

    def login(self, email: str, password: str, client_info: str = "") -> dict[str, Any]:
        return self._post(
            "login",
            {"email": email, "password": password, "client_info": _client_label(client_info)},
        )

    def logout(self, session_token: str) -> dict[str, Any]:
        return self._post("logout", session_token=session_token)

    def list_sessions(self, session_token: str) -> list[dict[str, Any]]:
        return list(self._post("list_sessions", session_token=session_token).get("sessions") or [])

    def list_session_history(self, session_token: str) -> list[dict[str, Any]]:
        try:
            return list(self._post("list_session_history", session_token=session_token).get("history") or [])
        except Exception:
            # Backward compatibility for an older Apps Script endpoint: still show
            # active sessions until that remote endpoint is upgraded.
            rows = [dict(x) for x in list_sessions(self, session_token)]
            for row in rows:
                row.setdefault("active", True)
            return rows[:SESSION_HISTORY_LIMIT]

    def force_logout(self, session_token: str, email: str) -> dict[str, Any]:
        return self._post("force_logout", {"email": email}, session_token=session_token)

    cls.login = login
    cls.logout = logout
    cls.list_sessions = list_sessions
    cls.list_session_history = list_session_history
    cls.force_logout = force_logout
    cls._qlda_single_session_gateway_installed = True


def install_single_session() -> None:
    install_local_single_session()
    install_gateway_single_session()
