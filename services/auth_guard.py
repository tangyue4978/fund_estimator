from __future__ import annotations

import json
from datetime import datetime, timedelta
import uuid

import streamlit as st
import streamlit.components.v1 as components

from config import settings
from services.auth_service import DEFAULT_DEVELOPER, login_user, register_user
from storage import paths
from storage.json_store import ensure_json_file_with_schema, update_json


_AUTH_COOKIE_KEY = "fund_estimator_sid"


def _now() -> datetime:
    return datetime.now()


def _sessions_path() -> str:
    return paths.file_auth_sessions()


def _sessions_schema() -> dict:
    return {"sessions": {}}


def _session_ttl_days() -> int:
    raw = getattr(settings, "AUTH_SESSION_DAYS", 14)
    try:
        return max(1, int(raw))
    except Exception:
        return 14


def _cookie_max_age_sec() -> int:
    return _session_ttl_days() * 24 * 60 * 60


def _cookie_secure_attr() -> str:
    try:
        url = str(getattr(st.context, "url", "") or "").strip().lower()
    except Exception:
        url = ""
    return "; Secure" if url.startswith("https://") else ""


def _clear_legacy_auth_query_params() -> None:
    try:
        qp = st.query_params
        changed = False
        for key in ("sid", "uid", "phone"):
            if key in qp:
                del qp[key]
                changed = True
        if changed:
            return
    except Exception:
        pass

    try:
        params = st.experimental_get_query_params()
        changed = False
        for key in ("sid", "uid", "phone"):
            if key in params:
                params.pop(key, None)
                changed = True
        if changed:
            st.experimental_set_query_params(**params)
    except Exception:
        pass


def _read_sid_from_cookie() -> str:
    try:
        cookies = st.context.cookies
        return str(cookies.get(_AUTH_COOKIE_KEY, "") or "").strip()
    except Exception:
        return ""


def _clear_expired_sessions() -> None:
    now_iso = _now().isoformat(timespec="seconds")

    def updater(data: dict) -> dict:
        sessions = data.get("sessions", {})
        if not isinstance(sessions, dict):
            data["sessions"] = {}
            return data
        data["sessions"] = {
            sid: row
            for sid, row in sessions.items()
            if isinstance(row, dict) and str(row.get("expires_at", "")).strip() > now_iso
        }
        return data

    update_json(_sessions_path(), updater)


def _persist_login_session(phone: str, user_id: str) -> str:
    _clear_expired_sessions()
    sid = uuid.uuid4().hex
    now = _now()
    payload = {
        "phone": str(phone),
        "user_id": str(user_id),
        "created_at": now.isoformat(timespec="seconds"),
        "updated_at": now.isoformat(timespec="seconds"),
        "expires_at": (now + timedelta(days=_session_ttl_days())).isoformat(timespec="seconds"),
    }

    def updater(data: dict) -> dict:
        sessions = data.get("sessions", {})
        if not isinstance(sessions, dict):
            sessions = {}
        sessions[sid] = payload
        data["sessions"] = sessions
        return data

    update_json(_sessions_path(), updater)
    return sid


def _drop_persistent_session() -> None:
    sid = str(st.session_state.get("auth_session_id") or _read_sid_from_cookie()).strip()
    if not sid:
        return

    def updater(data: dict) -> dict:
        sessions = data.get("sessions", {})
        if isinstance(sessions, dict):
            sessions.pop(sid, None)
            data["sessions"] = sessions
        return data

    update_json(_sessions_path(), updater)


def _queue_cookie_sync(action: str, sid: str = "") -> None:
    st.session_state["auth_cookie_action"] = action
    if sid:
        st.session_state["auth_cookie_value"] = sid
    else:
        st.session_state.pop("auth_cookie_value", None)


def _render_cookie_sync() -> None:
    action = str(st.session_state.get("auth_cookie_action", "") or "").strip().lower()
    sid = str(st.session_state.get("auth_cookie_value", "") or "").strip()
    if action not in {"set", "clear"}:
        return

    if action == "set" and sid:
        cookie_stmt = (
            f'document.cookie = "{_AUTH_COOKIE_KEY}=" + encodeURIComponent({json.dumps(sid)}) + '
            f'"; path=/; max-age={_cookie_max_age_sec()}; SameSite=Lax{_cookie_secure_attr()}";'
        )
    else:
        cookie_stmt = (
            f'document.cookie = "{_AUTH_COOKIE_KEY}=; path=/; expires=Thu, 01 Jan 1970 00:00:00 GMT; '
            f'SameSite=Lax{_cookie_secure_attr()}";'
        )

    components.html(
        f"""
<script>
{cookie_stmt}
</script>
""",
        height=0,
        width=0,
    )
    st.session_state.pop("auth_cookie_action", None)
    st.session_state.pop("auth_cookie_value", None)


def _set_login_state(phone: str, user_id: str, *, persist: bool = True, sid: str = "") -> None:
    st.session_state["auth_logged_in"] = True
    st.session_state["auth_phone"] = str(phone)
    st.session_state["auth_user_id"] = str(user_id)
    st.session_state["fund_estimator_user_id"] = str(user_id)
    paths.set_active_user(str(user_id))

    auth_sid = sid.strip()
    if persist and bool(getattr(settings, "AUTH_PERSIST_LOGIN_ENABLED", True)):
        auth_sid = _persist_login_session(phone, user_id)
    if auth_sid:
        st.session_state["auth_session_id"] = auth_sid
        _queue_cookie_sync("set", auth_sid)


def _restore_login_from_session() -> bool:
    if not bool(getattr(settings, "AUTH_PERSIST_LOGIN_ENABLED", True)):
        return False

    sid = _read_sid_from_cookie()
    if not sid:
        return False

    data = ensure_json_file_with_schema(_sessions_path(), _sessions_schema())
    sessions = data.get("sessions", {})
    if not isinstance(sessions, dict):
        return False

    row = sessions.get(sid)
    now_iso = _now().isoformat(timespec="seconds")
    if not isinstance(row, dict) or str(row.get("expires_at", "")).strip() <= now_iso:
        _queue_cookie_sync("clear")
        _drop_persistent_session()
        return False

    def updater(data: dict) -> dict:
        cur = data.get("sessions", {}).get(sid)
        if isinstance(cur, dict):
            cur["updated_at"] = now_iso
            cur["expires_at"] = (_now() + timedelta(days=_session_ttl_days())).isoformat(timespec="seconds")
            data["sessions"][sid] = cur
        return data

    update_json(_sessions_path(), updater)
    _set_login_state(str(row.get("phone", "")), str(row.get("user_id", "")), persist=False, sid=sid)
    _queue_cookie_sync("set", sid)
    return True


def _is_logged_in() -> bool:
    logged = bool(st.session_state.get("auth_logged_in"))
    uid = str(st.session_state.get("auth_user_id", "")).strip()
    if logged and uid:
        paths.set_active_user(uid)
        st.session_state["fund_estimator_user_id"] = uid
        return True
    return False


def logout() -> None:
    _drop_persistent_session()
    for key in (
        "auth_logged_in",
        "auth_phone",
        "auth_user_id",
        "fund_estimator_user_id",
        "auth_session_id",
    ):
        st.session_state.pop(key, None)
    paths.set_active_user("public")
    _queue_cookie_sync("clear")
    _clear_legacy_auth_query_params()


def require_login() -> str:
    _clear_legacy_auth_query_params()
    _render_cookie_sync()
    if _is_logged_in():
        phone = str(st.session_state.get("auth_phone", ""))
        with st.sidebar:
            st.caption(f"已登录：{phone}")
            if st.button("退出登录", key="logout_btn"):
                logout()
                st.rerun()
        return str(st.session_state.get("auth_user_id"))

    ensure_json_file_with_schema(_sessions_path(), _sessions_schema())
    if _restore_login_from_session():
        st.rerun()
    _render_cookie_sync()

    st.title("欢迎使用 Fund Estimator")
    st.info("请先注册或登录后再使用系统功能。")

    tab_login, tab_register = st.tabs(["登录", "注册"])
    with tab_login:
        with st.form("login_form"):
            phone = st.text_input("手机号", placeholder="请输入手机号")
            password = st.text_input("密码", type="password")
            submitted = st.form_submit_button("登录")
        if submitted:
            ok, msg, user_id = login_user(phone, password)
            if ok and user_id:
                _set_login_state(phone, user_id)
                st.success("登录成功")
                st.rerun()
            st.error(msg)

    with tab_register:
        with st.form("register_form"):
            st.text_input("开发者", value=DEFAULT_DEVELOPER, disabled=True)
            phone = st.text_input("手机号（注册）", placeholder="请输入手机号")
            password = st.text_input("密码（至少6位）", type="password")
            password2 = st.text_input("确认密码", type="password")
            submitted = st.form_submit_button("注册")
        if submitted:
            if password != password2:
                st.error("两次密码不一致")
            else:
                ok, msg, user_id = register_user(phone, password)
                if ok and user_id:
                    _set_login_state(phone, user_id)
                    st.success("注册并登录成功")
                    st.rerun()
                st.error(msg)

    st.stop()
