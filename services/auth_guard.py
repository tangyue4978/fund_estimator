from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta

import streamlit as st

from config import settings
from services.auth_service import DEFAULT_DEVELOPER, login_user, register_user
from services.collector_service import ensure_collector_running
from storage import paths
from storage.json_store import update_json


_AUTH_SESSION_KEY = "sid"


def _persist_login_enabled() -> bool:
    return bool(getattr(settings, "AUTH_PERSIST_LOGIN_ENABLED", True))


def _auth_session_ttl_days() -> int:
    try:
        days = int(getattr(settings, "AUTH_SESSION_DAYS", 14))
    except Exception:
        days = 14
    return max(1, days)


def _now() -> datetime:
    return datetime.now()


def _to_iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _parse_iso(value: str) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except Exception:
        return None


def _hash_session_token(token: str) -> str:
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


def _read_sid_from_query() -> str:
    if not _persist_login_enabled():
        return ""
    try:
        sid = st.query_params.get(_AUTH_SESSION_KEY, "")
        if isinstance(sid, list):
            sid = sid[0] if sid else ""
        return str(sid or "").strip()
    except Exception:
        return ""


def _write_sid_to_query(sid: str) -> None:
    if not _persist_login_enabled():
        return
    token = str(sid or "").strip()
    if not token:
        return
    try:
        st.query_params[_AUTH_SESSION_KEY] = token
    except Exception:
        pass


def _drop_sid_query() -> None:
    try:
        if _AUTH_SESSION_KEY in st.query_params:
            del st.query_params[_AUTH_SESSION_KEY]
    except Exception:
        pass


def _issue_auth_session(phone: str, user_id: str) -> str:
    token = secrets.token_urlsafe(32)
    token_hash = _hash_session_token(token)
    now = _now()
    expires_at = now + timedelta(days=_auth_session_ttl_days())

    def updater(data: dict) -> dict:
        items = data.get("items", [])
        if not isinstance(items, list):
            items = []
        kept: list[dict] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            exp = _parse_iso(str(item.get("expires_at", "")))
            if exp is None or exp <= now:
                continue
            kept.append(item)
        kept.append(
            {
                "token_hash": token_hash,
                "user_id": str(user_id or ""),
                "phone": str(phone or ""),
                "created_at": _to_iso(now),
                "expires_at": _to_iso(expires_at),
            }
        )
        # Keep latest sessions only to avoid unbounded file growth.
        data["items"] = kept[-1000:]
        return data

    update_json(paths.file_auth_sessions(), updater)
    return token


def _resolve_auth_session(token: str) -> tuple[str, str]:
    raw_token = str(token or "").strip()
    if not raw_token:
        return "", ""

    target_hash = _hash_session_token(raw_token)
    now = _now()
    matched_uid = ""
    matched_phone = ""

    def updater(data: dict) -> dict:
        nonlocal matched_uid, matched_phone
        items = data.get("items", [])
        if not isinstance(items, list):
            items = []
        kept: list[dict] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            exp = _parse_iso(str(item.get("expires_at", "")))
            if exp is None or exp <= now:
                continue
            if str(item.get("token_hash", "")) == target_hash:
                matched_uid = str(item.get("user_id", "")).strip()
                matched_phone = str(item.get("phone", "")).strip()
            kept.append(item)
        data["items"] = kept
        return data

    try:
        update_json(paths.file_auth_sessions(), updater)
    except Exception:
        # Fail safe: do not trust token when storage is unavailable.
        return "", ""

    return matched_uid, matched_phone


def _revoke_auth_session(token: str) -> None:
    raw_token = str(token or "").strip()
    if not raw_token:
        return

    target_hash = _hash_session_token(raw_token)

    def updater(data: dict) -> dict:
        items = data.get("items", [])
        if not isinstance(items, list):
            data["items"] = []
            return data
        data["items"] = [
            item
            for item in items
            if isinstance(item, dict) and str(item.get("token_hash", "")) != target_hash
        ]
        return data

    try:
        update_json(paths.file_auth_sessions(), updater)
    except Exception:
        pass


def _restore_login_from_sid() -> tuple[str, str, str]:
    sid = _read_sid_from_query()
    if not sid:
        return "", "", ""

    uid, phone = _resolve_auth_session(sid)
    if not uid:
        _drop_sid_query()
        return "", "", ""

    return uid, phone, sid


def _query_auth_enabled() -> bool:
    return bool(getattr(settings, "AUTH_QUERY_LOGIN_ENABLED", False))


def _read_auth_from_query() -> tuple[str, str]:
    if not _query_auth_enabled():
        return "", ""
    try:
        qp = st.query_params
        uid = qp.get("uid", "")
        phone = qp.get("phone", "")
        if isinstance(uid, list):
            uid = uid[0] if uid else ""
        if isinstance(phone, list):
            phone = phone[0] if phone else ""
        return str(uid or "").strip(), str(phone or "").strip()
    except Exception:
        return "", ""


def _write_auth_to_query(phone: str, user_id: str) -> None:
    if not _query_auth_enabled():
        return

    uid = str(user_id or "").strip()
    ph = str(phone or "").strip()
    if not uid:
        return

    try:
        st.query_params["uid"] = uid
        if ph:
            st.query_params["phone"] = ph
    except Exception:
        pass


def _clear_auth_query() -> None:
    try:
        if "uid" in st.query_params:
            del st.query_params["uid"]
        if "phone" in st.query_params:
            del st.query_params["phone"]
    except Exception:
        pass


def _set_login_state(phone: str, user_id: str, sid: str = "") -> None:
    st.session_state["auth_logged_in"] = True
    st.session_state["auth_phone"] = str(phone)
    st.session_state["auth_user_id"] = str(user_id)
    st.session_state["auth_sid"] = str(sid or "")
    st.session_state["fund_estimator_user_id"] = str(user_id)
    paths.set_active_user(str(user_id))
    _write_auth_to_query(phone, user_id)
    if sid:
        _write_sid_to_query(sid)
    try:
        ensure_collector_running()
    except Exception:
        pass


def _is_logged_in() -> bool:
    logged = bool(st.session_state.get("auth_logged_in"))
    uid = str(st.session_state.get("auth_user_id", "")).strip()
    if logged and uid:
        sid = str(st.session_state.get("auth_sid", "")).strip()
        if sid:
            _write_sid_to_query(sid)
        paths.set_active_user(uid)
        st.session_state["fund_estimator_user_id"] = uid
        try:
            ensure_collector_running()
        except Exception:
            pass
        return True

    sid_uid, sid_phone, sid_token = _restore_login_from_sid()
    if sid_uid:
        _set_login_state(sid_phone, sid_uid, sid=sid_token)
        return True

    # Optional compatibility mode for old deployments.
    uid_q, phone_q = _read_auth_from_query()
    if uid_q:
        sid = _issue_auth_session(phone_q, uid_q) if _persist_login_enabled() else ""
        _set_login_state(phone_q, uid_q, sid=sid)
        return True

    return False


def logout() -> None:
    sid = str(st.session_state.get("auth_sid", "")).strip() or _read_sid_from_query()
    _revoke_auth_session(sid)
    for k in ("auth_logged_in", "auth_phone", "auth_user_id", "auth_sid", "fund_estimator_user_id"):
        st.session_state.pop(k, None)
    paths.set_active_user("public")
    _drop_sid_query()
    _clear_auth_query()


def require_login() -> str:
    if _is_logged_in():
        phone = str(st.session_state.get("auth_phone", ""))
        with st.sidebar:
            st.caption(f"已登录：{phone}")
            if st.button("退出登录", key="logout_btn"):
                logout()
                st.rerun()
        return str(st.session_state.get("auth_user_id"))

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
                sid = _issue_auth_session(phone, user_id) if _persist_login_enabled() else ""
                _set_login_state(phone, user_id, sid=sid)
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
                    sid = _issue_auth_session(phone, user_id) if _persist_login_enabled() else ""
                    _set_login_state(phone, user_id, sid=sid)
                    st.success("注册并登录成功")
                    st.rerun()
                st.error(msg)

    st.stop()
