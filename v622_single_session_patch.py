from __future__ import annotations

import re


PATCH_MARKER = "V6.22 SINGLE SESSION UI V3 HISTORY10 VN"


def _replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"{PATCH_MARKER}: expected one {label}, found {count}")
    return source.replace(old, new, 1)


def patch_single_session(source: str) -> str:
    """Add single-login UX on top of the Drive/local auth gateway.

    Backend remains authoritative. The UI surfaces the rolling 10-login history,
    formats timestamps in Vietnam time, and keeps active-session revoke controls.

    V3 deliberately avoids matching the deployment-specific login page title.
    ``patch_local_vps`` rewrites "PostgreSQL Cloud" to "PostgreSQL VPS" when the
    local storage backend is active, so the authentication hook must be based on
    the structural ``if not _gateway_session_token():`` block instead.
    """
    if PATCH_MARKER in source:
        return source

    logout_pattern = re.compile(
        r"def _gateway_logout\(\) -> None:\n.*?\n\ndef _cloud_identity\(refresh: bool = False\):",
        re.S,
    )
    match = logout_pattern.search(source)
    if not match:
        raise RuntimeError(f"{PATCH_MARKER}: gateway logout function not found")
    logout_new = '''def _gateway_logout(preserve_error: bool = False) -> None:\n    # V6.22 SINGLE SESSION UI V3 HISTORY10 VN\n    token = str(st.session_state.get("qlda_drive_session_token", "") or "").strip()\n    holder = st.session_state.get("_qlda_drive_gateway_instance")\n    saved_error = str(st.session_state.get("qlda_drive_error", "") or "") if preserve_error else ""\n    try:\n        gateway = None\n        if isinstance(holder, DriveGateway):\n            gateway = holder\n        elif isinstance(holder, tuple) and len(holder) == 2 and isinstance(holder[1], DriveGateway):\n            gateway = holder[1]\n        if gateway is not None and token:\n            gateway.logout(token)\n    except Exception:\n        # A superseded token is expected to fail server-side; local cleanup still runs.\n        pass\n    try:\n        if isinstance(holder, DriveGateway):\n            holder.close()\n        elif isinstance(holder, tuple) and len(holder) == 2:\n            holder[1].close()\n    except Exception:\n        pass\n    st.session_state.pop("_qlda_drive_gateway_instance", None)\n    for key in ("qlda_drive_session_token", "qlda_drive_identity", "qlda_drive_error", "qlda_auth_restored_from_cookie", "_qlda_cookie_written_for_token"):\n        st.session_state.pop(key, None)\n    if preserve_error and saved_error:\n        st.session_state["qlda_drive_error"] = saved_error\n    st.session_state["qlda_ignore_persistent_auth"] = True\n    _clear_browser_session_cookie()\n\n\ndef _cloud_identity(refresh: bool = False):'''
    source = source[: match.start()] + logout_new + source[match.end() :]

    cloud_error_old = '''    except Exception as exc:\n        st.session_state["qlda_drive_error"] = str(exc)\n        _gateway_logout()\n        return {"role": "unknown", "email": "", "name": "", "label": "Chưa đăng nhập"}\n'''
    cloud_error_new = '''    except Exception as exc:\n        st.session_state["qlda_drive_error"] = str(exc)\n        _gateway_logout(preserve_error=True)\n        return {"role": "unknown", "email": "", "name": "", "label": "Chưa đăng nhập"}\n'''
    source = _replace_once(source, cloud_error_old, cloud_error_new, "preserve superseded-session error")

    # Deployment-safe login-page insertion. Do not match the title text because
    # the local VPS UI patch intentionally changes Cloud -> VPS before this patch.
    login_page_pattern = re.compile(
        r'(?m)^(?P<if_indent>[ \t]*)if not _gateway_session_token\(\):\n'
        r'(?P<title_indent>[ \t]+)st\.title\([^\n]+\)\n'
    )
    login_matches = list(login_page_pattern.finditer(source))
    if len(login_matches) != 1:
        raise RuntimeError(
            f"{PATCH_MARKER}: expected one structural login session block, found {len(login_matches)}"
        )
    login_match = login_matches[0]
    title_indent = login_match.group("title_indent")
    login_error_ui = (
        f'{title_indent}_session_error = str(st.session_state.get("qlda_drive_error", "") or "").strip()\n'
        f'{title_indent}if _session_error:\n'
        f'{title_indent}    st.warning(_session_error)\n'
    )
    source = source[: login_match.end()] + login_error_ui + source[login_match.end() :]

    login_call_old = '''                result = gw.login(email, password)\n                token = str(result.get("session_token") or "")\n'''
    login_call_new = '''                try:\n                    _client_info = str(st.context.headers.get("User-Agent", "") or "")[:500]\n                except Exception:\n                    _client_info = ""\n                result = gw.login(email, password, client_info=_client_info)\n                token = str(result.get("session_token") or "")\n'''
    source = _replace_once(source, login_call_old, login_call_new, "login client metadata")

    login_success_old = '''                st.session_state["qlda_drive_session_token"] = token\n                st.session_state.pop("qlda_drive_identity", None)\n                st.session_state.pop("qlda_ignore_persistent_auth", None)\n'''
    login_success_new = '''                st.session_state["qlda_drive_session_token"] = token\n                st.session_state.pop("qlda_drive_identity", None)\n                st.session_state.pop("qlda_drive_error", None)\n                st.session_state.pop("qlda_ignore_persistent_auth", None)\n'''
    source = _replace_once(source, login_success_old, login_success_new, "clear session error after login")

    admin_else = '''                else:\n                    st.info("Chỉ Admin mới được quản lý tài khoản và phân quyền.")\n'''
    session_ui = '''                    st.markdown("### 🔐 Phiên đăng nhập")\n                    _ui_note(\n                        "Mỗi tài khoản chỉ có 01 phiên đang hoạt động. Hệ thống lưu cuốn chiếu 10 lần đăng nhập gần nhất. "\n                        "Thời gian bên dưới hiển thị theo giờ Việt Nam (UTC+7)."\n                    )\n                    try:\n                        def _v622_vn_time(value):\n                            if value in (None, ""):\n                                return ""\n                            try:\n                                _ts = pd.to_datetime(value, utc=True, errors="coerce")\n                                if pd.isna(_ts):\n                                    return str(value)\n                                return _ts.tz_convert("Asia/Ho_Chi_Minh").strftime("%d/%m/%Y %H:%M:%S")\n                            except Exception:\n                                return str(value)\n\n                        def _v622_session_status(item):\n                            if bool(item.get("active")):\n                                return "Đang hoạt động"\n                            _reason = str(item.get("end_reason") or "").upper()\n                            return {\n                                "LOGOUT": "Đã đăng xuất",\n                                "REPLACED": "Đã bị thay thế",\n                                "ADMIN_FORCE_LOGOUT": "Admin kết thúc",\n                                "EXPIRED": "Hết hạn",\n                                "PASSWORD_RESET": "Đặt lại mật khẩu",\n                                "PASSWORD_CHANGED": "Đổi mật khẩu",\n                            }.get(_reason, "Đã kết thúc")\n\n                        _sessions = gw.list_sessions(token)\n                        _history = list(gw.list_session_history(token) or [])[:10]\n                        if _history:\n                            _session_df = pd.DataFrame([{\n                                "Tên": x.get("name", ""),\n                                "Email": x.get("email", ""),\n                                "Quyền": {"read":"Chỉ đọc","update":"Cập nhật","admin":"Admin"}.get(x.get("role", ""), x.get("role", "")),\n                                "Trạng thái": _v622_session_status(x),\n                                "Đăng nhập": _v622_vn_time(x.get("created_at", "")),\n                                "Hoạt động gần nhất": _v622_vn_time(x.get("last_seen_at", "")),\n                                "Hết hạn": _v622_vn_time(x.get("expires_at", "")),\n                                "Thiết bị / trình duyệt": x.get("client_info", ""),\n                            } for x in _history])\n                            st.dataframe(_session_df, hide_index=True, width="stretch")\n                        else:\n                            st.info("Chưa có lịch sử đăng nhập.")\n\n                        if _sessions:\n                            _current_session_email = str(ident.get("email") or "").strip().lower()\n                            _revocable = [\n                                str(x.get("email") or "") for x in _sessions\n                                if str(x.get("email") or "").strip().lower() != _current_session_email\n                            ]\n                            if _revocable:\n                                _session_target = st.selectbox(\n                                    "Buộc đăng xuất tài khoản đang hoạt động",\n                                    _revocable,\n                                    key="drive_force_logout_email",\n                                )\n                                if st.button(\n                                    "🔒 Kết thúc phiên đăng nhập",\n                                    key="drive_force_logout_btn",\n                                    type="secondary",\n                                ):\n                                    gw.force_logout(token, _session_target)\n                                    st.success(f"Đã kết thúc phiên đăng nhập của {_session_target}.")\n                                    st.rerun()\n                    except Exception as exc:\n                        st.warning(\n                            "Backend đăng nhập chưa hỗ trợ quản lý phiên đơn hoặc chưa được deploy bản mới: " + str(exc)\n                        )\n\n                else:\n                    st.info("Chỉ Admin mới được quản lý tài khoản và phân quyền.")\n'''
    source = _replace_once(source, admin_else, session_ui, "admin active-session panel")

    if "_session_error = str(st.session_state.get(\"qlda_drive_error\"" not in source:
        raise RuntimeError(f"{PATCH_MARKER}: login error UI insertion missing")
    return source
