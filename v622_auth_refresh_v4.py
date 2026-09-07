from __future__ import annotations


PATCH_MARKER = "# V6.22 AUTH REFRESH V5"


def patch_auth_refresh_v4(source: str) -> str:
    """Persist the signed QLDA session across a real browser F5/Refresh.

    V5 fixes the remaining Community Cloud failure mode from V4:

    * V4 wrote the token with a Streamlit component, but restored it only from
      ``st.context.cookies``. If the browser stored the component cookie in the
      component/partitioned cookie jar, a real F5 created a new Streamlit session
      and the server could not see the token.
    * V5 reads the token from both the native request-cookie snapshot and a
      browser-side CookieController. A second partitioned cookie is also written
      on HTTPS as a fallback for browsers that isolate iframe/component cookies.
    * Cookie persistence is verified before the current Streamlit session marks
      the token as written. Failed writes are retried a small bounded number of
      times instead of being treated as successful immediately.

    The backend still validates every restored token through the existing Drive
    Gateway ``me`` call, so browser persistence does not bypass account/role
    validation.
    """
    if PATCH_MARKER in source:
        return source

    required = (
        '_QLDA_AUTH_COOKIE = "qlda_auth_session_v618"',
        "def _browser_session_cookie() -> str:",
        "def _write_browser_session_cookie(token: str) -> None:",
        "def _clear_browser_session_cookie() -> None:",
        "def _gateway_session_token() -> str:",
        "def _gateway_logout() -> None:",
        "_write_browser_session_cookie(token)\n                st.rerun()",
        "_gateway_logout()\n            st.rerun()",
        "_qlda_cookie_written_for_token",
        "live_token = _gateway_session_token()",
    )
    missing = [item for item in required if item not in source]
    if missing:
        raise RuntimeError(f"V6.22 auth refresh V5 anchors missing: {missing}")

    # Replace the complete browser-cookie reader/writer and token restore block.
    # Keeping the reader and writer components on different keys avoids duplicate
    # element-key errors on a login run that both probes and writes a cookie.
    start = source.index("def _browser_session_cookie() -> str:")
    end = source.index("\ndef _gateway_logout() -> None:", start)

    replacement = r'''# V6.22 AUTH REFRESH V5
_QLDA_AUTH_COOKIE_FALLBACK = "qlda_auth_session_v622_partitioned"
_QLDA_AUTH_COOKIE_READER_KEY = "qlda_auth_cookie_reader_v622_v5"
_QLDA_AUTH_COOKIE_WRITER_KEY = "qlda_auth_cookie_writer_v622_v5"


def _qlda_native_cookie_v5(name: str) -> str:
    """Read a cookie attached to the current top-level Streamlit request."""
    try:
        cookies = st.context.cookies
        return str(cookies.get(str(name), "") or "").strip()
    except Exception:
        return ""


def _qlda_cookie_reader_v5():
    """Create a per-run browser reader component.

    On a fresh F5 the first component call can return its default value and then
    trigger one Streamlit rerun when the browser cookie snapshot arrives. On that
    rerun the keyed component value is available and authentication is restored.
    """
    from streamlit_cookies_controller import CookieController as _QLDACookieController
    return _QLDACookieController(key=_QLDA_AUTH_COOKIE_READER_KEY)


def _qlda_cookie_writer_v5():
    from streamlit_cookies_controller import CookieController as _QLDACookieController
    return _QLDACookieController(key=_QLDA_AUTH_COOKIE_WRITER_KEY)


def _qlda_cookie_secure_v5() -> bool:
    try:
        return str(st.context.url or "").strip().lower().startswith("https://")
    except Exception:
        return False


def _browser_session_cookie() -> str:
    # Fast path: a normal host cookie is visible on the initial F5 request.
    for _name in (_QLDA_AUTH_COOKIE, _QLDA_AUTH_COOKIE_FALLBACK):
        _token = _qlda_native_cookie_v5(_name)
        if _token:
            return _token

    # Community Cloud/browser fallback: ask the browser component directly.
    # This also covers partitioned component cookies that st.context may not see.
    try:
        _controller = _qlda_cookie_reader_v5()
        for _name in (_QLDA_AUTH_COOKIE, _QLDA_AUTH_COOKIE_FALLBACK):
            _token = str(_controller.get(_name) or "").strip()
            if _token:
                return _token
    except Exception:
        pass
    return ""


def _write_browser_session_cookie(token: str) -> None:
    value = str(token or "").strip()
    if not value:
        return

    # Do not create another writer when the top-level request already carries the
    # exact cookie. This is the common path after a successful F5 restore.
    if _qlda_native_cookie_v5(_QLDA_AUTH_COOKIE) == value:
        return

    try:
        controller = _qlda_cookie_writer_v5()
        controller.set(
            _QLDA_AUTH_COOKIE,
            value,
            path="/",
            max_age=float(_QLDA_AUTH_COOKIE_MAX_AGE),
            secure=_qlda_cookie_secure_v5(),
            same_site="lax",
        )

        # Chromium and some embedded/component contexts may isolate third-party
        # iframe cookies. Keep a second CHIPS/partitioned copy on HTTPS; the V5
        # reader checks both names. Browsers without CHIPS simply ignore the
        # partitioned attribute while the normal host cookie remains available.
        if _qlda_cookie_secure_v5():
            controller.set(
                _QLDA_AUTH_COOKIE_FALLBACK,
                value,
                path="/",
                max_age=float(_QLDA_AUTH_COOKIE_MAX_AGE),
                secure=True,
                same_site="none",
                partitioned=True,
            )
        return
    except Exception:
        # Fail open to the historical inline writer. Never blank the app merely
        # because a third-party cookie component failed to load.
        pass

    js_value = json.dumps(value)
    secure = "; Secure" if _qlda_cookie_secure_v5() else ""
    components.html(
        f"""<script>
        (function() {{
          const value = {js_value};
          document.cookie = "{_QLDA_AUTH_COOKIE}=" + value + "; Path=/; Max-Age={_QLDA_AUTH_COOKIE_MAX_AGE}; SameSite=Lax{secure}";
        }})();
        </script>""",
        height=0,
    )


def _clear_browser_session_cookie() -> None:
    # Use set(..., max_age=0) instead of CookieController.remove(). Version 0.0.4
    # can raise KeyError when its local cookie cache does not contain the name.
    try:
        controller = _qlda_cookie_writer_v5()
        controller.set(
            _QLDA_AUTH_COOKIE,
            "",
            path="/",
            max_age=0.0,
            secure=_qlda_cookie_secure_v5(),
            same_site="lax",
        )
        if _qlda_cookie_secure_v5():
            controller.set(
                _QLDA_AUTH_COOKIE_FALLBACK,
                "",
                path="/",
                max_age=0.0,
                secure=True,
                same_site="none",
                partitioned=True,
            )
    except Exception:
        pass

    secure = "; Secure" if _qlda_cookie_secure_v5() else ""
    components.html(
        f"""<script>
        document.cookie = "{_QLDA_AUTH_COOKIE}=; Path=/; Max-Age=0; SameSite=Lax{secure}";
        document.cookie = "{_QLDA_AUTH_COOKIE_FALLBACK}=; Path=/; Max-Age=0; SameSite=Lax{secure}";
        </script>""",
        height=0,
    )


def _gateway_session_token() -> str:
    token = str(st.session_state.get("qlda_drive_session_token", "") or "").strip()
    if token:
        return token
    if bool(st.session_state.get("qlda_ignore_persistent_auth", False)):
        return ""
    token = _browser_session_cookie()
    if token:
        st.session_state["qlda_drive_session_token"] = token
        st.session_state["qlda_auth_restored_from_cookie"] = True
        # Avoid invoking the keyed reader component a second time in this same run.
        st.session_state["_qlda_browser_cookie_seen_v5"] = token
    return token
'''
    source = source[:start] + replacement + source[end:]

    # Logout must forget all V5 verification state as well as the auth token.
    old_logout_keys = (
        'for key in ("qlda_drive_session_token", "qlda_drive_identity", "qlda_drive_error", '
        '"qlda_auth_restored_from_cookie", "_qlda_cookie_written_for_token"):'
    )
    new_logout_keys = (
        'for key in ("qlda_drive_session_token", "qlda_drive_identity", "qlda_drive_error", '
        '"qlda_auth_restored_from_cookie", "_qlda_cookie_written_for_token", '
        '"_qlda_browser_cookie_seen_v5", "_qlda_cookie_write_attempts_v5"):'
    )
    if old_logout_keys not in source:
        raise RuntimeError("V6.22 auth refresh V5 logout-state anchor missing")
    source = source.replace(old_logout_keys, new_logout_keys, 1)

    # Do not mark a browser write successful just because the Python component
    # call returned. Verify by reading it back; retry at most three times.
    old_live = '''    live_token = _gateway_session_token()\n    if live_token:\n        cookie_marker = str(st.session_state.get("_qlda_cookie_written_for_token") or "")\n        token_marker = str(hash(live_token))\n        if cookie_marker != token_marker:\n            _write_browser_session_cookie(live_token)\n            st.session_state["_qlda_cookie_written_for_token"] = token_marker\n        if st.session_state.pop("qlda_auth_restored_from_cookie", False):\n            st.toast("Đã khôi phục phiên đăng nhập sau khi refresh.", icon="🔐")\n'''
    new_live = '''    live_token = _gateway_session_token()\n    if live_token:\n        cookie_marker = str(st.session_state.get("_qlda_cookie_written_for_token") or "")\n        token_marker = str(hash(live_token))\n        if cookie_marker != token_marker:\n            browser_seen = str(st.session_state.get("_qlda_browser_cookie_seen_v5") or "").strip()\n            if not browser_seen:\n                browser_seen = _browser_session_cookie()\n                if browser_seen:\n                    st.session_state["_qlda_browser_cookie_seen_v5"] = browser_seen\n            if browser_seen == live_token:\n                st.session_state["_qlda_cookie_written_for_token"] = token_marker\n                st.session_state["_qlda_cookie_write_attempts_v5"] = 0\n            else:\n                attempts = int(st.session_state.get("_qlda_cookie_write_attempts_v5", 0) or 0)\n                if attempts < 3:\n                    _write_browser_session_cookie(live_token)\n                    st.session_state["_qlda_cookie_write_attempts_v5"] = attempts + 1\n                    time.sleep(0.75)\n                    st.rerun()\n        if st.session_state.pop("qlda_auth_restored_from_cookie", False):\n            st.toast("Đã khôi phục phiên đăng nhập sau khi refresh.", icon="🔐")\n'''
    if old_live not in source:
        raise RuntimeError("V6.22 auth refresh V5 verification anchor missing")
    source = source.replace(old_live, new_live, 1)

    # Give the browser enough time to process the login/logout cookie component
    # before the explicit rerun tears down the current script run.
    source = source.replace(
        "_write_browser_session_cookie(token)\n                st.rerun()",
        "_write_browser_session_cookie(token)\n                time.sleep(1.00)\n                st.rerun()",
        1,
    )
    source = source.replace(
        "_gateway_logout()\n            st.rerun()",
        "_gateway_logout()\n            time.sleep(0.75)\n            st.rerun()",
        1,
    )

    return source
