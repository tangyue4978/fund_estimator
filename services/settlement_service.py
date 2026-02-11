from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from typing import Optional, Tuple
try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None

from config import constants
from datasources.nav_api import fetch_official_nav_for_date
from services.estimation_service import estimate_many
from services import supabase_client
from storage import paths
from storage.json_store import ensure_json_file, update_json

from services.snapshot_service import build_positions_as_of


def _strict_web_cloud_mode() -> bool:
    return bool(os.getenv("STREAMLIT_SHARING_MODE", "").strip())


def _now_iso() -> str:
    if ZoneInfo is None:
        return datetime.now().isoformat(timespec="seconds")
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")


def _today_str() -> str:
    if ZoneInfo is None:
        return date.today().isoformat()
    return datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()


def _load_ledger() -> dict:
    if _strict_web_cloud_mode() and (not supabase_client.is_enabled()):
        return {"items": []}
    if supabase_client.is_enabled():
        try:
            rows = supabase_client.get_rows(
                "app_daily_ledger",
                params={
                    "user_id": f"eq.{paths.current_user_id()}",
                    "select": (
                        "date,code,shares_end,avg_cost_nav_end,realized_pnl_end,"
                        "estimated_nav_close,estimated_pnl_close,official_nav,official_pnl,"
                        "settle_status,updated_at"
                    ),
                    "order": "date.asc,code.asc",
                },
            )
            return {"items": [x for x in rows if isinstance(x, dict)]}
        except Exception:
            if _strict_web_cloud_mode():
                return {"items": []}

    p = paths.file_daily_ledger()
    res = ensure_json_file(p)
    data = res.data if isinstance(res.data, dict) else {}
    if "items" not in data or not isinstance(data.get("items"), list):
        data["items"] = []
    return data


def get_ledger_items() -> list[dict]:
    ledger = _load_ledger()
    items = ledger.get("items", [])
    return items if isinstance(items, list) else []


def get_ledger_row(date_str: str, code: str) -> dict:
    d = str(date_str or "").strip()
    c = str(code or "").strip()
    if not d or not c:
        return {}
    for it in get_ledger_items():
        if not isinstance(it, dict):
            continue
        if str(it.get("date", "")).strip() == d and str(it.get("code", "")).strip() == c:
            return it
    return {}


def _find_item_index(items: list, date_str: str, code: str) -> Optional[int]:
    for i, it in enumerate(items):
        if str(it.get("date")) == date_str and str(it.get("code")) == code:
            return i
    return None


def finalize_estimated_close(date_str: Optional[str] = None) -> dict:
    d = date_str or _today_str()
    if _strict_web_cloud_mode() and (not supabase_client.is_enabled()):
        raise RuntimeError("cloud storage is not configured")

    snapshots = build_positions_as_of(d)
    codes = [s.code for s in snapshots]

    if supabase_client.is_enabled():
        try:
            uid = paths.current_user_id()
            existing_rows = supabase_client.get_rows(
                "app_daily_ledger",
                params={
                    "user_id": f"eq.{uid}",
                    "date": f"eq.{d}",
                    "select": (
                        "date,code,shares_end,avg_cost_nav_end,realized_pnl_end,"
                        "estimated_nav_close,estimated_pnl_close,official_nav,official_pnl,"
                        "settle_status,updated_at"
                    ),
                },
            )
            existing_rows = [x for x in existing_rows if isinstance(x, dict)]
            code_set = set(codes)

            # Keep ledger rows strictly aligned to current snapshot codes for this date.
            if not code_set:
                if existing_rows:
                    resp = supabase_client.delete_rows(
                        "app_daily_ledger",
                        {"user_id": f"eq.{uid}", "date": f"eq.{d}"},
                    )
                    if resp.status_code not in (200, 204):
                        raise RuntimeError(f"finalize cleanup failed({resp.status_code})")
                return _load_ledger()

            stale_codes = sorted(
                {
                    str(r.get("code", "")).strip()
                    for r in existing_rows
                    if str(r.get("code", "")).strip() and str(r.get("code", "")).strip() not in code_set
                }
            )
            for stale_code in stale_codes:
                resp = supabase_client.delete_rows(
                    "app_daily_ledger",
                    {
                        "user_id": f"eq.{uid}",
                        "date": f"eq.{d}",
                        "code": f"eq.{stale_code}",
                    },
                )
                if resp.status_code not in (200, 204):
                    raise RuntimeError(f"finalize stale cleanup failed({resp.status_code})")

            est_map = estimate_many(codes)
            existing_map = {
                (str(r.get("date")), str(r.get("code"))): r
                for r in existing_rows
                if str(r.get("code", "")).strip() in code_set
            }
            upserts = []
            for s in snapshots:
                est = est_map.get(s.code)
                est_nav = est.est_nav if est else 0.0

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
                resp = supabase_client.upsert_rows(
                    "app_daily_ledger",
                    upserts,
                    on_conflict="user_id,date,code",
                )
                if resp.status_code not in (200, 201):
                    raise RuntimeError(f"finalize upsert failed({resp.status_code})")
            return _load_ledger()
        except Exception as e:
            if _strict_web_cloud_mode():
                raise RuntimeError(f"finalize_estimated_close cloud failed: {e}") from e

    if not codes:
        ledger_path = paths.file_daily_ledger()

        def cleaner(data: dict):
            items = data.get("items", [])
            if not isinstance(items, list):
                items = []
            data["items"] = [
                it
                for it in items
                if str(it.get("date", "")).strip() != d
            ]
            data["updated_at"] = _now_iso()
            return data

        return update_json(ledger_path, cleaner)

    est_map = estimate_many(codes)

    ledger_path = paths.file_daily_ledger()

    def updater(data: dict):
        items = data.get("items", [])
        if not isinstance(items, list):
            items = []
        code_set = set(codes)
        # Keep ledger rows strictly aligned to current snapshot codes for this date.
        items = [
            it
            for it in items
            if not (
                str(it.get("date", "")).strip() == d
                and str(it.get("code", "")).strip() not in code_set
            )
        ]

        for s in snapshots:
            est = est_map.get(s.code)
            est_nav = est.est_nav if est else 0.0

            shares_end = float(s.shares_end)
            avg_cost_nav_end = float(s.avg_cost_nav_end)
            realized_pnl_end = float(s.realized_pnl_end)

            cost = shares_end * avg_cost_nav_end
            est_value = shares_end * est_nav
            est_pnl = est_value - cost + realized_pnl_end

            idx = _find_item_index(items, d, s.code)

            payload = {
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

            if idx is None:
                items.append(payload)
            else:
                if items[idx].get("settle_status") == constants.SETTLE_SETTLED:
                    items[idx]["shares_end"] = shares_end
                    items[idx]["avg_cost_nav_end"] = avg_cost_nav_end
                    items[idx]["realized_pnl_end"] = realized_pnl_end
                    items[idx]["updated_at"] = _now_iso()
                else:
                    items[idx] = payload

        data["items"] = items
        data["updated_at"] = _now_iso()
        return data

    return update_json(ledger_path, updater)


def settle_day(date_str: str) -> Tuple[dict, int]:
    """
    尝试结算某一天：
    - 只对 estimated_only 记录尝试覆盖
    - 严格 nav_date == date_str 才覆盖
    - official_pnl 与 estimated_pnl 口径一致（包含 realized_pnl_end）
    """
    if _strict_web_cloud_mode() and (not supabase_client.is_enabled()):
        raise RuntimeError("cloud storage is not configured")
    if supabase_client.is_enabled():
        try:
            uid = paths.current_user_id()
            rows = supabase_client.get_rows(
                "app_daily_ledger",
                params={
                    "user_id": f"eq.{uid}",
                    "date": f"eq.{date_str}",
                    "select": (
                        "date,code,shares_end,avg_cost_nav_end,realized_pnl_end,"
                        "estimated_nav_close,estimated_pnl_close,official_nav,official_pnl,"
                        "settle_status,updated_at"
                    ),
                },
            )
            settled = 0
            upserts = []
            for it in rows:
                if not isinstance(it, dict):
                    continue
                if it.get("settle_status") == constants.SETTLE_SETTLED:
                    continue

                code = str(it.get("code"))
                off = fetch_official_nav_for_date(code, date_str)
                if not off:
                    continue

                official_nav = float(off.nav)
                shares_end = float(it.get("shares_end", 0.0))
                avg_cost_nav_end = float(it.get("avg_cost_nav_end", 0.0))
                realized_pnl_end = float(it.get("realized_pnl_end", 0.0))

                cost = shares_end * avg_cost_nav_end
                official_value = shares_end * official_nav
                official_pnl = official_value - cost + realized_pnl_end

                it["official_nav"] = official_nav
                it["official_pnl"] = float(official_pnl)
                it["settle_status"] = constants.SETTLE_SETTLED
                it["updated_at"] = _now_iso()
                it["user_id"] = uid
                upserts.append(it)
                settled += 1

            if upserts:
                resp = supabase_client.upsert_rows(
                    "app_daily_ledger",
                    upserts,
                    on_conflict="user_id,date,code",
                )
                if resp.status_code not in (200, 201):
                    raise RuntimeError(f"settle upsert failed({resp.status_code})")
            return _load_ledger(), settled
        except Exception as e:
            if _strict_web_cloud_mode():
                raise RuntimeError(f"settle_day cloud failed: {e}") from e

    ledger_path = paths.file_daily_ledger()

    def updater(data: dict):
        items = data.get("items", [])
        settled = 0

        for it in items:
            if str(it.get("date")) != date_str:
                continue
            if it.get("settle_status") == constants.SETTLE_SETTLED:
                continue

            code = str(it.get("code"))
            off = fetch_official_nav_for_date(code, date_str)
            if not off:
                continue

            official_nav = float(off.nav)
            shares_end = float(it.get("shares_end", 0.0))
            avg_cost_nav_end = float(it.get("avg_cost_nav_end", 0.0))
            realized_pnl_end = float(it.get("realized_pnl_end", 0.0))

            cost = shares_end * avg_cost_nav_end
            official_value = shares_end * official_nav
            official_pnl = official_value - cost + realized_pnl_end

            it["official_nav"] = official_nav
            it["official_pnl"] = float(official_pnl)
            it["settle_status"] = constants.SETTLE_SETTLED
            it["updated_at"] = _now_iso()
            settled += 1

        data["items"] = items
        data["updated_at"] = _now_iso()
        data["_last_settle_day"] = date_str
        data["_last_settle_count"] = settled
        return data

    new_data = update_json(ledger_path, updater)
    settled_count = int(new_data.get("_last_settle_count", 0))
    return new_data, settled_count


def settle_pending_days(max_days_back: int = 7) -> Tuple[dict, int]:
    """
    扫描最近 N 天的未结算记录，逐日尝试覆盖。
    注意：只有 ledger 中存在该日期的 estimated_only 记录才会结算。
    """
    ledger = _load_ledger()
    items = ledger.get("items", [])
    if not items:
        return ledger, 0

    total = 0
    today = datetime.fromisoformat(_today_str()).date()
    for i in range(max_days_back):
        d = (today - timedelta(days=i)).isoformat()
        _, cnt = settle_day(d)
        total += cnt

    return _load_ledger(), total


def count_pending_settlement(max_days_back: int = 7) -> int:
    """
    Count estimated_only ledger rows in recent N days.
    Used by scheduler to decide whether retries can stop for tonight.
    """
    days_back = max(1, int(max_days_back))
    today = datetime.fromisoformat(_today_str()).date()
    cutoff = (today - timedelta(days=days_back - 1)).isoformat()
    ledger = _load_ledger()
    items = ledger.get("items", [])
    if not isinstance(items, list):
        return 0

    pending = 0
    for it in items:
        if not isinstance(it, dict):
            continue
        d = str(it.get("date", ""))
        if (not d) or d < cutoff:
            continue
        if str(it.get("settle_status", "")) == constants.SETTLE_ESTIMATED_ONLY:
            pending += 1
    return pending
