from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import uuid
from datetime import datetime, timedelta

import streamlit as st

from config import settings
from services.auth_service import DEFAULT_DEVELOPER, login_user, register_user
from storage import paths
from storage.json_store import ensure_json_file_with_schema, update_json


_AUTH_COOKIE_KEY = "fund_estimator_sid"


def _mask_phone(phone: str) -> str:
    raw = str(phone or "").strip()
    if re.fullmatch(r"\d{3}\*{4}\d{4}", raw):
        return raw
    digits = re.sub(r"\D+", "", raw)
    if len(digits) == 11:
        return f"{digits[:3]}****{digits[-4:]}"
    return "已认证用户"


def _now() -> datetime:
    return datetime.now()


def _sessions_path() -> str:
    return paths.file_auth_sessions()


def _sessions_schema() -> dict:
    return {"sessions": {}}


def _ensure_sessions_store() -> dict:
    data = ensure_json_file_with_schema(_sessions_path(), _sessions_schema())
    try:
        os.chmod(_sessions_path(), 0o600)
    except OSError:
        pass
    return data


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


def _auth_cookie_secret() -> str:
    value = os.getenv("AUTH_COOKIE_SECRET", "").strip()
    if value:
        return value
    try:
        value = st.secrets.get("AUTH_COOKIE_SECRET", "")
        return str(value or "").strip()
    except Exception:
        return ""


def _b64url_decode(raw: str) -> bytes:
    padded = raw + ("=" * (-len(raw) % 4))
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def _sign_payload(payload_b64: str, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256).hexdigest()


def _build_opaque_session_token(sid: str) -> str:
    sid = str(sid or "").strip()
    if not re.fullmatch(r"[a-f0-9]{32}", sid):
        return ""
    secret = _auth_cookie_secret()
    if not secret:
        return sid
    signed_value = f"v2.{sid}"
    sig = _sign_payload(signed_value, secret)
    return f"{signed_value}.{sig}"


def _extract_opaque_session_id(token: str) -> str:
    token = str(token or "").strip()
    if re.fullmatch(r"[a-f0-9]{32}", token):
        # Unsigned local-session IDs are supported only when no signing secret
        # is configured. Enabling the secret intentionally invalidates them.
        return "" if _auth_cookie_secret() else token
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != "v2":
        return ""
    _, sid, sig = parts
    if not re.fullmatch(r"[a-f0-9]{32}", sid):
        return ""
    secret = _auth_cookie_secret()
    if not secret:
        return ""
    expected = _sign_payload(f"v2.{sid}", secret)
    return sid if hmac.compare_digest(sig, expected) else ""


def _session_storage_key(token: str) -> str:
    sid = _extract_opaque_session_id(token)
    if not sid:
        return ""
    return hashlib.sha256(sid.encode("ascii")).hexdigest()


def _verify_signed_session(token: str) -> dict:
    """Verify a legacy self-contained v1 token so it can be rotated to v2."""
    token = str(token or "").strip()
    if not token.startswith("v1."):
        return {}
    secret = _auth_cookie_secret()
    if not secret:
        return {}
    parts = token.split(".", 2)
    if len(parts) != 3:
        return {}
    _, payload_b64, sig = parts
    expected = _sign_payload(payload_b64, secret)
    if not hmac.compare_digest(sig, expected):
        return {}
    try:
        payload = json.loads(_b64url_decode(payload_b64).decode("utf-8"))
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    try:
        exp = int(payload.get("exp", 0) or 0)
    except Exception:
        exp = 0
    if exp <= int(_now().timestamp()):
        return {}
    phone = str(payload.get("phone", "") or "").strip()
    user_id = str(payload.get("user_id", "") or "").strip()
    if not phone or not user_id:
        return {}
    return {"phone": phone, "user_id": user_id}


def _clear_legacy_auth_query_params() -> None:
    try:
        qp = st.query_params
        for key in ("sid", "uid", "phone"):
            if key in qp:
                del qp[key]
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
    try:
        os.chmod(_sessions_path(), 0o600)
    except OSError:
        pass


def _persist_login_session(phone: str, user_id: str) -> str:
    _clear_expired_sessions()
    sid = uuid.uuid4().hex
    token = _build_opaque_session_token(sid)
    storage_key = _session_storage_key(token)
    if not token or not storage_key:
        return ""
    now = _now()
    payload = {
        # Restoring a session only needs the owner ID. Keep a display-safe
        # phone value instead of persisting the complete login identifier.
        "phone_masked": _mask_phone(phone),
        "user_id": str(user_id),
        "created_at": now.isoformat(timespec="seconds"),
        "updated_at": now.isoformat(timespec="seconds"),
        "expires_at": (now + timedelta(days=_session_ttl_days())).isoformat(timespec="seconds"),
    }

    def updater(data: dict) -> dict:
        sessions = data.get("sessions", {})
        if not isinstance(sessions, dict):
            sessions = {}
        sessions[storage_key] = payload
        data["sessions"] = sessions
        return data

    update_json(_sessions_path(), updater)
    try:
        os.chmod(_sessions_path(), 0o600)
    except OSError:
        pass
    return token


def _drop_persistent_session() -> None:
    token = str(st.session_state.get("auth_session_id") or _read_sid_from_cookie()).strip()
    storage_key = _session_storage_key(token)
    sid = _extract_opaque_session_id(token)
    if not token or token.startswith("v1."):
        return

    def updater(data: dict) -> dict:
        sessions = data.get("sessions", {})
        if isinstance(sessions, dict):
            if storage_key:
                sessions.pop(storage_key, None)
            # Remove pre-v2 local-session keys during the compatibility window.
            if sid:
                sessions.pop(sid, None)
            sessions.pop(token, None)
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
            f'"; path=/; max-age={_cookie_max_age_sec()}; SameSite=Strict{_cookie_secure_attr()}";'
        )
    else:
        cookie_stmt = (
            f'document.cookie = "{_AUTH_COOKIE_KEY}=; path=/; expires=Thu, 01 Jan 1970 00:00:00 GMT; '
            f'SameSite=Strict{_cookie_secure_attr()}";'
        )

    st.iframe(
        f"""
<script>
{cookie_stmt}
</script>
""",
        height=1,
        width=1,
        tab_index=-1,
    )
    st.session_state.pop("auth_cookie_action", None)
    st.session_state.pop("auth_cookie_value", None)


def _set_login_state(phone: str, user_id: str, *, persist: bool = True, sid: str = "") -> None:
    # A Streamlit browser session can be reused after logout. Clear every
    # page/widget cache before attaching a user so data from the previous
    # account cannot leak into the newly authenticated session.
    st.session_state.clear()
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

    signed = _verify_signed_session(sid)
    if signed:
        phone = str(signed.get("phone", ""))
        user_id = str(signed.get("user_id", ""))
        # v1 exposed phone/user_id in a base64 payload. Successful validation
        # immediately rotates it to a server-side v2 session with an opaque SID.
        _set_login_state(phone, user_id, persist=True)
        return True

    storage_key = _session_storage_key(sid)
    opaque_sid = _extract_opaque_session_id(sid)
    if not storage_key:
        _queue_cookie_sync("clear")
        return False

    data = _ensure_sessions_store()
    sessions = data.get("sessions", {})
    if not isinstance(sessions, dict):
        return False

    row_key = storage_key
    row = sessions.get(row_key)
    if not isinstance(row, dict):
        # Compatibility with local sessions created before storage keys were
        # hashed. They are migrated on the first successful refresh.
        for legacy_key in (opaque_sid, sid):
            legacy_row = sessions.get(legacy_key)
            if isinstance(legacy_row, dict):
                row = legacy_row
                row_key = legacy_key
                break
    now_iso = _now().isoformat(timespec="seconds")
    if not isinstance(row, dict) or str(row.get("expires_at", "")).strip() <= now_iso:
        _queue_cookie_sync("clear")
        _drop_persistent_session()
        return False

    def updater(data: dict) -> dict:
        stored_sessions = data.get("sessions", {})
        if not isinstance(stored_sessions, dict):
            return data
        cur = stored_sessions.get(row_key)
        if isinstance(cur, dict):
            cur["updated_at"] = now_iso
            cur["expires_at"] = (_now() + timedelta(days=_session_ttl_days())).isoformat(timespec="seconds")
            stored_sessions.pop(row_key, None)
            stored_sessions[storage_key] = cur
            data["sessions"] = stored_sessions
        return data

    update_json(_sessions_path(), updater)
    display_phone = str(row.get("phone_masked") or row.get("phone") or "")
    _set_login_state(display_phone, str(row.get("user_id", "")), persist=False, sid=sid)
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
    # Page caches, imported images and widget values may contain private
    # portfolio data. Removing only the auth keys lets the next user in the
    # same browser session inherit that state.
    st.session_state.clear()
    # st.context.cookies reflects the browser's initial request and does not
    # change during Streamlit reruns. Block restoring that stale cookie until
    # this browser session authenticates again or starts a fresh connection.
    st.session_state["auth_cookie_restore_blocked"] = True
    paths.set_active_user("public")
    _queue_cookie_sync("clear")
    _clear_legacy_auth_query_params()


def require_login(*, render_sidebar: bool = True) -> str:
    _clear_legacy_auth_query_params()
    _render_cookie_sync()
    if _is_logged_in():
        phone = str(st.session_state.get("auth_phone", ""))
        if render_sidebar:
            with st.sidebar:
                st.caption(f"已登录：{_mask_phone(phone)}")
                if st.button("退出登录", key="logout_btn"):
                    logout()
                    st.rerun()
        return str(st.session_state.get("auth_user_id"))

    if bool(getattr(settings, "AUTH_PERSIST_LOGIN_ENABLED", True)):
        _ensure_sessions_store()
    restore_blocked = bool(st.session_state.get("auth_cookie_restore_blocked"))
    if not restore_blocked and _restore_login_from_session():
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
            password = st.text_input("密码（12 至 128 位）", type="password")
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
