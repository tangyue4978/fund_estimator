from __future__ import annotations

from datetime import date, datetime, timedelta
from time import time
from typing import Optional, Tuple

from config import constants
from datasources.nav_api import OfficialNav, fetch_official_navs
from services import supabase_client
from services.cloud_status_service import clear_cloud_error, get_cloud_error, set_cloud_error
from services.estimation_service import estimate_many
from services.snapshot_service import build_positions_as_of
from storage import paths

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None


_LEDGER_SELECT = (
    "date,code,shares_end,avg_cost_nav_end,realized_pnl_end,"
    "estimated_nav_close,estimated_pnl_close,official_nav,official_pnl,"
    "settle_status,updated_at"
)
_LEDGER_CACHE_TTL_SEC = 5.0


def _now_iso() -> str:
    if ZoneInfo is None:
        return datetime.now().isoformat(timespec="seconds")
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")


def _today_str() -> str:
    if ZoneInfo is None:
        return date.today().isoformat()
    return datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()


def _ledger_cache_key() -> str:
    return f"_daily_ledger_cache_{paths.current_user_id()}"


def _read_cached_ledger() -> dict:
    try:
        import streamlit as st  # type: ignore

        cached = st.session_state.get(_ledger_cache_key(), {})
        if isinstance(cached, dict) and isinstance(cached.get("items"), list):
            return {
                "items": [x for x in cached.get("items", []) if isinstance(x, dict)],
                "cached_at": float(cached.get("cached_at", 0.0) or 0.0),
            }
    except Exception:
        pass
    return {"items": [], "cached_at": 0.0}


def _write_cached_ledger(ledger: dict) -> None:
    try:
        import streamlit as st  # type: ignore

        items = ledger.get("items", []) if isinstance(ledger, dict) else []
        st.session_state[_ledger_cache_key()] = {
            "items": [x for x in items if isinstance(x, dict)],
            "cached_at": time(),
        }
    except Exception:
        pass


def _clear_ledger_cache() -> None:
    try:
        uid: Optional[str] = paths.current_user_id()
    except Exception:
        uid = None
    try:
        import streamlit as st  # type: ignore

        st.session_state.pop(_ledger_cache_key(), None)
    except Exception:
        pass
    try:
        from services.accuracy_service import clear_ledger_query_cache

        clear_ledger_query_cache(uid)
    except Exception:
        pass


def _fetch_ledger_rows(
    *,
    date_eq: str = "",
    start_date: str = "",
    end_date: str = "",
    code: str = "",
    settle_status: str = "",
    select: str = _LEDGER_SELECT,
    order: str = "date.asc,code.asc",
    limit: Optional[int] = None,
) -> list[dict]:
    params = {
        "user_id": f"eq.{paths.current_user_id()}",
        "select": select,
        "order": order,
    }
    if code:
        params["code"] = f"eq.{code}"
    if settle_status:
        params["settle_status"] = f"eq.{settle_status}"
    if date_eq:
        params["date"] = f"eq.{date_eq}"
    elif start_date and end_date:
        params["and"] = f"(date.gte.{start_date},date.lte.{end_date})"
    elif start_date:
        params["date"] = f"gte.{start_date}"
    elif end_date:
        params["date"] = f"lte.{end_date}"
    if limit is not None:
        if int(limit) <= 0:
            return []
        params["limit"] = str(int(limit))

    rows = supabase_client.get_rows_paginated("app_daily_ledger", params=params)
    return [row for row in rows if isinstance(row, dict)]


def _load_ledger(*, force_refresh: bool = False) -> dict:
    if not supabase_client.is_enabled():
        clear_cloud_error("daily_ledger")
        return {"items": []}
    cached = _read_cached_ledger()
    if (
        not force_refresh
        and not get_cloud_error("daily_ledger")
        and float(cached.get("cached_at", 0.0) or 0.0) > 0
        and time() - float(cached.get("cached_at", 0.0)) <= _LEDGER_CACHE_TTL_SEC
    ):
        return {"items": list(cached.get("items", []))}
    try:
        items = _fetch_ledger_rows()
        ledger = {"items": items}
        _write_cached_ledger(ledger)
        clear_cloud_error("daily_ledger")
        return ledger
    except Exception as e:
        set_cloud_error("daily_ledger", e)
        return {"items": list(cached.get("items", []))}


def get_ledger_items() -> list[dict]:
    ledger = _load_ledger()
    items = ledger.get("items", [])
    return items if isinstance(items, list) else []


def get_ledger_row(date_str: str, code: str) -> dict:
    d = str(date_str or "").strip()
    c = str(code or "").strip()
    if not d or not c:
        return {}
    if supabase_client.is_enabled():
        try:
            rows = _fetch_ledger_rows(date_eq=d, code=c, limit=1)
            clear_cloud_error("daily_ledger")
            return rows[0] if rows else {}
        except Exception as e:
            set_cloud_error("daily_ledger", e)
    else:
        clear_cloud_error("daily_ledger")
        return {}

    cached = _read_cached_ledger()
    for it in cached.get("items", []):
        if not isinstance(it, dict):
            continue
        if str(it.get("date", "")).strip() == d and str(it.get("code", "")).strip() == c:
            return it
    return {}


def finalize_estimated_close(date_str: Optional[str] = None) -> dict:
    d = str(date_str or _today_str()).strip()
    if d != _today_str():
        raise ValueError("历史日期不能使用当前行情生成收盘估算；请选择今天，历史数据请使用官方净值结算")
    if not supabase_client.is_enabled():
        raise RuntimeError("cloud storage is not configured")

    snapshots = build_positions_as_of(d)
    if get_cloud_error("adjustments"):
        raise RuntimeError("持仓流水读取失败，已取消日结写入以保护现有台账")
    codes = [s.code for s in snapshots]
    uid = paths.current_user_id()

    try:
        existing_rows = _fetch_ledger_rows(date_eq=d)
        code_set = set(codes)

        if not code_set:
            # An empty snapshot can also be caused by a temporary upstream or
            # permission problem. Never turn an empty read into bulk deletion.
            return _load_ledger()

        est_map = estimate_many(codes)
        missing_codes = sorted(
            code
            for code in code_set
            if not est_map.get(code)
            or float(est_map[code].est_nav or 0.0) <= 0
            or est_map[code].method == constants.METHOD_FROZEN_NAV
            or float(est_map[code].confidence or 0.0) <= 0
        )
        if missing_codes:
            joined = "、".join(missing_codes)
            raise RuntimeError(f"以下基金缺少有效实时收盘估值，已取消日结写入：{joined}")

        stale_codes = sorted(
            {
                str(r.get("code", "")).strip()
                for r in existing_rows
                if str(r.get("code", "")).strip() and str(r.get("code", "")).strip() not in code_set
            }
        )

        existing_map = {
            (str(r.get("date")), str(r.get("code"))): r
            for r in existing_rows
            if str(r.get("code", "")).strip() in code_set
        }
        upserts = []
        for s in snapshots:
            est = est_map[s.code]
            est_nav = float(est.est_nav)
            shares_end = float(s.shares_end)
            avg_cost_nav_end = float(s.avg_cost_nav_end)
            realized_pnl_end = float(s.realized_pnl_end)
            cost = shares_end * avg_cost_nav_end
            est_value = shares_end * est_nav
            est_pnl = est_value - cost + realized_pnl_end
            payload = {
                "user_id": uid,
                "date": d,
                "code": s.code,
                "shares_end": shares_end,
                "avg_cost_nav_end": avg_cost_nav_end,
                "realized_pnl_end": realized_pnl_end,
                "estimated_nav_close": float(est_nav),
                "estimated_pnl_close": float(est_pnl),
                "official_nav": None,
                "official_pnl": None,
                "settle_status": constants.SETTLE_ESTIMATED_ONLY,
                "updated_at": _now_iso(),
            }
            cur = existing_map.get((d, s.code))
            if cur and cur.get("settle_status") == constants.SETTLE_SETTLED:
                payload["official_nav"] = cur.get("official_nav")
                payload["official_pnl"] = cur.get("official_pnl")
                payload["settle_status"] = constants.SETTLE_SETTLED
            upserts.append(payload)

        if upserts:
            resp = supabase_client.upsert_rows("app_daily_ledger", upserts, on_conflict="user_id,date,code")
            if resp.status_code not in (200, 201):
                raise RuntimeError(f"finalize upsert failed({resp.status_code})")
        # Clean up obsolete rows only after the replacement rows are safely
        # stored. A failed upsert must never first delete valid ledger data.
        for stale_code in stale_codes:
            resp = supabase_client.delete_rows(
                "app_daily_ledger",
                {"user_id": f"eq.{uid}", "date": f"eq.{d}", "code": f"eq.{stale_code}"},
            )
            if resp.status_code not in (200, 204):
                raise RuntimeError(f"finalize stale cleanup failed({resp.status_code})")
        _clear_ledger_cache()
        return _load_ledger()
    except Exception as e:
        raise RuntimeError(f"finalize_estimated_close cloud failed: {e}") from e


def _official_nav_lookup(rows: list[dict], *, days_back: int = 180) -> dict[tuple[str, str], OfficialNav]:
    dates_by_code: dict[str, set[str]] = {}
    for item in rows:
        if item.get("settle_status") == constants.SETTLE_SETTLED:
            continue
        code = str(item.get("code") or "").strip()
        row_date = str(item.get("date") or "").strip()
        if code and row_date:
            dates_by_code.setdefault(code, set()).add(row_date)

    lookup: dict[tuple[str, str], OfficialNav] = {}
    for code, target_dates in sorted(dates_by_code.items()):
        for official_nav in fetch_official_navs(code, days_back=days_back):
            if official_nav.nav_date in target_dates:
                lookup[(code, official_nav.nav_date)] = official_nav
    return lookup


def _build_settlement_upserts(
    rows: list[dict],
    *,
    user_id: str,
    official_navs: dict[tuple[str, str], OfficialNav],
) -> list[dict]:
    upserts: list[dict] = []
    for item in rows:
        if item.get("settle_status") == constants.SETTLE_SETTLED:
            continue
        code = str(item.get("code") or "").strip()
        row_date = str(item.get("date") or "").strip()
        official = official_navs.get((code, row_date))
        if official is None:
            continue

        official_nav = float(official.nav)
        shares_end = float(item.get("shares_end", 0.0) or 0.0)
        avg_cost_nav_end = float(item.get("avg_cost_nav_end", 0.0) or 0.0)
        realized_pnl_end = float(item.get("realized_pnl_end", 0.0) or 0.0)
        cost = shares_end * avg_cost_nav_end
        official_value = shares_end * official_nav

        payload = dict(item)
        payload["official_nav"] = official_nav
        payload["official_pnl"] = float(official_value - cost + realized_pnl_end)
        payload["settle_status"] = constants.SETTLE_SETTLED
        payload["updated_at"] = _now_iso()
        payload["user_id"] = user_id
        upserts.append(payload)
    return upserts


def _write_settlement_upserts(upserts: list[dict]) -> None:
    if not upserts:
        return
    resp = supabase_client.upsert_rows("app_daily_ledger", upserts, on_conflict="user_id,date,code")
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"settle upsert failed({resp.status_code})")


def settle_day(date_str: str) -> Tuple[dict, int]:
    if not supabase_client.is_enabled():
        raise RuntimeError("cloud storage is not configured")
    target_date = str(date_str or "").strip()
    if not target_date:
        raise ValueError("date_str is required")

    try:
        uid = paths.current_user_id()
        rows = _fetch_ledger_rows(date_eq=target_date)
        official_navs = _official_nav_lookup(rows)
        upserts = _build_settlement_upserts(rows, user_id=uid, official_navs=official_navs)
        _write_settlement_upserts(upserts)
        _clear_ledger_cache()
        return _load_ledger(), len(upserts)
    except Exception as e:
        raise RuntimeError(f"settle_day cloud failed: {e}") from e


def settle_pending_days(max_days_back: int = 7) -> Tuple[dict, int]:
    if not supabase_client.is_enabled():
        return _load_ledger(), 0

    days_back = max(0, int(max_days_back))
    if days_back == 0:
        return _load_ledger(), 0

    today = datetime.fromisoformat(_today_str()).date()
    cutoff = (today - timedelta(days=days_back - 1)).isoformat()
    try:
        uid = paths.current_user_id()
        rows = _fetch_ledger_rows(
            start_date=cutoff,
            end_date=today.isoformat(),
            settle_status=constants.SETTLE_ESTIMATED_ONLY,
        )
        official_navs = _official_nav_lookup(rows)
        upserts = _build_settlement_upserts(rows, user_id=uid, official_navs=official_navs)
        _write_settlement_upserts(upserts)
        if upserts:
            _clear_ledger_cache()
        return _load_ledger(force_refresh=bool(upserts)), len(upserts)
    except Exception as e:
        raise RuntimeError(f"settle_pending_days cloud failed: {e}") from e


def count_pending_settlement(max_days_back: int = 7) -> int:
    days_back = max(1, int(max_days_back))
    today = datetime.fromisoformat(_today_str()).date()
    cutoff = (today - timedelta(days=days_back - 1)).isoformat()
    if not supabase_client.is_enabled():
        clear_cloud_error("daily_ledger")
        return 0
    try:
        rows = _fetch_ledger_rows(
            start_date=cutoff,
            end_date=today.isoformat(),
            settle_status=constants.SETTLE_ESTIMATED_ONLY,
            select="date,code,settle_status",
        )
        clear_cloud_error("daily_ledger")
        return len(rows)
    except Exception as e:
        set_cloud_error("daily_ledger", e)

    pending = 0
    for it in _read_cached_ledger().get("items", []):
        if not isinstance(it, dict):
            continue
        d = str(it.get("date", ""))
        if (not d) or d < cutoff or d > today.isoformat():
            continue
        if str(it.get("settle_status", "")) == constants.SETTLE_ESTIMATED_ONLY:
            pending += 1
    return pending
