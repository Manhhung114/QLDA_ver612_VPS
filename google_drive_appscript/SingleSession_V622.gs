/**
 * QLDA V6.22 - SINGLE ACTIVE SESSION PATCH
 * -----------------------------------------
 * Add this file to the SAME Apps Script project as Code.gs, then Deploy a new
 * Web App version. It intentionally wraps the existing V6.x functions so the
 * large upload gateway does not need to be duplicated.
 *
 * Rule: one account = one active session. A successful login replaces the
 * previous login. Refresh/multiple tabs are unaffected because they reuse the
 * same signed token. Old upload tickets/sessions are invalidated after a newer
 * login as well.
 */

var V622_SINGLE_SESSION_PATCH = 'V6.22 SINGLE ACTIVE SESSION V1';
var _V622_SS_ORIGINAL_DOPOST = doPost;
var _V622_SS_ORIGINAL_SET_USER = setUser_;
var _V622_SS_ORIGINAL_CHANGE_PASSWORD = changePassword_;
var _V622_SS_ORIGINAL_VALIDATE_UPLOAD_TICKET = validateUploadTicketMeta_;
var _V622_SS_ORIGINAL_READ_UPLOAD_STATE = readUploadState_;

function v622SessionPropertyKey_(sid) {
  return 'qlda_active_session_' + String(sid || '');
}

function v622ReadSessionMeta_(sid) {
  const key = v622SessionPropertyKey_(sid);
  const raw = PropertiesService.getScriptProperties().getProperty(key);
  if (!raw) return null;
  try {
    const meta = JSON.parse(raw);
    if (Number(meta.expires_at || 0) <= Date.now()) {
      PropertiesService.getScriptProperties().deleteProperty(key);
      return null;
    }
    return meta;
  } catch (ignore) {
    PropertiesService.getScriptProperties().deleteProperty(key);
    return null;
  }
}

function v622WriteSessionMeta_(sid, meta) {
  PropertiesService.getScriptProperties().setProperty(
    v622SessionPropertyKey_(sid), JSON.stringify(meta || {})
  );
}

function v622DeleteSessionMeta_(sid) {
  if (!sid) return;
  try { PropertiesService.getScriptProperties().deleteProperty(v622SessionPropertyKey_(sid)); } catch (ignore) {}
}

function v622InvalidateUserSession_(email, actor, reason) {
  const normalized = normalizeEmail_(email);
  const lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try {
    const users = readUsers_();
    const idx = users.findIndex(x => x.email === normalized);
    if (idx < 0) return {email: normalized, revoked_sessions: 0};
    const oldSid = String(users[idx].active_session_id || '');
    if (oldSid) v622DeleteSessionMeta_(oldSid);
    users[idx].active_session_id = '';
    users[idx].active_session_started_at = '';
    users[idx].active_session_expires_at = '';
    users[idx].updated_at = new Date().toISOString();
    writeUsers_(users);
    audit_('SESSION_REVOKE', actor || normalized, normalized, reason || 'force logout');
    return {email: normalized, revoked_sessions: oldSid ? 1 : 0};
  } finally {
    lock.releaseLock();
  }
}

// Replace the historical login with an atomic login + active_session_id write.
login_ = function(body) {
  const email = normalizeEmail_(body.email);
  const password = String(body.password || '');
  const clientInfo = String(body.client_info || '').substring(0, 500);
  const lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try {
    const users = readUsers_();
    const idx = users.findIndex(x => x.email === email && x.active !== false);
    if (idx < 0 || passwordHash_(users[idx].salt, password) !== users[idx].password_hash) {
      throw new Error('Email hoặc mật khẩu không đúng.');
    }
    const u = users[idx];
    const oldSid = String(u.active_session_id || '');
    if (oldSid) v622DeleteSessionMeta_(oldSid);

    const sid = randomSecret_();
    const started = Date.now();
    const expires = started + SESSION_TTL_SECONDS * 1000;
    u.active_session_id = sid;
    u.active_session_started_at = new Date(started).toISOString();
    u.active_session_expires_at = new Date(expires).toISOString();
    u.updated_at = new Date().toISOString();
    writeUsers_(users);

    v622WriteSessionMeta_(sid, {
      sid: sid,
      email: email,
      name: String(u.name || ''),
      role: String(u.role || ''),
      approval_role: normalizeApprovalRole_(u.approval_role || u.approval_group || ''),
      created_at: u.active_session_started_at,
      last_seen_at: u.active_session_started_at,
      expires_at: expires,
      client_info: clientInfo
    });

    try { applyDriveRole_(email, u.role); } catch (ignore) {}
    const token = makeSessionToken_(u);
    audit_('LOGIN_SINGLE_SESSION', email, email, String(u.role || ''));
    return {session_token: token, user: publicUser_(u)};
  } finally {
    lock.releaseLock();
  }
};

makeSessionToken_ = function(u) {
  const sid = String(u.active_session_id || '');
  if (!sid) throw new Error('Không tạo được phiên đăng nhập đơn. Hãy đăng nhập lại.');
  let exp = Math.floor(Date.now() / 1000) + SESSION_TTL_SECONDS;
  const storedExp = Date.parse(String(u.active_session_expires_at || ''));
  if (Number.isFinite(storedExp) && storedExp > Date.now()) exp = Math.floor(storedExp / 1000);
  const payload = {email: u.email, role: u.role, sid: sid, exp: exp, nonce: randomSecret_()};
  const body = Utilities.base64EncodeWebSafe(JSON.stringify(payload), Utilities.Charset.UTF_8).replace(/=+$/g, '');
  const sig = Utilities.base64EncodeWebSafe(Utilities.computeHmacSha256Signature(body, API_TOKEN)).replace(/=+$/g, '');
  return body + '.' + sig;
};

requireSession_ = function(body) {
  const token = String(body.session_token || '');
  const session = verifySessionToken_(token);
  const u = currentUserRecord_(session.email);
  const activeSid = String(u.active_session_id || '');
  if (!session.sid || !activeSid || String(session.sid) !== activeSid) {
    throw new Error('Tài khoản đã đăng nhập trên thiết bị khác hoặc phiên đã bị kết thúc. Hãy đăng nhập lại.');
  }
  const expAt = Date.parse(String(u.active_session_expires_at || ''));
  if (Number.isFinite(expAt) && expAt <= Date.now()) {
    v622InvalidateUserSession_(u.email, u.email, 'expired');
    throw new Error('Phiên đăng nhập đã hết hạn. Hãy đăng nhập lại.');
  }

  // Touch at most once per minute to avoid excessive ScriptProperties writes.
  const meta = v622ReadSessionMeta_(activeSid) || {
    sid: activeSid,
    email: u.email,
    name: String(u.name || ''),
    role: String(u.role || ''),
    created_at: String(u.active_session_started_at || new Date().toISOString()),
    expires_at: Number.isFinite(expAt) ? expAt : (Date.now() + SESSION_TTL_SECONDS * 1000),
    client_info: ''
  };
  const lastSeenMs = Date.parse(String(meta.last_seen_at || meta.created_at || ''));
  if (!Number.isFinite(lastSeenMs) || Date.now() - lastSeenMs >= 60000) {
    meta.last_seen_at = new Date().toISOString();
    v622WriteSessionMeta_(activeSid, meta);
  }

  session.role = u.role;
  session.name = u.name || '';
  session.approval_role = normalizeApprovalRole_(u.approval_role || u.approval_group || '');
  if (String(u.role || '').toLowerCase() === 'admin' && !session.approval_role) {
    session.approval_role = 'PROJECT_MANAGEMENT';
  }
  return session;
};

function logoutV622_(body) {
  const token = String(body.session_token || '');
  let payload;
  try { payload = verifySessionToken_(token); } catch (ignore) { return {logged_out: false}; }
  const email = normalizeEmail_(payload.email);
  const lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try {
    const users = readUsers_();
    const idx = users.findIndex(x => x.email === email);
    if (idx < 0) return {logged_out: false};
    if (String(users[idx].active_session_id || '') !== String(payload.sid || '')) {
      return {logged_out: false};
    }
    v622DeleteSessionMeta_(users[idx].active_session_id);
    users[idx].active_session_id = '';
    users[idx].active_session_started_at = '';
    users[idx].active_session_expires_at = '';
    users[idx].updated_at = new Date().toISOString();
    writeUsers_(users);
    audit_('LOGOUT', email, email, 'single session');
    return {logged_out: true};
  } finally {
    lock.releaseLock();
  }
}

function listSessionsV622_(body) {
  const actor = requireRole_(body, ['admin']);
  const now = Date.now();
  const sessions = [];
  readUsers_().forEach(function(u) {
    if (u.active === false) return;
    const sid = String(u.active_session_id || '');
    const exp = Date.parse(String(u.active_session_expires_at || ''));
    if (!sid || !Number.isFinite(exp) || exp <= now) return;
    const meta = v622ReadSessionMeta_(sid) || {};
    sessions.push({
      email: String(u.email || ''),
      name: String(u.name || ''),
      role: String(u.role || ''),
      approval_role: normalizeApprovalRole_(u.approval_role || u.approval_group || ''),
      created_at: String(meta.created_at || u.active_session_started_at || ''),
      last_seen_at: String(meta.last_seen_at || u.active_session_started_at || ''),
      expires_at: String(u.active_session_expires_at || ''),
      client_info: String(meta.client_info || ''),
      current: String(u.email || '') === String(actor.email || '')
    });
  });
  sessions.sort((a, b) => String(b.last_seen_at || '').localeCompare(String(a.last_seen_at || '')));
  return {sessions: sessions, requested_by: actor.email};
}

function forceLogoutV622_(body) {
  const actor = requireRole_(body, ['admin']);
  const target = normalizeEmail_(body.email);
  return v622InvalidateUserSession_(target, actor.email, 'admin force logout');
}

// Password changes invalidate all existing tokens for that account.
setUser_ = function(body) {
  const result = _V622_SS_ORIGINAL_SET_USER(body);
  if (String(body.password || '')) {
    try { v622InvalidateUserSession_(normalizeEmail_(body.email), 'admin', 'password reset'); } catch (ignore) {}
  }
  return result;
};

changePassword_ = function(body) {
  const session = requireSession_(body);
  const result = _V622_SS_ORIGINAL_CHANGE_PASSWORD(body);
  v622InvalidateUserSession_(session.email, session.email, 'password changed');
  return result;
};

// A ticket created before the current login must not survive a replacement login.
validateUploadTicketMeta_ = function(meta) {
  _V622_SS_ORIGINAL_VALIDATE_UPLOAD_TICKET(meta);
  const email = normalizeEmail_(meta.email || '');
  const u = currentUserRecord_(email);
  const activeStarted = Date.parse(String(u.active_session_started_at || ''));
  const ticketStarted = Date.parse(String(meta.created_at || ''));
  if (!u.active_session_id || !Number.isFinite(activeStarted) || !Number.isFinite(ticketStarted) || ticketStarted + 1000 < activeStarted) {
    throw new Error('Phiên đăng nhập tạo link upload đã bị thay thế. Hãy đăng nhập lại và tạo link mới.');
  }
  return true;
};

// A resumable upload already started on an old login is also stopped.
readUploadState_ = function(uploadId) {
  const state = _V622_SS_ORIGINAL_READ_UPLOAD_STATE(uploadId);
  const email = normalizeEmail_(state.email || '');
  const u = currentUserRecord_(email);
  const activeStarted = Date.parse(String(u.active_session_started_at || ''));
  const uploadStarted = Date.parse(String(state.created_at || ''));
  if (!u.active_session_id || !Number.isFinite(activeStarted) || !Number.isFinite(uploadStarted) || uploadStarted + 1000 < activeStarted) {
    throw new Error('Phiên upload thuộc đăng nhập cũ đã bị kết thúc. Hãy đăng nhập lại.');
  }
  return state;
};

// Add session-management actions without rewriting the legacy doPost switch.
doPost = function(e) {
  let body = {};
  try {
    body = JSON.parse((e && e.postData && e.postData.contents) || '{}');
    requireApiToken_(body);
    const action = String(body.action || '').trim();
    let result = null;
    if (action === 'logout') result = logoutV622_(body);
    else if (action === 'list_sessions') result = listSessionsV622_(body);
    else if (action === 'force_logout') result = forceLogoutV622_(body);
    if (result !== null) {
      result = result || {};
      result.ok = true;
      return jsonResponse_(result);
    }
  } catch (err) {
    return jsonResponse_({ok: false, error: String(err && err.message ? err.message : err)});
  }
  return _V622_SS_ORIGINAL_DOPOST(e);
};
