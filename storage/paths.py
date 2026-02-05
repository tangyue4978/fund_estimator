# storage/paths.py
from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path


APP_NAME = "FundEstimator"
_USER_ID_ENV = "FUND_ESTIMATOR_USER_ID"
_DEFAULT_USER_ID = "public"
_SEEDED_FILES = (
    "fund_cache.json",
    "fund_profile_map.json",
    "fund_holdings_map.json",
    "stock_quote_map.json",
)


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def bundle_root() -> Path:
    """
    Source code/data root for imports and bundled assets.
    - dev: repository root
    - frozen: PyInstaller extraction directory (sys._MEIPASS) when available
    """
    if _is_frozen():
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(str(meipass)).resolve()
    return Path(__file__).resolve().parent.parent


def runtime_root() -> Path:
    """
    Writable app root.
    - FUND_ESTIMATOR_HOME has highest priority
    - Windows default: %LOCALAPPDATA%\\FundEstimator
    - Fallback: ~/.fund_estimator
    """
    custom = os.getenv("FUND_ESTIMATOR_HOME", "").strip()
    if custom:
        return Path(custom).expanduser().resolve()

    if os.name == "nt":
        base = os.getenv("LOCALAPPDATA") or os.getenv("APPDATA")
        if base:
            return Path(base).resolve() / APP_NAME

    return Path.home() / ".fund_estimator"


def _is_streamlit_cloud() -> bool:
    # Streamlit Community Cloud sets this variable in deployed runtime.
    return bool(os.getenv("STREAMLIT_SHARING_MODE", "").strip())


def _sanitize_user_id(user_id: str | None) -> str:
    raw = str(user_id or "").strip().lower()
    cleaned = re.sub(r"[^a-z0-9_.-]+", "_", raw).strip("._-")
    return cleaned or _DEFAULT_USER_ID


def set_active_user(user_id: str | None) -> str:
    uid = _sanitize_user_id(user_id)
    # Keep user context in current Streamlit session only.
    # Do not write process-global state (env/global variable), which can leak across sessions.
    try:
        if "streamlit" in sys.modules:
            from streamlit.runtime.scriptrunner import get_script_run_ctx  # type: ignore

            if get_script_run_ctx() is not None:
                import streamlit as st  # type: ignore

                st.session_state["fund_estimator_user_id"] = uid
                return uid
    except Exception:
        pass

    # Non-Streamlit scripts can still opt in via environment variable.
    os.environ[_USER_ID_ENV] = uid
    return uid


def current_user_id() -> str:
    # Streamlit runtime: strictly read from current session.
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

    # Non-Streamlit runtime fallback (CLI scripts/tests).
    env_uid = os.getenv(_USER_ID_ENV, "").strip()
    if env_uid:
        return _sanitize_user_id(env_uid)
    return _DEFAULT_USER_ID


def project_root() -> Path:
    # Backward-compatible alias for old call sites.
    return bundle_root()


def data_dir() -> Path:
    # Frozen binaries should not write into installation folders.
    if _is_frozen():
        return runtime_root() / "data"
    # In Streamlit Cloud, keep writable data under user home instead of repo path.
    if _is_streamlit_cloud():
        return runtime_root() / "data"
    return project_root() / "data"


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


def ensure_data_dir() -> Path:
    d = data_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_filename(key: str) -> str:
    s = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(key or "").strip())
    return s or "cache"


# ---------- common data files ----------
def file_watchlist() -> str:
    return str(_user_file_with_legacy_seed("watchlist.json"))


def file_portfolio() -> str:
    return str(_user_file_with_legacy_seed("portfolio.json"))


def file_adjustments() -> str:
    return str(_user_file_with_legacy_seed("adjustments.json"))


def file_daily_ledger() -> str:
    return str(_user_file_with_legacy_seed("daily_ledger.json"))


# ---------- intraday by date ----------
def file_intraday(date_str: str) -> str:
    d = user_data_dir() / "intraday"
    d.mkdir(parents=True, exist_ok=True)
    return str(d / f"{date_str}.json")


def file_intraday_fund(date_str: str, code: str) -> str:
    d = user_data_dir() / "intraday" / date_str
    d.mkdir(parents=True, exist_ok=True)
    return str(d / f"{code}.json")


# ---------- fund cache ----------
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


def status_dir() -> Path:
    d = runtime_root() / "status"
    d.mkdir(parents=True, exist_ok=True)
    return d


def logs_dir() -> Path:
    d = runtime_root() / "logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def file_collector_status() -> Path:
    return status_dir() / "collector_status.json"


def file_collector_log() -> Path:
    return logs_dir() / "collector.log"


def _seed_runtime_data() -> None:
    if not _is_frozen():
        return

    src_data = bundle_root() / "data"
    dst_data = data_dir()
    if not src_data.exists():
        return

    for name in _SEEDED_FILES:
        src = src_data / name
        dst = dst_data / name
        if src.exists() and not dst.exists():
            try:
                shutil.copy2(src, dst)
            except Exception:
                # Seeds are optional; ignore copy failures.
                pass


def ensure_dirs() -> None:
    ensure_data_dir()
    (data_dir() / "intraday").mkdir(parents=True, exist_ok=True)
    (data_dir() / "http_cache").mkdir(parents=True, exist_ok=True)
    (data_dir() / "raw").mkdir(parents=True, exist_ok=True)
    status_dir()
    _seed_runtime_data()
