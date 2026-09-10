from __future__ import annotations

import re


PATCH_MARKER = "V6.22 SINGLE SESSION UI V1"


def _replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"{PATCH_MARKER}: expected one {label}, found {count}")
    return source.replace(old, new, 1)


def patch_single_session(source: str) -> str:
    """Add single-login UX on top of the Drive/local auth gateway.

    Backend remains authoritative. The UI only surfaces active sessions and sends
    revoke/logout actions; a stolen/stale cookie cannot bypass requireSession.
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
    logout_new = '''def _gateway_logout(preserve_error: bool = False) -> None:\n    # V6.22 SINGLE SESSION UI V1\n    token = str(st.session_state.get("qlda_drive_session_token", "") or "").strip()\n    holder = st.session_state.get("_qlda_drive_gateway_instance")\n    saved_error = str(st.session_state.get("qlda_drive_error", "") or "") if preserve_error else ""\n    try:\n        gateway = None\n        if isinstance(holder, DriveGateway):\n            gateway = holder\n        elif isinstance(holder, tuple) and len(holder) == 2 and isinstance(holder[1], DriveGateway):\n            gateway = holder[1]\n        if gateway is not None and token:\n            gateway.logout(token)\n    except Exception:\n        # A superseded token is expected to fail server-side; local cleanup still runs.\n        pass\n    try:\n        if isinstance(holder, DriveGateway):\n            holder.close()\n        elif isinstance(holder, tuple) and len(holder) == 2:\n            holder[1].close()\n    except Exception:\n        pass\n    st.session_state.pop("_qlda_drive_gateway_instance", None)\n    for key in ("qlda_drive_session_token", "qlda_drive_identity", "qlda_drive_error", "qlda_auth_restored_from_cookie", "_qlda_cookie_written_for_token"):\n        st.session_state.pop(key, None)\n    if preserve_error and saved_error:\n        st.session_state["qlda_drive_error"] = saved_error\n    st.session_state["qlda_ignore_persistent_auth"] = True\n    _clear_browser_session_cookie()\n\n\ndef _cloud_identity(refresh: bool = False):'''
    source = source[: match.start()] + logout_new + source[match.end() :]

    cloud_error_old = '''    except Exception as exc:\n        st.session_state["qlda_drive_error"] = str(exc)\n        _gateway_logout()\n        return {"role": "unknown", "email": "", "name": "", "label": "Chưa đăng nhập"}\n'''
    cloud_error_new = '''    except Exception as exc:\n        st.session_state["qlda_drive_error"] = str(exc)\n        _gateway_logout(preserve_error=True)\n        return {"role": "unknown", "email": "", "name": "", "label": "Chưa đăng nhập"}\n'''
    source = _replace_once(source, cloud_error_old, cloud_error_new, "preserve superseded-session error")

    login_page_old = '''    if not _gateway_session_token():\n        st.title("🏗️ QLDA Xây dựng V6.22 PostgreSQL Cloud")\n'''
    login_page_new = '''    if not _gateway_session_token():\n        st.title("🏗️ QLDA Xây dựng V6.22 PostgreSQL Cloud")\n        _session_error = str(st.session_state.get("qlda_drive_error", "") or "").strip()\n        if _session_error:\n            st.warning(_session_error)\n'''
    source = _replace_once(source, login_page_old, login_page_new, "login session error")

    login_call_old = '''                result = gw.login(email, password)\n                token = str(result.get("session_token") or "")\n'''
    login_call_new = '''                try:\n                    _client_info = str(st.context.headers.get("User-Agent", "") or "")[:500]\n                except Exception:\n                    _client_info = ""\n                result = gw.login(email, password, client_info=_client_info)\n                token = str(result.get("session_token") or "")\n'''
    source = _replace_once(source, login_call_old, login_call_new, "login client metadata")

    login_success_old = '''                st.session_state["qlda_drive_session_token"] = token\n                st.session_state.pop("qlda_drive_identity", None)\n                st.session_state.pop("qlda_ignore_persistent_auth", None)\n'''
    login_success_new = '''                st.session_state["qlda_drive_session_token"] = token\n                st.session_state.pop("qlda_drive_identity", None)\n                st.session_state.pop("qlda_drive_error", None)\n                st.session_state.pop("qlda_ignore_persistent_auth", None)\n'''
    source = _replace_once(source, login_success_old, login_success_new, "clear session error after login")

    admin_else = '''                else:\n                    st.info("Chỉ Admin mới được quản lý tài khoản và phân quyền.")\n'''
    session_ui = '''                    st.markdown("### 🔐 Phiên đăng nhập")\n                    _ui_note(\n                        "Mỗi tài khoản chỉ có 01 phiên đang hoạt động. Đăng nhập ở thiết bị/trình duyệt khác sẽ "\n                        "tự kết thúc phiên cũ; F5 và nhiều tab dùng cùng phiên không bị ảnh hưởng."\n                    )\n                    try:\n                        _sessions = gw.list_sessions(token)\n                        if _sessions:\n                            _session_df = pd.DataFrame([{\n                                "Tên": x.get("name", ""),\n                                "Email": x.get("email", ""),\n                                "Quyền": {"read":"Chỉ đọc","update":"Cập nhật","admin":"Admin"}.get(x.get("role", ""), x.get("role", "")),\n                                "Đăng nhập": x.get("created_at", ""),\n                                "Hoạt động gần nhất": x.get("last_seen_at", ""),\n                                "Hết hạn": x.get("expires_at", ""),\n                                "Thiết bị / trình duyệt": x.get("client_info", ""),\n                            } for x in _sessions])\n                            st.dataframe(_session_df, hide_index=True, width="stretch")\n                            _current_session_email = str(ident.get("email") or "").strip().lower()\n                            _revocable = [\n                                str(x.get("email") or "") for x in _sessions\n                                if str(x.get("email") or "").strip().lower() != _current_session_email\n                            ]\n                            if _revocable:\n                                _session_target = st.selectbox(\n                                    "Buộc đăng xuất tài khoản",\n                                    _revocable,\n                                    key="drive_force_logout_email",\n                                )\n                                if st.button(\n                                    "🔒 Kết thúc phiên đăng nhập",\n                                    key="drive_force_logout_btn",\n                                    type="secondary",\n                                ):\n                                    gw.force_logout(token, _session_target)\n                                    st.success(f"Đã kết thúc phiên đăng nhập của {_session_target}.")\n                                    st.rerun()\n                        else:\n                            st.info("Chưa có phiên đăng nhập nào đang hoạt động.")\n                    except Exception as exc:\n                        st.warning(\n                            "Backend đăng nhập chưa hỗ trợ quản lý phiên đơn hoặc chưa được deploy bản mới: " + str(exc)\n                        )\n\n                else:\n                    st.info("Chỉ Admin mới được quản lý tài khoản và phân quyền.")\n'''
    source = _replace_once(source, admin_else, session_ui, "admin active-session panel")

    return source
