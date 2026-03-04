from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path


_USER_ID_ENV = "FUND_ESTIMATOR_USER_ID"
_DEFAULT_USER_ID = "public"


def _is_streamlit_cloud() -> bool:
    return bool(os.getenv("STREAMLIT_SHARING_MODE", "").strip())


def bundle_root() -> Path:
    return Path(__file__).resolve().parent.parent


def project_root() -> Path:
    return bundle_root()


def _data_root() -> Path:
    if _is_streamlit_cloud():
        return Path.home() / ".fund_estimator" / "data"
    return project_root() / "data"


def _sanitize_user_id(user_id: str | None) -> str:
    raw = str(user_id or "").strip().lower()
    cleaned = re.sub(r"[^a-z0-9_.-]+", "_", raw).strip("._-")
    return cleaned or _DEFAULT_USER_ID


def set_active_user(user_id: str | None) -> str:
    uid = _sanitize_user_id(user_id)
    try:
        if "streamlit" in sys.modules:
            from streamlit.runtime.scriptrunner import get_script_run_ctx  # type: ignore

            if get_script_run_ctx() is not None:
                import streamlit as st  # type: ignore

                st.session_state["fund_estimator_user_id"] = uid
                return uid
    except Exception:
        pass

    os.environ[_USER_ID_ENV] = uid
    return uid


def current_user_id() -> str:
    try:
        if "streamlit" in sys.modules:
            from streamlit.runtime.scriptrunner import get_script_run_ctx  # type: ignore

            if get_script_run_ctx() is not None:
                import streamlit as st  # type: ignore

                session_uid = st.session_state.get("fund_estimator_user_id", "")
                if session_uid:
                    return _sanitize_user_id(str(session_uid))
                return _DEFAULT_USER_ID
    except Exception:
        pass

    env_uid = os.getenv(_USER_ID_ENV, "").strip()
    if env_uid:
        return _sanitize_user_id(env_uid)
    return _DEFAULT_USER_ID


def data_dir() -> Path:
    return _data_root()


def ensure_data_dir() -> Path:
    d = data_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def user_data_dir(user_id: str | None = None) -> Path:
    uid = _sanitize_user_id(user_id) if user_id is not None else current_user_id()
    d = data_dir() / "users" / uid
    d.mkdir(parents=True, exist_ok=True)
    return d


def _user_file_with_legacy_seed(filename: str) -> Path:
    user_path = user_data_dir() / filename
    legacy_path = data_dir() / filename
    if (not user_path.exists()) and legacy_path.exists():
        try:
            shutil.copy2(legacy_path, user_path)
        except Exception:
            pass
    return user_path


def _safe_filename(key: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(key or "").strip())
    return s or "cache"


def file_watchlist() -> str:
    return str(_user_file_with_legacy_seed("watchlist.json"))


def file_portfolio() -> str:
    return str(_user_file_with_legacy_seed("portfolio.json"))


def file_adjustments() -> str:
    return str(_user_file_with_legacy_seed("adjustments.json"))


def file_daily_ledger() -> str:
    return str(_user_file_with_legacy_seed("daily_ledger.json"))


def file_intraday(date_str: str) -> str:
    d = user_data_dir() / "intraday"
    d.mkdir(parents=True, exist_ok=True)
    return str(d / f"{date_str}.json")


def file_intraday_fund(date_str: str, code: str) -> str:
    d = user_data_dir() / "intraday" / date_str
    d.mkdir(parents=True, exist_ok=True)
    return str(d / f"{code}.json")


def file_fund_cache() -> str:
    ensure_data_dir()
    return str(data_dir() / "fund_cache.json")


def file_http_cache(cache_key: str) -> Path:
    d = data_dir() / "http_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{_safe_filename(cache_key)}.json"


def file_raw_snapshot(key: str) -> Path:
    d = data_dir() / "raw"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{_safe_filename(key)}.txt"


def file_fund_holdings_map() -> str:
    ensure_data_dir()
    return str(data_dir() / "fund_holdings_map.json")


def file_stock_quote_map() -> str:
    ensure_data_dir()
    return str(data_dir() / "stock_quote_map.json")


def file_auth_users() -> str:
    d = data_dir() / "auth"
    d.mkdir(parents=True, exist_ok=True)
    return str(d / "users.json")


def file_auth_sessions() -> str:
    d = data_dir() / "auth"
    d.mkdir(parents=True, exist_ok=True)
    return str(d / "sessions.json")


def ensure_dirs() -> None:
    ensure_data_dir()
    (data_dir() / "intraday").mkdir(parents=True, exist_ok=True)
    (data_dir() / "http_cache").mkdir(parents=True, exist_ok=True)
    (data_dir() / "raw").mkdir(parents=True, exist_ok=True)
