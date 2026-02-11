from __future__ import annotations

import re
import os
from datetime import datetime
from typing import Any, Dict, List

from services import supabase_client
from storage import paths
from storage.json_store import ensure_json_file, update_json
from services.estimation_service import estimate_many
from services.fund_service import get_fund_profile
from config import settings


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _load() -> dict:
    p = paths.file_watchlist()
    res = ensure_json_file(p)
    data = res.data if isinstance(res.data, dict) else {}
    if "items" not in data or not isinstance(data.get("items"), list):
        data["items"] = []
    return data


def _current_user_id() -> str:
    return paths.current_user_id()


def _is_web_runtime() -> bool:
    return bool(os.getenv("STREAMLIT_SHARING_MODE", "").strip())


def _strict_web_cloud_sync() -> bool:
    return _is_web_runtime() and bool(getattr(settings, "WEB_WATCHLIST_REQUIRE_CLOUD_SYNC", True))


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


def _load_local_items() -> List[str]:
    data = _load()
    items = data.get("items", [])
    if not isinstance(items, list):
        return []
    return _normalize_items(items)


def _merge_codes(primary: List[str], secondary: List[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for x in list(primary) + list(secondary):
        code = _normalize_code(x)
        if (not code) or (code in seen):
            continue
        seen.add(code)
        out.append(code)
    return out


def _add_local(code: str) -> dict:
    p = paths.file_watchlist()

    def updater(data: dict):
        items = data.get("items", [])
        if not isinstance(items, list):
            items = []
        normalized = _normalize_items(items)
        if code not in normalized:
            normalized.append(code)
        data["items"] = normalized
        data["updated_at"] = _now_iso()
        return data

    return update_json(p, updater)


def _remove_local(code: str) -> dict:
    p = paths.file_watchlist()

    def updater(data: dict):
        items = data.get("items", [])
        if not isinstance(items, list):
            items = []
        normalized = _normalize_items(items)
        data["items"] = [x for x in normalized if x != code]
        data["updated_at"] = _now_iso()
        return data

    return update_json(p, updater)


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
    local_items = _load_local_items()
    if _strict_web_cloud_sync() and (not supabase_client.is_enabled()):
        return []
    if supabase_client.is_enabled():
        try:
            remote_items = _load_remote_items()
            if _strict_web_cloud_sync():
                return remote_items
            return _merge_codes(remote_items, local_items)
        except Exception:
            if _strict_web_cloud_sync():
                return []
            return local_items
    return local_items


def watchlist_add_result(code: str) -> Dict[str, Any]:
    code_n = _normalize_code(code)
    cloud_enabled = bool(supabase_client.is_enabled())
    if _strict_web_cloud_sync() and (not cloud_enabled):
        return {
            "ok": False,
            "code": code_n,
            "items": [],
            "message": "网页端云端未配置，无法添加自选",
            "cloud_synced": False,
        }
    if not _is_valid_code(code_n):
        return {
            "ok": False,
            "code": code_n,
            "items": watchlist_list(),
            "message": "代码格式无效（仅支持3-20位字母/数字/._-）",
            "cloud_synced": False,
        }

    _add_local(code_n)

    cloud_synced = False
    cloud_error = ""
    if cloud_enabled:
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
            cloud_synced = True
        except Exception as e:
            cloud_error = str(e)

    items = watchlist_list()
    if cloud_enabled and (not cloud_synced):
        if _is_web_runtime() and bool(getattr(settings, "WEB_WATCHLIST_REQUIRE_CLOUD_SYNC", True)):
            _remove_local(code_n)
            return {
                "ok": False,
                "code": code_n,
                "items": watchlist_list(),
                "message": "网页端云端同步失败，已取消本次添加，请稍后重试",
                "cloud_synced": False,
            }
        msg = "已添加到本地，云端同步失败"
        if cloud_error:
            msg = f"{msg}：{cloud_error}"
        return {
            "ok": True,
            "code": code_n,
            "items": items,
            "message": msg,
            "cloud_synced": False,
        }
    return {
        "ok": True,
        "code": code_n,
        "items": items,
        "message": "已添加",
        # Local-only mode should still be treated as successful sync state for UI.
        "cloud_synced": True if (not cloud_enabled) else cloud_synced,
    }


def watchlist_add(code: str) -> dict:
    res = watchlist_add_result(code)
    return {"items": res.get("items", []), "updated_at": _now_iso()}


def watchlist_remove(code: str) -> dict:
    code_n = _normalize_code(code)
    if not code_n:
        return {"ok": True, "items": watchlist_list(), "updated_at": _now_iso()}

    cloud_enabled = bool(supabase_client.is_enabled())
    strict_web = _strict_web_cloud_sync()
    if strict_web and (not cloud_enabled):
        return {"ok": False, "items": watchlist_list(), "updated_at": _now_iso(), "message": "网页端云端未配置，无法移除自选"}

    if cloud_enabled:
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
            _remove_local(code_n)
            return {"ok": True, "items": watchlist_list(), "updated_at": _now_iso()}
        except Exception:
            if strict_web:
                return {"ok": False, "items": watchlist_list(), "updated_at": _now_iso(), "message": "云端移除失败，请稍后重试"}
            _remove_local(code_n)
            return {"ok": True, "items": watchlist_list(), "updated_at": _now_iso()}

    _remove_local(code_n)
    return {"ok": True, "items": watchlist_list(), "updated_at": _now_iso()}


# compatibility aliases

def list_watchlist() -> List[str]:
    return watchlist_list()


def add_to_watchlist(code: str) -> dict:
    return watchlist_add(code)


def remove_from_watchlist(code: str) -> dict:
    return watchlist_remove(code)


def get_watchlist() -> List[str]:
    return watchlist_list()


def watchlist_realtime_view() -> List[Dict[str, Any]]:
    """
    Real-time view rows for UI.
    """
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
