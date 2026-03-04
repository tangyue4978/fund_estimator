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


def clear_cloud_error(scope: str) -> None:
    key = str(scope or "").strip()
    if not key:
        return
    _state_dict().pop(key, None)


def get_cloud_error(scope: str) -> str:
    key = str(scope or "").strip()
    if not key:
        return ""
    return str(_state_dict().get(key, "") or "").strip()
