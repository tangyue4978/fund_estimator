from __future__ import annotations

from typing import Dict


_STATE_KEY = "_fund_estimator_cloud_errors"
_FALLBACK_ERRORS: Dict[str, str] = {}


def _state_dict() -> Dict[str, str]:
    try:
        import streamlit as st  # type: ignore
        from streamlit.runtime.scriptrunner import get_script_run_ctx  # type: ignore

        if get_script_run_ctx() is not None:
            data = st.session_state.get(_STATE_KEY)
            if not isinstance(data, dict):
                data = {}
                st.session_state[_STATE_KEY] = data
            return data
    except Exception:
        pass
    return _FALLBACK_ERRORS


def set_cloud_error(scope: str, error: Exception | str) -> None:
    key = str(scope or "").strip()
    if not key:
        return
    msg = str(error or "").strip() or "unknown cloud error"
    _state_dict()[key] = msg


def _classify_error(msg: str) -> str:
    text = str(msg or "").strip()
    lower = text.lower()
    if not text:
        return ""
    if "cloud storage is not configured" in lower or "supabase" in lower and "not configured" in lower:
        return "云端未配置：请检查 SUPABASE_URL 和 SUPABASE_KEY。"
    if "401" in lower or "403" in lower or "unauthorized" in lower or "forbidden" in lower or "permission" in lower:
        return "云端权限异常：请检查 Supabase Key、RLS 策略或当前用户权限。"
    if "404" in lower or "relation" in lower or "column" in lower or "schema" in lower:
        return "云端表结构异常：请检查 Supabase 表、字段或迁移是否完整。"
    if (
        "timeout" in lower
        or "connection" in lower
        or "network" in lower
        or "failed to establish" in lower
        or "max retries" in lower
        or "10013" in lower
    ):
        return "云端连接异常：请检查网络、代理、防火墙或 Supabase 服务状态。"
    if "429" in lower or "too many" in lower or "rate" in lower:
        return "云端请求过于频繁：请稍后重试或降低刷新频率。"
    return "云端读写异常：请稍后重试；若持续出现，请检查云端配置和日志。"


def clear_cloud_error(scope: str) -> None:
    key = str(scope or "").strip()
    if not key:
        return
    _state_dict().pop(key, None)


def get_cloud_error(scope: str) -> str:
    key = str(scope or "").strip()
    if not key:
        return ""
    raw = str(_state_dict().get(key, "") or "").strip()
    if not raw:
        return ""
    summary = _classify_error(raw)
    # UI pages and downloadable diagnostics must not expose raw request URLs,
    # credentials, schema details or user identifiers.
    return summary or "云端读写异常：请稍后重试。"
