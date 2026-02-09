from __future__ import annotations

import streamlit as st

from config import settings
from services.auth_service import DEFAULT_DEVELOPER, login_user, register_user
from services.collector_service import ensure_collector_running
from storage import paths


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


def _set_login_state(phone: str, user_id: str) -> None:
    st.session_state["auth_logged_in"] = True
    st.session_state["auth_phone"] = str(phone)
    st.session_state["auth_user_id"] = str(user_id)
    st.session_state["fund_estimator_user_id"] = str(user_id)
    paths.set_active_user(str(user_id))
    _write_auth_to_query(phone, user_id)
    try:
        ensure_collector_running()
    except Exception:
        pass


def _is_logged_in() -> bool:
    logged = bool(st.session_state.get("auth_logged_in"))
    uid = str(st.session_state.get("auth_user_id", "")).strip()
    if logged and uid:
        paths.set_active_user(uid)
        st.session_state["fund_estimator_user_id"] = uid
        try:
            ensure_collector_running()
        except Exception:
            pass
        return True

    # Optional compatibility mode for old deployments.
    uid_q, phone_q = _read_auth_from_query()
    if uid_q:
        _set_login_state(phone_q, uid_q)
        return True
    return False


def logout() -> None:
    for k in ("auth_logged_in", "auth_phone", "auth_user_id", "fund_estimator_user_id"):
        st.session_state.pop(k, None)
    paths.set_active_user("public")
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
