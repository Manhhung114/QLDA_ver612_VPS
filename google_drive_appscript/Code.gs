/**
 * QLDA Xây dựng V6.0 - Google Drive Direct Upload Gateway
 * ------------------------------------------------------------
 * Mục tiêu V6.0:
 * - Không cần Google Cloud Console / OAuth Client / Service Account.
 * - Streamlit chỉ quản lý nghiệp vụ + quyền.
 * - File đính kèm KHÔNG đi qua Streamlit/SQLite.
 * - Trang uploader Apps Script đọc file theo chunk thích nghi, mặc định 2 MiB; Apps Script giữ OAuth
 *   server-side và chuyển từng chunk vào Drive API resumable upload.
 * - Giới hạn ứng dụng: 2 GB / file.
 *
 * Cài đặt:
 * 1) Đổi API_TOKEN và BOOTSTRAP_CODE thành chuỗi bí mật riêng.
 * 2) Deploy > New deployment > Web app.
 * 3) Execute as: Me. Who has access: Anyone.
 * 4) Copy URL /exec vào Streamlit Secrets.
 */

const ROOT_FOLDER_NAME = 'QLDA Xây dựng';
const PRIVATE_FOLDER_NAME = 'QLDA_XayDung_SYSTEM_PRIVATE';
const USERS_FILE_NAME = 'users.json';
const API_TOKEN = 'CHANGE_ME_API_TOKEN_QLDA_6_0';
const BOOTSTRAP_CODE = 'CHANGE_ME_BOOTSTRAP_CODE_QLDA_6_0';
const SESSION_TTL_SECONDS = 12 * 60 * 60;
const UPLOAD_TICKET_TTL_SECONDS = 6 * 60 * 60;
const MAX_DIRECT_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024; // 2 GiB / file
const LEGACY_MAX_UPLOAD_BYTES = 30 * 1024 * 1024;
const RECOMMENDED_CHUNK_BYTES = 2 * 1024 * 1024; // 2 MiB: safer through google.script.run; multiple of 256 KiB

function doGet(e) {
  const p = (e && e.parameter) || {};
  if (String(p.mode || '') === 'upload' && String(p.ticket || '')) {
    return renderUploaderPage_(String(p.ticket));
  }
  return jsonResponse_({
    ok: true,
    service: 'QLDA Drive Gateway',
    version: '6.11',
    direct_upload: true,
    max_file_bytes: MAX_DIRECT_UPLOAD_BYTES,
    message: 'Use POST JSON requests or open an upload ticket URL.'
  });
}

function doPost(e) {
  try {
    const body = JSON.parse((e && e.postData && e.postData.contents) || '{}');
    requireApiToken_(body);
    const action = String(body.action || '').trim();
    let result;
    switch (action) {
      case 'health': result = health_(); break;
      case 'bootstrap': result = bootstrap_(body); break;
      case 'login': result = login_(body); break;
      case 'me': result = me_(body); break;
      case 'root_info': result = rootInfo_(body); break;
      case 'list_users': result = listUsers_(body); break;
      case 'approval_users': result = approvalUsers_(body); break;
      case 'set_user': result = setUser_(body); break;
      case 'delete_user': result = deleteUser_(body); break;
      case 'change_password': result = changePassword_(body); break;
      case 'send_approval_email': result = sendApprovalEmail_(body); break;
      case 'create_upload_ticket': result = createUploadTicket_(body); break;
      case 'list_record_files': result = listRecordFiles_(body); break;
      case 'record_file_counts': result = recordFileCounts_(body); break;
      case 'file_info': result = fileInfo_(body); break;
      case 'upload':
      case 'upload_legacy': result = uploadLegacy_(body); break;
      case 'download':
      case 'download_legacy': result = downloadLegacy_(body); break;
      case 'trash_file': result = trashFile_(body); break;
      default: throw new Error('Action không được hỗ trợ: ' + action);
    }
    result = result || {};
    result.ok = true;
    return jsonResponse_(result);
  } catch (err) {
    return jsonResponse_({ok: false, error: String(err && err.message ? err.message : err)});
  }
}

function requireApiToken_(body) {
  if (!API_TOKEN || API_TOKEN.indexOf('CHANGE_ME_') === 0) {
    throw new Error('Chưa đổi API_TOKEN trong Code.gs.');
  }
  if (String(body.api_token || '') !== API_TOKEN) {
    throw new Error('API token không hợp lệ.');
  }
}

function authorizeV60_() {
  // Chạy thủ công 1 lần trong Apps Script editor sau khi thay Code.gs V6.0.
  // Mục đích: Google hiện hộp thoại cấp quyền Drive + external_request cho UrlFetchApp.
  const root = ensureRootFolder_();
  const response = UrlFetchApp.fetch('https://www.googleapis.com/drive/v3/about?fields=user,storageQuota', {
    method: 'get',
    headers: {Authorization: 'Bearer ' + ScriptApp.getOAuthToken()},
    muteHttpExceptions: true
  });
  if (response.getResponseCode() < 200 || response.getResponseCode() >= 300) {
    throw new Error('Authorization test HTTP ' + response.getResponseCode() + ': ' + response.getContentText().substring(0, 300));
  }
  Logger.log('QLDA V6.0 authorized. Root=' + root.getUrl());
  return true;
}

function authorizeV50_() { return authorizeV60_(); } // alias tương thích khi nâng từ V5

function health_() {
  const users = readUsers_();
  const root = ensureRootFolder_();
  return {
    initialized: users.length > 0,
    user_count: users.length,
    root: {id: root.getId(), name: root.getName(), url: root.getUrl()},
    version: '6.11',
    approval_role_schema: 'approval_role+approval_group-compatible',
    direct_upload: true,
    max_file_bytes: MAX_DIRECT_UPLOAD_BYTES,
    max_file_gb: 2
  };
}

function bootstrap_(body) {
  const lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try {
    const users = readUsers_();
    if (users.length > 0) throw new Error('Hệ thống đã có tài khoản Admin; bootstrap đã bị khóa.');
    if (!BOOTSTRAP_CODE || BOOTSTRAP_CODE.indexOf('CHANGE_ME_') === 0) {
      throw new Error('Chưa đổi BOOTSTRAP_CODE trong Code.gs.');
    }
    if (String(body.bootstrap_code || '') !== BOOTSTRAP_CODE) throw new Error('Mã khởi tạo Admin không đúng.');
    const email = normalizeEmail_(body.email);
    const name = String(body.name || '').trim();
    validatePassword_(body.password);
    const u = newUser_(email, name, 'admin', String(body.password), 'PROJECT_MANAGEMENT');
    writeUsers_([u]);
    applyDriveRole_(email, 'admin');
    audit_('BOOTSTRAP_ADMIN', email, email, 'admin');
    return {user: publicUser_(u)};
  } finally {
    lock.releaseLock();
  }
}

function login_(body) {
  const email = normalizeEmail_(body.email);
  const password = String(body.password || '');
  const approvalRole = normalizeApprovalRole_(body.approval_role || body.approval_group);
  const users = readUsers_();
  const u = users.find(x => x.email === email && x.active !== false);
  if (!u || passwordHash_(u.salt, password) !== u.password_hash) {
    throw new Error('Email hoặc mật khẩu không đúng.');
  }
  // Đồng bộ quyền Drive mỗi lần đăng nhập. Read/Update chỉ là Viewer để
  // không thể xóa trực tiếp trên Google Drive; quyền Update vẫn upload
  // qua Apps Script Gateway chạy dưới quyền chủ sở hữu.
  try { applyDriveRole_(email, u.role); } catch (ignore) {}
  const token = makeSessionToken_(u);
  audit_('LOGIN', email, email, u.role);
  return {session_token: token, user: publicUser_(u)};
}

function me_(body) {
  const session = requireSession_(body);
  const u = currentUserRecord_(session.email);
  return {user: publicUser_(u)};
}

function rootInfo_(body) {
  requireSession_(body);
  const root = ensureRootFolder_();
  return {root: {id: root.getId(), name: root.getName(), url: root.getUrl()}};
}

function listUsers_(body) {
  const session = requireRole_(body, ['admin']);
  return {users: readUsers_().map(publicUser_), requested_by: session.email};
}

function approvalUsers_(body) {
  // V6.7: mọi user đã đăng nhập được đọc danh bạ phê duyệt tối thiểu
  // (chỉ publicUser, không có salt/password_hash) để tự định tuyến workflow.
  const session = requireSession_(body);
  const users = readUsers_()
    .filter(function(u){ return u.active !== false; })
    .map(publicUser_)
    .filter(function(u){ return normalizeApprovalRole_(u.approval_role || u.approval_group || '') !== ''; });
  return {users: users, requested_by: session.email};
}

function setUser_(body) {
  const session = requireRole_(body, ['admin']);
  const email = normalizeEmail_(body.email);
  const role = normalizeRole_(body.role);
  const name = String(body.name || '').trim();
  const password = String(body.password || '');
  let approvalRole = normalizeApprovalRole_(body.approval_role || body.approval_group);
  if (role === 'admin' && !approvalRole) approvalRole = 'PROJECT_MANAGEMENT';

  const lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try {
    const users = readUsers_();
    let idx = users.findIndex(x => x.email === email);
    if (idx < 0) {
      validatePassword_(password);
      users.push(newUser_(email, name, role, password, approvalRole));
      idx = users.length - 1;
    } else {
      users[idx].name = name || users[idx].name || '';
      users[idx].role = role;
      users[idx].approval_role = approvalRole;
      users[idx].approval_group = legacyApprovalGroup_(approvalRole); // alias tương thích V6.0/V6.1
      users[idx].active = true;
      users[idx].updated_at = new Date().toISOString();
      if (password) {
        validatePassword_(password);
        users[idx].salt = randomSecret_();
        users[idx].password_hash = passwordHash_(users[idx].salt, password);
      }
    }
    writeUsers_(users);
    applyDriveRole_(email, role);
    audit_('SET_USER', session.email, email, role);
    return {user: publicUser_(users[idx])};
  } finally {
    lock.releaseLock();
  }
}

function deleteUser_(body) {
  const session = requireRole_(body, ['admin']);
  const email = normalizeEmail_(body.email);
  if (email === session.email) throw new Error('Không thể tự xóa tài khoản Admin đang đăng nhập.');
  const lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try {
    let users = readUsers_();
    const target = users.find(x => x.email === email);
    if (!target) throw new Error('Không tìm thấy người dùng.');
    const remainingAdmins = users.filter(x => x.email !== email && x.active !== false && x.role === 'admin').length;
    if (target.role === 'admin' && remainingAdmins < 1) throw new Error('Phải còn ít nhất một Admin.');
    users = users.filter(x => x.email !== email);
    writeUsers_(users);
    revokeDriveRole_(email);
    audit_('DELETE_USER', session.email, email, target.role);
    return {deleted: email};
  } finally {
    lock.releaseLock();
  }
}

function changePassword_(body) {
  const session = requireSession_(body);
  const oldPassword = String(body.old_password || '');
  const newPassword = String(body.new_password || '');
  validatePassword_(newPassword);
  const lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try {
    const users = readUsers_();
    const idx = users.findIndex(x => x.email === session.email);
    if (idx < 0) throw new Error('Không tìm thấy tài khoản.');
    if (passwordHash_(users[idx].salt, oldPassword) !== users[idx].password_hash) {
      throw new Error('Mật khẩu hiện tại không đúng.');
    }
    users[idx].salt = randomSecret_();
    users[idx].password_hash = passwordHash_(users[idx].salt, newPassword);
    users[idx].updated_at = new Date().toISOString();
    writeUsers_(users);
    audit_('CHANGE_PASSWORD', session.email, session.email, users[idx].role);
    return {changed: true};
  } finally {
    lock.releaseLock();
  }
}

// -----------------------------------------------------------------------------
// V6.0 DIRECT / RESUMABLE UPLOAD
// -----------------------------------------------------------------------------

function createUploadTicket_(body) {
  const session = requireUploadRole_(body);
  const target = ensureRecordFolderFromBody_(body);
  const requestedMax = Number(body.max_bytes || MAX_DIRECT_UPLOAD_BYTES);
  const maxBytes = Math.min(MAX_DIRECT_UPLOAD_BYTES, Math.max(1, requestedMax));
  const now = Date.now();
  const meta = {
    v: 2,
    email: session.email,
    role: session.role,
    approval_role: String(session.approval_role || ''),
    upload_purpose: String(body.upload_purpose || ''),
    folder_id: target.getId(),
    folder_url: target.getUrl(),
    project_code: sanitizeName_(body.project_code || 'DU_AN'),
    kind: String(body.kind || ''),
    subtype: sanitizeName_(body.subtype || 'Khac'),
    record_code: sanitizeName_(body.record_code || 'Chung'),
    tower_name: towerFolderNameFromRecordCode_(body.record_code || ''),
    max_bytes: maxBytes,
    created_at: new Date(now).toISOString(),
    expires_at: now + UPLOAD_TICKET_TTL_SECONDS * 1000,
    nonce: randomSecret_()
  };

  // V6 ticket v2 là ticket ký số, tự chứa metadata và thời hạn.
  // Không phụ thuộc CacheService/PropertiesService nên không còn tình trạng
  // vừa tạo ticket nhưng GET uploader ở invocation khác lại không tìm thấy ticket.
  const ticket = makeSignedUploadTicket_(meta);

  // Luôn ưu tiên đúng URL /exec mà Python/Streamlit Community Cloud đang cấu hình.
  // Điều này tránh ScriptApp.getService().getUrl() trỏ sang deployment khác khi
  // một project Apps Script có nhiều deployment/version.
  const configuredBase = String(body.webapp_url || '').trim();
  const base = validWebAppExecUrl_(configuredBase) ? configuredBase : String(ScriptApp.getService().getUrl() || '').trim();
  if (!base) throw new Error('Không xác định được URL Web App /exec.');

  return {
    upload: {
      ticket: ticket,
      url: base + (base.indexOf('?') >= 0 ? '&' : '?') + 'mode=upload&ticket=' + encodeURIComponent(ticket),
      folder_url: target.getUrl(),
      max_bytes: maxBytes,
      max_gb: Math.round(maxBytes / 1024 / 1024 / 1024 * 10) / 10,
      expires_seconds: UPLOAD_TICKET_TTL_SECONDS,
      ticket_version: 2
    }
  };
}

function readUploadTicket_(ticket) {
  const value = String(ticket || '').trim();
  if (!value) throw new Error('Phiên tải file không hợp lệ.');

  // Ticket V2 stateless: <base64url-json>.<base64url-hmac>
  if (value.indexOf('.') > 0) {
    const parts = value.split('.');
    if (parts.length !== 2 || !parts[0] || !parts[1]) throw new Error('Phiên tải file không hợp lệ.');
    const expected = uploadTicketSignature_(parts[0]);
    if (String(parts[1]) !== String(expected)) throw new Error('Phiên tải file không hợp lệ hoặc chữ ký không đúng.');
    let meta;
    try {
      meta = JSON.parse(base64UrlDecodeUtf8_(parts[0]));
    } catch (err) {
      throw new Error('Phiên tải file không hợp lệ.');
    }
    validateUploadTicketMeta_(meta);
    return meta;
  }

  // Tương thích ticket V6 cũ đang còn trong ScriptProperties trong lúc nâng cấp.
  const props = PropertiesService.getScriptProperties();
  const key = uploadTicketKey_(value);
  const raw = props.getProperty(key);
  if (!raw) throw new Error('Phiên tải file đã hết hạn. Quay lại QLDA và tạo phiên tải mới.');
  let legacyMeta;
  try {
    legacyMeta = JSON.parse(raw);
  } catch (err) {
    props.deleteProperty(key);
    throw new Error('Phiên tải file không hợp lệ.');
  }
  validateUploadTicketMeta_(legacyMeta);
  return legacyMeta;
}

function validateUploadTicketMeta_(meta) {
  if (!meta || !meta.folder_id) throw new Error('Phiên tải file không hợp lệ.');
  const exp = Number(meta.expires_at || 0);
  if (!exp || exp < Date.now()) throw new Error('Phiên tải file đã hết hạn. Quay lại QLDA và tạo phiên tải mới.');
  if (!['update', 'admin'].includes(String(meta.role || ''))) throw new Error('Phiên tải file không có quyền cập nhật.');
  const maxBytes = Number(meta.max_bytes || 0);
  if (!maxBytes || maxBytes < 1 || maxBytes > MAX_DIRECT_UPLOAD_BYTES) throw new Error('Giới hạn tải file trong phiên không hợp lệ.');
}

function makeSignedUploadTicket_(meta) {
  if (!API_TOKEN || API_TOKEN.indexOf('CHANGE_ME_') === 0) throw new Error('Chưa đổi API_TOKEN trong Code.gs.');
  const payload = base64UrlEncodeUtf8_(JSON.stringify(meta));
  return payload + '.' + uploadTicketSignature_(payload);
}

function uploadTicketSignature_(payload) {
  const bytes = Utilities.computeHmacSha256Signature(String(payload), String(API_TOKEN), Utilities.Charset.UTF_8);
  return Utilities.base64EncodeWebSafe(bytes).replace(/=+$/g, '');
}

function base64UrlEncodeUtf8_(text) {
  return Utilities.base64EncodeWebSafe(String(text), Utilities.Charset.UTF_8).replace(/=+$/g, '');
}

function base64UrlDecodeUtf8_(text) {
  let value = String(text || '').replace(/-/g, '-').replace(/_/g, '_');
  while (value.length % 4) value += '=';
  return Utilities.newBlob(Utilities.base64DecodeWebSafe(value)).getDataAsString('UTF-8');
}

function validWebAppExecUrl_(url) {
  return /^https:\/\/script\.google\.com\/macros\/s\/[^/?#]+\/exec(?:[?#].*)?$/.test(String(url || '').trim());
}

function uploadTicketKey_(ticket) {
  return 'upload_ticket_' + String(ticket || '');
}

/**
 * Called from the Apps Script HTML uploader via google.script.run.
 * OAuth token NEVER leaves Apps Script. We create a Drive resumable session and
 * keep its session URL in Script Cache. The browser receives only a random QLDA
 * upload_id, then sends adaptive chunks (2 MiB down to 256 KiB when needed) to uploadChunk().
 */
function startResumableUpload(ticket, fileName, mimeType, fileSize) {
  const meta = readUploadTicket_(String(ticket || ''));
  const name = sanitizeFileName_(fileName || 'file.bin');
  const type = String(mimeType || 'application/octet-stream');
  const size = Number(fileSize || 0);
  if (!size || size < 0) throw new Error('Dung lượng file không hợp lệ.');
  if (size > Math.min(MAX_DIRECT_UPLOAD_BYTES, Number(meta.max_bytes || MAX_DIRECT_UPLOAD_BYTES))) {
    throw new Error('File vượt giới hạn 2 GB của QLDA V6.0.');
  }

  const endpoint = 'https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable&supportsAllDrives=true&fields=id,name,mimeType,size,webViewLink,parents,modifiedTime';
  const payload = JSON.stringify({name: name, parents: [meta.folder_id]});
  const response = UrlFetchApp.fetch(endpoint, {
    method: 'post',
    contentType: 'application/json; charset=UTF-8',
    payload: payload,
    headers: {
      Authorization: 'Bearer ' + ScriptApp.getOAuthToken(),
      'X-Upload-Content-Type': type,
      'X-Upload-Content-Length': String(size)
    },
    muteHttpExceptions: true
  });
  const code = response.getResponseCode();
  if (code < 200 || code >= 300) {
    throw new Error('Không khởi tạo được phiên upload Google Drive. HTTP ' + code + ': ' + response.getContentText().substring(0, 300));
  }
  const headers = response.getHeaders();
  const sessionUrl = headers.Location || headers.location;
  if (!sessionUrl) throw new Error('Google Drive không trả về resumable session URL.');

  const uploadId = randomSecret_();
  const state = {
    session_url: String(sessionUrl),
    folder_id: meta.folder_id,
    folder_url: meta.folder_url,
    project_code: meta.project_code,
    record_code: meta.record_code,
    email: meta.email,
    file_name: name,
    mime_type: type,
    total_size: size,
    next_offset: 0,
    created_at: new Date().toISOString()
  };
  saveUploadState_(uploadId, state);
  return {
    upload_id: uploadId,
    chunk_bytes: RECOMMENDED_CHUNK_BYTES,
    folder_url: meta.folder_url,
    file_name: name,
    file_size: size,
    next_offset: 0
  };
}

function uploadStateKey_(uploadId) {
  return 'upload_session_' + String(uploadId || '');
}

function saveUploadState_(uploadId, state) {
  state.expires_at = Date.now() + UPLOAD_TICKET_TTL_SECONDS * 1000;
  PropertiesService.getScriptProperties().setProperty(uploadStateKey_(uploadId), JSON.stringify(state));
}

function readUploadState_(uploadId) {
  const props = PropertiesService.getScriptProperties();
  const key = uploadStateKey_(uploadId);
  const raw = props.getProperty(key);
  if (!raw) throw new Error('Phiên upload đã hết hạn. Hãy tạo phiên tải mới.');
  const state = JSON.parse(raw);
  if (Number(state.expires_at || 0) < Date.now()) {
    props.deleteProperty(key);
    throw new Error('Phiên upload đã hết hạn. Hãy tạo phiên tải mới.');
  }
  return state;
}

/**
 * Secure chunk relay: browser -> Apps Script -> Drive resumable session.
 * File never enters Streamlit/SQLite. Each call carries one bounded chunk.
 * V6.0 validates the decoded byte length against the browser slice length.
 * If google.script.run truncates/changes a payload, the server DOES NOT forward it
 * to Drive; it asks the browser to retry the same offset with a smaller chunk.
 */
function uploadChunk(uploadId, chunkBase64, startOffset, totalSize, expectedChunkSize) {
  const state = readUploadState_(String(uploadId || ''));
  const start = Number(startOffset || 0);
  const total = Number(totalSize || 0);
  if (total !== Number(state.total_size)) throw new Error('Kích thước file không khớp phiên upload.');
  if (start < 0 || start >= total) throw new Error('Offset chunk không hợp lệ.');
  const encoded = String(chunkBase64 || '');
  if (!encoded) throw new Error('Chunk đang trống.');
  const bytes = Utilities.base64Decode(encoded);
  const expected = Number(expectedChunkSize || bytes.length);
  const quantum = 256 * 1024;

  // google.script.run serializes strings between the HTML page and Apps Script.
  // For large Base64 strings the safest approach is to verify the exact decoded
  // byte count before forwarding anything to the Drive resumable session.
  if (expected <= 0 || expected > RECOMMENDED_CHUNK_BYTES) {
    throw new Error('Kích thước chunk dự kiến không hợp lệ: ' + expected + ' bytes.');
  }
  if (bytes.length !== expected) {
    const smaller = Math.max(quantum, Math.floor((Math.min(expected, RECOMMENDED_CHUNK_BYTES) / 2) / quantum) * quantum);
    return {
      complete: false, transport_mismatch: true, next_offset: start,
      expected_bytes: expected, actual_bytes: bytes.length,
      recommended_chunk_bytes: smaller
    };
  }

  const remaining = total - start;
  if (bytes.length > remaining) throw new Error('Chunk vượt phần dữ liệu còn lại của file.');
  const isFinalChunk = bytes.length === remaining;
  if (!isFinalChunk && bytes.length % quantum !== 0) {
    const smaller = Math.max(quantum, Math.floor((Math.min(bytes.length, RECOMMENDED_CHUNK_BYTES) / 2) / quantum) * quantum);
    return {
      complete: false, transport_mismatch: true, next_offset: start,
      expected_bytes: expected, actual_bytes: bytes.length,
      recommended_chunk_bytes: smaller
    };
  }
  const end = start + bytes.length - 1;

  const response = UrlFetchApp.fetch(state.session_url, {
    method: 'put',
    contentType: state.mime_type || 'application/octet-stream',
    payload: bytes,
    headers: {
      Authorization: 'Bearer ' + ScriptApp.getOAuthToken(),
      'Content-Range': 'bytes ' + start + '-' + end + '/' + total
    },
    muteHttpExceptions: true
  });
  const code = response.getResponseCode();
  const headers = response.getHeaders();
  const newLocation = headers.Location || headers.location;
  if (newLocation) state.session_url = String(newLocation);

  if (code === 308) {
    const range = String(headers.Range || headers.range || '');
    const m = range.match(/bytes=0-(\d+)/);
    state.next_offset = m ? Number(m[1]) + 1 : end + 1;
    saveUploadState_(uploadId, state);
    return {complete: false, next_offset: state.next_offset, uploaded_bytes: state.next_offset};
  }

  if (code === 200 || code === 201) {
    let info = {};
    try { info = JSON.parse(response.getContentText() || '{}'); } catch (ignore) {}
    if (!info.id) throw new Error('Drive hoàn tất upload nhưng không trả file ID.');
    const file = DriveApp.getFileById(info.id);
    assertFileInFolder_(file, state.folder_id);
    archiveSameNameExcept_(DriveApp.getFolderById(state.folder_id), state.file_name, info.id);
    try {
      file.setDescription('QLDA V6.0 | Resumable upload: ' + new Date().toISOString() + ' | User: ' + state.email);
    } catch (ignore) {}
    audit_('RESUMABLE_UPLOAD', state.email, state.file_name, state.project_code + '/' + state.record_code);
    PropertiesService.getScriptProperties().deleteProperty(uploadStateKey_(uploadId));
    return {complete: true, next_offset: total, file: fileInfoObject_(file, state.folder_url)};
  }

  if (code >= 500) {
    saveUploadState_(uploadId, state);
    return {complete: false, retryable: true, http_status: code, next_offset: state.next_offset || start};
  }
  throw new Error('Google Drive HTTP ' + code + ': ' + response.getContentText().substring(0, 300));
}

function queryResumableUpload(uploadId) {
  const state = readUploadState_(String(uploadId || ''));
  const total = Number(state.total_size || 0);
  const response = UrlFetchApp.fetch(state.session_url, {
    method: 'put',
    payload: '',
    headers: {
      Authorization: 'Bearer ' + ScriptApp.getOAuthToken(),
      'Content-Range': 'bytes */' + total
    },
    muteHttpExceptions: true
  });
  const code = response.getResponseCode();
  const headers = response.getHeaders();
  if (code === 308) {
    const range = String(headers.Range || headers.range || '');
    const m = range.match(/bytes=0-(\d+)/);
    state.next_offset = m ? Number(m[1]) + 1 : 0;
    saveUploadState_(uploadId, state);
    return {complete: false, next_offset: state.next_offset};
  }
  if (code === 200 || code === 201) {
    let info = {};
    try { info = JSON.parse(response.getContentText() || '{}'); } catch (ignore) {}
    if (info.id) {
      const file = DriveApp.getFileById(info.id);
      archiveSameNameExcept_(DriveApp.getFolderById(state.folder_id), state.file_name, info.id);
      PropertiesService.getScriptProperties().deleteProperty(uploadStateKey_(uploadId));
      return {complete: true, next_offset: total, file: fileInfoObject_(file, state.folder_url)};
    }
  }
  if (code === 404) throw new Error('Phiên resumable Google Drive đã hết hạn.');
  throw new Error('Không truy vấn được phiên upload. HTTP ' + code);
}

function listRecordFiles_(body) {
  requireRole_(body, ['read', 'update', 'admin']);
  const primary = ensureRecordFolderFromBody_(body);
  const folders = [primary];
  const legacy = findLegacyRecordFolderFromBody_(body);
  if (legacy && legacy.getId() !== primary.getId()) folders.push(legacy);

  const files = [];
  const seen = {};
  folders.forEach(function(target) {
    const it = target.getFiles();
    while (it.hasNext()) {
      const f = it.next();
      if (seen[f.getId()]) continue;
      seen[f.getId()] = true;
      files.push(fileInfoObject_(f, target.getUrl()));
    }
    if (body.include_history) {
      const hit = target.getFoldersByName('_Lich_su');
      if (hit.hasNext()) {
        const h = hit.next();
        const hi = h.getFiles();
        while (hi.hasNext()) {
          const hf = hi.next();
          if (seen[hf.getId()]) continue;
          seen[hf.getId()] = true;
          const item = fileInfoObject_(hf, h.getUrl());
          item.history = true;
          files.push(item);
        }
      }
    }
  });
  files.sort((a, b) => String(b.modified_time || '').localeCompare(String(a.modified_time || '')));
  return {
    folder: {id: primary.getId(), name: primary.getName(), url: primary.getUrl()},
    files: files,
    tower: towerFolderNameFromRecordCode_(body.record_code || '')
  };
}

function recordFileCounts_(body) {
  requireRole_(body, ['read', 'update', 'admin']);
  const rawCodes = Array.isArray(body.record_codes) ? body.record_codes.slice(0, 500) : [];
  const result = {};
  rawCodes.forEach(codeValue => {
    const raw = String(codeValue || '').trim();
    if (!raw) return;
    const entry = {count: 0, latest_modified: '', folder_url: '', tower: towerFolderNameFromRecordCode_(raw)};
    const seen = {};
    const localBody = {
      project_code: body.project_code,
      kind: body.kind,
      subtype: body.subtype,
      record_code: raw
    };
    const folders = [];
    const primary = findRecordFolderFromBody_(localBody, false);
    if (primary) folders.push(primary);
    const legacy = findLegacyRecordFolderFromBody_(localBody);
    if (legacy && (!primary || legacy.getId() !== primary.getId())) folders.push(legacy);
    folders.forEach(function(folder) {
      if (!entry.folder_url) entry.folder_url = folder.getUrl();
      const fit = folder.getFiles();
      while (fit.hasNext()) {
        const f = fit.next();
        if (seen[f.getId()]) continue;
        seen[f.getId()] = true;
        entry.count++;
        try {
          const t = f.getLastUpdated().toISOString();
          if (!entry.latest_modified || t > entry.latest_modified) entry.latest_modified = t;
        } catch (ignore) {}
      }
    });
    result[raw] = entry;
  });
  return {counts: result};
}

function fileInfo_(body) {
  requireRole_(body, ['read', 'update', 'admin']);
  const id = String(body.file_id || '').trim();
  if (!id) throw new Error('Thiếu file_id.');
  const file = DriveApp.getFileById(id);
  return {file: fileInfoObject_(file, '')};
}

function towerFolderNameFromRecordCode_(recordCode) {
  const code = sanitizeName_(String(recordCode || '')).toUpperCase();
  const first = code.split('-')[0] || 'KHAC';
  return 'Tháp ' + first;
}

function usesTowerFolder_(kind) {
  const k = String(kind || '').toLowerCase();
  return k === 'document' || k === 'drawing';
}

function existingChildFolder_(parent, name) {
  if (!parent) return null;
  const it = parent.getFoldersByName(sanitizeName_(name));
  return it.hasNext() ? it.next() : null;
}

function routeFolder_(start, route, createMissing) {
  let target = start;
  for (let i = 0; i < route.length; i++) {
    if (!target) return null;
    target = createMissing ? ensureChildFolder_(target, route[i]) : existingChildFolder_(target, route[i]);
  }
  return target;
}

function findProjectFolder_(projectCode, createMissing) {
  const root = ensureRootFolder_();
  const name = sanitizeName_(projectCode || 'DU_AN');
  return createMissing ? ensureChildFolder_(root, name) : existingChildFolder_(root, name);
}

function findRecordFolderFromBody_(body, createMissing) {
  const project = findProjectFolder_(body.project_code || 'DU_AN', createMissing);
  if (!project) return null;
  const route = storageRoute_(String(body.kind || ''), String(body.subtype || ''));
  let target = project;
  if (usesTowerFolder_(body.kind)) {
    const towerName = towerFolderNameFromRecordCode_(body.record_code || '');
    target = createMissing ? ensureChildFolder_(target, towerName) : existingChildFolder_(target, towerName);
    if (!target) return null;
  }
  target = routeFolder_(target, route, createMissing);
  if (!target) return null;
  const recordName = sanitizeName_(body.record_code || 'Chung');
  return createMissing ? ensureChildFolder_(target, recordName) : existingChildFolder_(target, recordName);
}

function findLegacyRecordFolderFromBody_(body) {
  if (!usesTowerFolder_(body.kind)) return null;
  const project = findProjectFolder_(body.project_code || 'DU_AN', false);
  if (!project) return null;
  const route = storageRoute_(String(body.kind || ''), String(body.subtype || ''));
  const routeFolder = routeFolder_(project, route, false);
  if (!routeFolder) return null;
  return existingChildFolder_(routeFolder, sanitizeName_(body.record_code || 'Chung'));
}

function ensureRecordFolderFromBody_(body) {
  return findRecordFolderFromBody_(body, true);
}

function assertFileInFolder_(file, folderId) {
  const parents = file.getParents();
  while (parents.hasNext()) {
    if (parents.next().getId() === folderId) return true;
  }
  throw new Error('File hoàn tất không nằm trong thư mục QLDA dự kiến.');
}

function archiveSameNameExcept_(target, fileName, keepFileId) {
  const it = target.getFilesByName(fileName);
  let history = null;
  const stamp = Utilities.formatDate(new Date(), Session.getScriptTimeZone() || 'Asia/Ho_Chi_Minh', 'yyyyMMdd_HHmmss');
  let n = 1;
  while (it.hasNext()) {
    const f = it.next();
    if (f.getId() === keepFileId) continue;
    if (!history) history = ensureChildFolder_(target, '_Lich_su');
    f.setName(stamp + '_' + n + '__' + fileName);
    f.moveTo(history);
    n++;
  }
}

function fileInfoObject_(file, folderUrl) {
  let size = 0;
  try { size = Number(file.getSize() || 0); } catch (ignore) {}
  let modified = '';
  try { modified = file.getLastUpdated().toISOString(); } catch (ignore) {}
  return {
    id: file.getId(),
    name: file.getName(),
    url: file.getUrl(),
    webViewLink: file.getUrl(),
    download_url: 'https://drive.google.com/uc?export=download&id=' + encodeURIComponent(file.getId()),
    mime_type: file.getMimeType(),
    size: size,
    modified_time: modified,
    folder_url: folderUrl || ''
  };
}

// -----------------------------------------------------------------------------
// LEGACY SMALL-FILE FUNCTIONS - only for migration / old data paths
// -----------------------------------------------------------------------------

function uploadLegacy_(body) {
  const session = requireUploadRole_(body);
  const fileName = sanitizeFileName_(body.file_name || 'file.bin');
  const mimeType = String(body.mime_type || 'application/octet-stream');
  const encoded = String(body.file_base64 || '');
  if (!encoded) throw new Error('Nội dung file đang trống.');
  const bytes = Utilities.base64Decode(encoded);
  if (bytes.length > LEGACY_MAX_UPLOAD_BYTES) throw new Error('File vượt giới hạn legacy 30 MB. Hãy dùng Direct Upload V6.0.');
  const target = ensureRecordFolderFromBody_(body);
  archiveSameName_(target, fileName);
  const file = target.createFile(Utilities.newBlob(bytes, mimeType, fileName));
  audit_('LEGACY_UPLOAD', session.email, fileName, String(body.project_code || ''));
  return {file: fileInfoObject_(file, target.getUrl())};
}

function downloadLegacy_(body) {
  requireRole_(body, ['read', 'update', 'admin']);
  const id = String(body.file_id || '').trim();
  if (!id) throw new Error('Thiếu file_id.');
  const file = DriveApp.getFileById(id);
  const blob = file.getBlob();
  const bytes = blob.getBytes();
  if (bytes.length > LEGACY_MAX_UPLOAD_BYTES) throw new Error('File lớn: hãy mở/tải trực tiếp bằng link Google Drive.');
  return {file: {id: id, name: file.getName(), mime_type: blob.getContentType(), file_base64: Utilities.base64Encode(bytes)}};
}

function trashFile_(body) {
  const session = requireRole_(body, ['admin']);
  const id = String(body.file_id || '').trim();
  if (!id) throw new Error('Thiếu file_id.');
  const file = DriveApp.getFileById(id);
  const name = file.getName();
  file.setTrashed(true);
  audit_('TRASH_FILE', session.email, name, id);
  return {trashed: id};
}

// -----------------------------------------------------------------------------
// HTML DIRECT UPLOADER
// -----------------------------------------------------------------------------

function renderUploaderPage_(ticket) {
  let meta;
  try {
    meta = readUploadTicket_(ticket);
  } catch (err) {
    const detail = escapeHtml_(String(err && err.message ? err.message : err));
    return HtmlService.createHtmlOutput('<div style="font-family:Arial,sans-serif;padding:24px"><h2>Không mở được phiên đính kèm file.</h2><p>' + detail + '</p><p>Đóng khung này và bấm <b>Đính kèm file</b> để tạo phiên mới.</p></div>')
      .setTitle('QLDA V6.0 - Phiên đính kèm')
      .setXFrameOptionsMode(HtmlService.XFrameOptionsMode.ALLOWALL);
  }
  const ticketJs = JSON.stringify(String(ticket));
  const maxJs = Number(meta.max_bytes || MAX_DIRECT_UPLOAD_BYTES);
  const folderUrlJs = JSON.stringify(String(meta.folder_url || ''));
  const label = escapeHtml_(meta.project_code + ' / ' + (meta.tower_name || towerFolderNameFromRecordCode_(meta.record_code || '')) + ' / ' + meta.record_code);
  const html = `<!doctype html>
<html><head><base target="_top"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>QLDA V6.0 - Direct Drive Upload</title>
<style>
body{font-family:Arial,sans-serif;margin:0;background:#f6f8fb;color:#172033}.wrap{max-width:900px;margin:28px auto;padding:0 18px}.card{background:white;border:1px solid #dfe5ee;border-radius:14px;padding:22px;box-shadow:0 4px 16px rgba(20,40,80,.06)}h1{font-size:25px;margin:0 0 8px}.muted{color:#667085}.drop{margin:18px 0;border:2px dashed #8aa4c8;border-radius:12px;padding:28px;text-align:center;background:#f9fbff}input[type=file]{max-width:100%}button,.btn{background:#146cf0;color:white;border:0;border-radius:9px;padding:11px 16px;font-weight:700;cursor:pointer;text-decoration:none;display:inline-block}.btn2{background:#eef4ff;color:#174ea6}.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}.file{border-top:1px solid #edf0f5;padding:14px 0}.bar{height:12px;background:#e9eef5;border-radius:999px;overflow:hidden}.fill{height:100%;width:0;background:#146cf0;transition:width .15s}.ok{color:#087443}.err{color:#c62828;white-space:pre-wrap}.name{font-weight:700;word-break:break-all}.small{font-size:13px}.badge{display:inline-block;background:#edf5ff;color:#1c5ca8;border-radius:999px;padding:4px 9px;font-size:12px;font-weight:700}
</style></head>
<body><div class="wrap"><div class="card">
<h1>📎 QLDA V6.0 — Đính kèm file vào Google Drive</h1>
<div class="muted">Đích: <b>${label}</b></div>
<div class="drop"><p><b>Chọn một hoặc nhiều file</b></p><input id="files" type="file" multiple></div>
<div class="row"><button id="start">⬆ Tải lên</button><a class="btn btn2" href=${folderUrlJs} target="_blank">📂 Mở thư mục Drive</a></div>
<div id="status" style="margin-top:18px"></div>
</div></div>
<script>
const TICKET=${ticketJs}; const MAX_BYTES=${maxJs};
const input=document.getElementById('files'), status=document.getElementById('status'), startBtn=document.getElementById('start');
function fmt(n){if(n>=1073741824)return(n/1073741824).toFixed(2)+' GB';if(n>=1048576)return(n/1048576).toFixed(1)+' MB';if(n>=1024)return(n/1024).toFixed(1)+' KB';return n+' B'}
function gas(fn,...args){return new Promise((resolve,reject)=>{google.script.run.withSuccessHandler(resolve).withFailureHandler(e=>reject(new Error((e&&e.message)||String(e))))[fn](...args)})}
function rowFor(file){const d=document.createElement('div');d.className='file';d.innerHTML='<div class="name"></div><div class="muted small meta"></div><div class="bar"><div class="fill"></div></div><div class="small msg" style="margin-top:6px"></div>';d.querySelector('.name').textContent=file.name;d.querySelector('.meta').textContent=fmt(file.size);status.appendChild(d);return d}
function readChunkBase64(blob){return new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>{const v=String(r.result||'');const i=v.indexOf(',');resolve(i>=0?v.slice(i+1):v)};r.onerror=()=>reject(new Error('Không đọc được chunk file trên trình duyệt.'));r.readAsDataURL(blob)})}
async function uploadOne(file,row){const fill=row.querySelector('.fill'),msg=row.querySelector('.msg');if(file.size>MAX_BYTES)throw new Error('File '+file.name+' vượt giới hạn 2 GB.');msg.textContent='Đang khởi tạo phiên resumable Google Drive...';const init=await gas('startResumableUpload',TICKET,file.name,file.type||'application/octet-stream',file.size);const uploadId=init.upload_id;const QUANTUM=262144;let chunk=Number(init.chunk_bytes)||2097152,pos=Number(init.next_offset)||0,retries=0;while(pos<file.size){const remaining=file.size-pos;let sendSize=Math.min(chunk,remaining);if(sendSize<remaining){sendSize=Math.max(QUANTUM,Math.floor(sendSize/QUANTUM)*QUANTUM)}const end=pos+sendSize;const blob=file.slice(pos,end);msg.textContent='Đang đọc '+fmt(blob.size)+' tại '+fmt(pos)+' / '+fmt(file.size);const b64=await readChunkBase64(blob);msg.textContent='Đang chuyển '+fmt(pos)+' / '+fmt(file.size)+' tới Google Drive...';let res;try{res=await gas('uploadChunk',uploadId,b64,pos,file.size,blob.size)}catch(err){if(retries++>=5)throw err;await new Promise(r=>setTimeout(r,1000*Math.pow(2,retries)));try{const q=await gas('queryResumableUpload',uploadId);if(q.complete){res=q}else{pos=Number.isFinite(Number(q.next_offset))?Number(q.next_offset):pos;continue}}catch(ignore){continue}}
if(res&&res.complete){fill.style.width='100%';const done=res.file||{};msg.innerHTML='<span class="ok">✅ Hoàn thành: '+fmt(file.size)+'</span>'+(done.url?' — <a target="_blank" href="'+done.url+'">Mở file trên Drive</a>':'');return done}
if(res&&res.transport_mismatch){const recommended=Number(res.recommended_chunk_bytes)||Math.floor(chunk/2);chunk=Math.max(QUANTUM,Math.floor(recommended/QUANTUM)*QUANTUM);pos=Number.isFinite(Number(res.next_offset))?Number(res.next_offset):pos;retries=0;msg.textContent='Kênh truyền chưa đủ byte. Tự giảm chunk xuống '+fmt(chunk)+' và thử lại từ '+fmt(pos)+'...';await new Promise(r=>setTimeout(r,250));continue}
if(res&&res.retryable){if(retries++>=5)throw new Error('Google Drive tạm lỗi HTTP '+res.http_status);await new Promise(r=>setTimeout(r,1000*Math.pow(2,retries)));const q=await gas('queryResumableUpload',uploadId);pos=Number.isFinite(Number(q.next_offset))?Number(q.next_offset):pos;continue}
pos=Number.isFinite(Number(res&&res.next_offset))?Number(res.next_offset):end;retries=0;fill.style.width=Math.min(100,pos/file.size*100).toFixed(1)+'%';msg.textContent='Đã tải '+fmt(pos)+' / '+fmt(file.size)+' ('+(pos/file.size*100).toFixed(1)+'%) — chunk '+fmt(chunk)}throw new Error('Upload kết thúc không xác định.')}
async function startSelectedFiles(){const files=[...input.files];if(!files.length){alert('Hãy chọn file.');return}if(startBtn.disabled)return;startBtn.disabled=true;status.innerHTML='';let okCount=0;for(const f of files){const row=rowFor(f);try{await uploadOne(f,row);okCount++}catch(e){row.querySelector('.msg').innerHTML='<span class="err">❌ '+String(e.message||e)+'</span>'}}startBtn.disabled=false;if(okCount){const doneNote=document.createElement('div');doneNote.className='ok';doneNote.style.marginTop='14px';doneNote.style.fontWeight='700';doneNote.textContent='✅ Đã tải xong '+okCount+' file.';status.appendChild(doneNote);}}
startBtn.onclick=startSelectedFiles;
input.addEventListener('change',()=>{
  status.innerHTML='';
  const files=[...input.files];
  if(files.length){
    const note=document.createElement('div');
    note.className='muted small';
    note.style.marginTop='12px';
    note.textContent='Đã chọn '+files.length+' file. Bấm “Tải lên” để bắt đầu.';
    status.appendChild(note);
  }
});
</script></body></html>`;
  return HtmlService.createHtmlOutput(html)
    .setTitle('QLDA V6.0 - Direct Drive Upload')
    .setXFrameOptionsMode(HtmlService.XFrameOptionsMode.ALLOWALL);
}

// -----------------------------------------------------------------------------
// FOLDER / STORAGE / USER HELPERS
// -----------------------------------------------------------------------------

function storageRoute_(kind, subtype) {
  const k = String(kind || '').toLowerCase();
  const s = sanitizeName_(subtype || 'Khac');
  if (k === 'document') {
    if (s.toUpperCase() === 'VO') return ['04_Phat_sinh_VO', 'VO'];
    return ['02_Ho_so', s];
  }
  if (k === 'drawing') return ['03_Ban_ve', s];
  if (k === 'schedule') return ['01_Tien_do'];
  if (k === 'report') return ['05_Bao_cao', s];
  return ['99_Tai_lieu_khac', s];
}

function archiveSameName_(target, fileName) {
  const it = target.getFilesByName(fileName);
  if (!it.hasNext()) return;
  const history = ensureChildFolder_(target, '_Lich_su');
  const stamp = Utilities.formatDate(new Date(), Session.getScriptTimeZone() || 'Asia/Ho_Chi_Minh', 'yyyyMMdd_HHmmss');
  let n = 1;
  while (it.hasNext()) {
    const f = it.next();
    f.setName(stamp + '_' + n + '__' + fileName);
    f.moveTo(history);
    n++;
  }
}

function ensureRootFolder_() {
  const it = DriveApp.getFoldersByName(ROOT_FOLDER_NAME);
  const folder = it.hasNext() ? it.next() : DriveApp.createFolder(ROOT_FOLDER_NAME);
  try { folder.setShareableByEditors(false); } catch (ignore) {}
  return folder;
}

function ensurePrivateFolder_() {
  const it = DriveApp.getFoldersByName(PRIVATE_FOLDER_NAME);
  const folder = it.hasNext() ? it.next() : DriveApp.createFolder(PRIVATE_FOLDER_NAME);
  try { folder.setShareableByEditors(false); } catch (ignore) {}
  return folder;
}

function ensureChildFolder_(parent, name) {
  const safe = sanitizeName_(name);
  const it = parent.getFoldersByName(safe);
  return it.hasNext() ? it.next() : parent.createFolder(safe);
}

function readUsers_() {
  const folder = ensurePrivateFolder_();
  const it = folder.getFilesByName(USERS_FILE_NAME);
  if (!it.hasNext()) return [];
  try {
    const data = JSON.parse(it.next().getBlob().getDataAsString('UTF-8') || '[]');
    return Array.isArray(data) ? data : [];
  } catch (err) {
    throw new Error('Không đọc được users.json trong thư mục hệ thống riêng: ' + err.message);
  }
}

function writeUsers_(users) {
  const folder = ensurePrivateFolder_();
  const content = JSON.stringify(users, null, 2);
  const it = folder.getFilesByName(USERS_FILE_NAME);
  if (it.hasNext()) it.next().setContent(content);
  else folder.createFile(USERS_FILE_NAME, content, MimeType.PLAIN_TEXT);
}

function audit_(action, actor, target, detail) {
  try {
    const folder = ensurePrivateFolder_();
    const name = 'audit_' + Utilities.formatDate(new Date(), Session.getScriptTimeZone() || 'Asia/Ho_Chi_Minh', 'yyyyMM') + '.log';
    const line = [new Date().toISOString(), action, actor || '', target || '', detail || ''].join('\t') + '\n';
    const it = folder.getFilesByName(name);
    if (it.hasNext()) {
      const f = it.next();
      f.setContent(f.getBlob().getDataAsString('UTF-8') + line);
    } else folder.createFile(name, line, MimeType.PLAIN_TEXT);
  } catch (ignore) {}
}

function newUser_(email, name, role, password, approvalRole) {
  const salt = randomSecret_();
  const now = new Date().toISOString();
  const normalizedRole = normalizeRole_(role);
  let ar = normalizeApprovalRole_(approvalRole);
  if (normalizedRole === 'admin' && !ar) ar = 'PROJECT_MANAGEMENT';
  return {email: email, name: name || '', role: normalizedRole, approval_role: ar, approval_group: legacyApprovalGroup_(ar), active: true, salt: salt, password_hash: passwordHash_(salt, password), created_at: now, updated_at: now};
}

function publicUser_(u) {
  let ar = normalizeApprovalRole_(u.approval_role || u.approval_group || '');
  if (String(u.role || '').toLowerCase() === 'admin' && !ar) ar = 'PROJECT_MANAGEMENT';
  return {email: u.email, name: u.name || '', role: u.role, approval_role: ar, approval_group: legacyApprovalGroup_(ar), active: u.active !== false, created_at: u.created_at || '', updated_at: u.updated_at || ''};
}

function currentUserRecord_(email) {
  const u = readUsers_().find(x => x.email === email && x.active !== false);
  if (!u) throw new Error('Tài khoản đã bị xóa hoặc vô hiệu hóa.');
  return u;
}

function requireSession_(body) {
  const token = String(body.session_token || '');
  const session = verifySessionToken_(token);
  const u = currentUserRecord_(session.email);
  session.role = u.role;
  session.name = u.name || '';
  session.approval_role = normalizeApprovalRole_(u.approval_role || u.approval_group || '');
  if (String(u.role || '').toLowerCase() === 'admin' && !session.approval_role) session.approval_role = 'PROJECT_MANAGEMENT';
  return session;
}

function requireRole_(body, allowed) {
  const s = requireSession_(body);
  if (allowed.indexOf(s.role) < 0) throw new Error('Bạn không có quyền thực hiện chức năng này.');
  return s;
}

// V6.11: quyền upload riêng cho Nhà thầu trong luồng phê duyệt online.
// Tài khoản hệ thống 'read' vẫn được upload CHỈ khi approval_role=CONTRACTOR,
// purpose=approval_submission và subtype thuộc nhóm được duyệt online.
function isApprovalSubmissionTarget_(body) {
  const purpose = String(body.upload_purpose || '').trim().toLowerCase();
  const kind = String(body.kind || '').trim().toLowerCase();
  const subtype = String(body.subtype || '').trim().toUpperCase();
  const recordCode = String(body.record_code || '').trim().toUpperCase();
  if (purpose !== 'approval_submission') return false;
  // Chỉ cho upload vào mã hồ sơ chuẩn THÁP-BỘMÔN-STT, ví dụ S2-MEP-002.
  if (!/^[A-Z]+\d+-[A-Z0-9]+-\d{3,}$/.test(recordCode)) return false;
  if (kind === 'document') return ['RFA','RFI'].indexOf(subtype) >= 0;
  if (kind === 'drawing') return ['SHOPDRAWING','AS_BUILT'].indexOf(subtype) >= 0;
  return false;
}

function requireUploadRole_(body) {
  const s = requireSession_(body);
  if (['update','admin'].indexOf(String(s.role || '').toLowerCase()) >= 0) return s;
  const approvalRole = normalizeApprovalRole_(s.approval_role || '');
  if (String(s.role || '').toLowerCase() === 'read' && approvalRole === 'CONTRACTOR' && isApprovalSubmissionTarget_(body)) {
    return s;
  }
  throw new Error('Bạn không có quyền tải file. Nhà thầu chỉ được tải file cho RFA/RFI/Shopdrawing/Hoàn công trong luồng phê duyệt online.');
}

function makeSessionToken_(u) {
  const payload = {email: u.email, role: u.role, exp: Math.floor(Date.now() / 1000) + SESSION_TTL_SECONDS, nonce: randomSecret_()};
  const body = Utilities.base64EncodeWebSafe(JSON.stringify(payload), Utilities.Charset.UTF_8).replace(/=+$/g, '');
  const sig = Utilities.base64EncodeWebSafe(Utilities.computeHmacSha256Signature(body, API_TOKEN)).replace(/=+$/g, '');
  return body + '.' + sig;
}

function verifySessionToken_(token) {
  const parts = String(token || '').split('.');
  if (parts.length !== 2) throw new Error('Phiên đăng nhập không hợp lệ.');
  const expected = Utilities.base64EncodeWebSafe(Utilities.computeHmacSha256Signature(parts[0], API_TOKEN)).replace(/=+$/g, '');
  if (expected !== parts[1]) throw new Error('Chữ ký phiên đăng nhập không hợp lệ.');
  const payload = JSON.parse(Utilities.newBlob(Utilities.base64DecodeWebSafe(parts[0])).getDataAsString('UTF-8'));
  if (!payload.exp || Number(payload.exp) < Math.floor(Date.now() / 1000)) throw new Error('Phiên đăng nhập đã hết hạn. Hãy đăng nhập lại.');
  return payload;
}

function applyDriveRole_(email, role) {
  const root = ensureRootFolder_();
  try { root.revokePermissions(email); } catch (ignore) {}
  try {
    // V6.0 RBAC:
    // - read   : Viewer Drive, chỉ xem.
    // - update : Viewer Drive, nhưng được thêm/sửa/upload qua Gateway; KHÔNG được xóa.
    // - admin  : Editor Drive + toàn quyền quản trị/xóa trong app.
    // Việc giữ update ở Viewer ngăn xóa trực tiếp trên giao diện Google Drive.
    if (role === 'admin') root.addEditor(email);
    else root.addViewer(email);
  } catch (err) {}
}

function revokeDriveRole_(email) {
  try { ensureRootFolder_().revokePermissions(email); } catch (ignore) {}
}

function validatePassword_(password) {
  const p = String(password || '');
  if (p.length < 8) throw new Error('Mật khẩu phải có ít nhất 8 ký tự.');
}

function passwordHash_(salt, password) {
  const bytes = Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, String(salt) + '|' + String(password));
  return bytesToHex_(bytes);
}

function bytesToHex_(bytes) {
  return bytes.map(function(b) { const v = (b < 0 ? b + 256 : b); return ('0' + v.toString(16)).slice(-2); }).join('');
}

function randomSecret_() {
  return Utilities.getUuid().replace(/-/g, '') + Utilities.getUuid().replace(/-/g, '');
}

function normalizeEmail_(email) {
  const e = String(email || '').trim().toLowerCase();
  if (!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(e)) throw new Error('Email không hợp lệ.');
  return e;
}

function normalizeRole_(role) {
  const r = String(role || '').toLowerCase();
  if (['read', 'update', 'admin'].indexOf(r) < 0) throw new Error('Quyền không hợp lệ.');
  return r;
}

function normalizeApprovalRole_(role) {
  const raw = String(role || '').trim().toUpperCase().replace(/[-\s]+/g, '_');
  const aliases = {
    '': '',
    'NONE': '',
    'CONTRACTOR': 'CONTRACTOR',
    'SITE_MANAGEMENT': 'SITE_MANAGEMENT',
    'EXECUTIVE': 'SITE_MANAGEMENT',
    'BAN_DIEU_HANH': 'SITE_MANAGEMENT',
    'CONSULTANT': 'CONSULTANT',
    'TVGS': 'CONSULTANT',
    'SUPERVISION': 'CONSULTANT',
    'PROJECT_MANAGEMENT': 'PROJECT_MANAGEMENT',
    'BQLDA': 'PROJECT_MANAGEMENT',
    'PMB': 'PROJECT_MANAGEMENT'
  };
  if (!Object.prototype.hasOwnProperty.call(aliases, raw)) throw new Error('Phân loại phê duyệt không hợp lệ.');
  return aliases[raw];
}

function legacyApprovalGroup_(role) {
  const r = normalizeApprovalRole_(role);
  const map = {
    '': 'none',
    'CONTRACTOR': 'contractor',
    'SITE_MANAGEMENT': 'site_management',
    'CONSULTANT': 'tvgs',
    'PROJECT_MANAGEMENT': 'bqlda'
  };
  return map[r] || 'none';
}

function sendApprovalEmail_(body) {
  const session = requireRole_(body, ['update', 'admin']);
  const to = normalizeEmail_(body.to_email);
  const users = readUsers_();
  const target = users.find(x => x.email === to && x.active !== false);
  if (!target) throw new Error('Email người nhận chưa được khai báo trong danh sách người dùng QLDA.');
  const subject = String(body.subject || 'QLDA - Hồ sơ cần phê duyệt').substring(0, 180);
  const text = String(body.body || '').substring(0, 12000);
  const appUrl = String(body.app_url || '').trim();
  const html = '<div style="font-family:Arial,sans-serif;line-height:1.5">' +
    '<h3>' + escapeHtml_(subject) + '</h3>' +
    '<div>' + escapeHtml_(text).replace(/\n/g,'<br>') + '</div>' +
    (appUrl ? '<p><a href="' + escapeHtml_(appUrl) + '">Mở QLDA để xem và phê duyệt</a></p>' : '') +
    '<p style="color:#666">Email tự động từ QLDA Xây dựng V6.</p></div>';
  MailApp.sendEmail({to: to, subject: subject, htmlBody: html, name: 'QLDA Xây dựng'});
  audit_('APPROVAL_EMAIL', session.email, to, subject);
  return {sent: true, to_email: to};
}

function sanitizeName_(value) {
  let s = String(value || '').trim().replace(/[\\\/:*?"<>|]+/g, '_');
  if (!s) s = 'Tai_lieu';
  return s.substring(0, 120);
}

function sanitizeFileName_(value) {
  let s = String(value || '').trim().replace(/[\\\/:*?"<>|]+/g, '_');
  if (!s) s = 'file.bin';
  return s.substring(0, 220);
}

function escapeHtml_(value) {
  return String(value || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');
}

function jsonResponse_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
