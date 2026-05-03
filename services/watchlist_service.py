from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Dict, List

from services import supabase_client
from services.cloud_status_service import clear_cloud_error, set_cloud_error
from services.estimation_service import estimate_many
from services.fund_service import get_fund_profile
from storage import paths


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _current_user_id() -> str:
    return paths.current_user_id()


def _normalize_code(code: str) -> str:
    raw = str(code or "").strip()
    raw = re.sub(r"\s+", "", raw)
    return raw.upper()


def _is_valid_code(code: str) -> bool:
    if not code:
        return False
    return bool(re.fullmatch(r"[A-Z0-9._-]{3,20}", code))


def _normalize_items(items: list[Any]) -> List[str]:
    seen = set()
    out: List[str] = []
    for x in items:
        code = _normalize_code(str(x))
        if (not code) or (code in seen):
            continue
        seen.add(code)
        out.append(code)
    return out


def _cache_key() -> str:
    return f"_watchlist_cache_{_current_user_id()}"


def _read_cached_items() -> List[str]:
    try:
        import streamlit as st  # type: ignore

        cached = st.session_state.get(_cache_key(), [])
        return _normalize_items(cached if isinstance(cached, list) else [])
    except Exception:
        return []


def _write_cached_items(items: List[str]) -> None:
    try:
        import streamlit as st  # type: ignore

        st.session_state[_cache_key()] = list(items)
    except Exception:
        pass


def _clear_cached_items() -> None:
    try:
        import streamlit as st  # type: ignore

        st.session_state.pop(_cache_key(), None)
    except Exception:
        pass


def _load_remote_items() -> List[str]:
    uid = _current_user_id()
    rows = supabase_client.get_rows(
        "app_watchlist",
        params={
            "user_id": f"eq.{uid}",
            "select": "code",
            "order": "id.asc",
        },
    )
    raw_codes: List[str] = []
    for row in rows:
        code = str(row.get("code", "")).strip() if isinstance(row, dict) else ""
        raw_codes.append(code)
    return _normalize_items(raw_codes)


def watchlist_list() -> List[str]:
    if not supabase_client.is_enabled():
        clear_cloud_error("watchlist")
        return []
    try:
        items = _load_remote_items()
        _write_cached_items(items)
        clear_cloud_error("watchlist")
        return items
    except Exception as e:
        set_cloud_error("watchlist", e)
        return _read_cached_items()


def watchlist_add_result(code: str) -> Dict[str, Any]:
    code_n = _normalize_code(code)
    if not supabase_client.is_enabled():
        return {
            "ok": False,
            "code": code_n,
            "items": [],
            "message": "云端未配置，无法添加自选",
            "cloud_synced": False,
        }
    if not _is_valid_code(code_n):
        return {
            "ok": False,
            "code": code_n,
            "items": watchlist_list(),
            "message": "代码格式无效（仅支持 3-20 位字母、数字、点、下划线或短横线）",
            "cloud_synced": False,
        }

    try:
        uid = _current_user_id()
        exists = supabase_client.get_rows(
            "app_watchlist",
            params={
                "user_id": f"eq.{uid}",
                "code": f"eq.{code_n}",
                "select": "id",
                "limit": "1",
            },
        )
        if not exists:
            resp = supabase_client.insert_row("app_watchlist", {"user_id": uid, "code": code_n})
            if resp.status_code not in (200, 201, 409):
                raise RuntimeError(f"watchlist add failed({resp.status_code})")
        _clear_cached_items()
    except Exception as e:
        set_cloud_error("watchlist", e)
        return {
            "ok": False,
            "code": code_n,
            "items": watchlist_list(),
            "message": f"云端同步失败，请稍后重试：{e}",
            "cloud_synced": False,
        }

    return {
        "ok": True,
        "code": code_n,
        "items": watchlist_list(),
        "message": "已添加",
        "cloud_synced": True,
    }


def watchlist_add(code: str) -> dict:
    res = watchlist_add_result(code)
    return {"items": res.get("items", []), "updated_at": _now_iso()}


def watchlist_remove(code: str) -> dict:
    code_n = _normalize_code(code)
    if not code_n:
        return {"ok": True, "items": watchlist_list(), "updated_at": _now_iso()}
    if not supabase_client.is_enabled():
        return {
            "ok": False,
            "items": watchlist_list(),
            "updated_at": _now_iso(),
            "message": "云端未配置，无法移除自选",
        }

    try:
        uid = _current_user_id()
        resp = supabase_client.delete_rows(
            "app_watchlist",
            params={
                "user_id": f"eq.{uid}",
                "code": f"eq.{code_n}",
            },
        )
        if resp.status_code not in (200, 204):
            raise RuntimeError(f"watchlist remove failed({resp.status_code})")
        _clear_cached_items()
        return {"ok": True, "items": watchlist_list(), "updated_at": _now_iso()}
    except Exception as e:
        set_cloud_error("watchlist", e)
        return {
            "ok": False,
            "items": watchlist_list(),
            "updated_at": _now_iso(),
            "message": f"云端移除失败，请稍后重试：{e}",
        }


def list_watchlist() -> List[str]:
    return watchlist_list()


def add_to_watchlist(code: str) -> dict:
    return watchlist_add(code)


def remove_from_watchlist(code: str) -> dict:
    return watchlist_remove(code)


def get_watchlist() -> List[str]:
    return watchlist_list()


def watchlist_realtime_view() -> List[Dict[str, Any]]:
    codes = watchlist_list()
    if not codes:
        return []

    est_map = estimate_many(codes)
    profiles = {c: get_fund_profile(c) for c in codes}
    rows: List[Dict[str, Any]] = []
    for code in codes:
        est = est_map.get(code)
        profile = profiles.get(code)
        name = (profile.name if profile else "") or (est.name if est else "") or code
        if not est:
            rows.append(
                {
                    "code": code,
                    "name": name,
                    "est_change_pct": None,
                    "est_nav": None,
                    "method": "N/A",
                    "confidence": 0.0,
                    "warning": "估值失败",
                    "est_time": None,
                    "suggested_refresh_sec": None,
                }
            )
            continue
        rows.append(
            {
                "code": code,
                "name": name,
                "est_change_pct": est.est_change_pct,
                "est_nav": est.est_nav,
                "method": est.method,
                "confidence": est.confidence,
                "warning": est.warning,
                "est_time": est.est_time,
                "suggested_refresh_sec": est.suggested_refresh_sec,
            }
        )
    return rows
