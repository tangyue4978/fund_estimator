from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional, Tuple

from config import constants
from datasources.nav_api import fetch_official_nav_for_date
from services import supabase_client
from services.cloud_status_service import clear_cloud_error, set_cloud_error
from services.estimation_service import estimate_many
from services.snapshot_service import build_positions_as_of
from storage import paths

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None


def _now_iso() -> str:
    if ZoneInfo is None:
        return datetime.now().isoformat(timespec="seconds")
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")


def _today_str() -> str:
    if ZoneInfo is None:
        return date.today().isoformat()
    return datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()


def _load_ledger() -> dict:
    if not supabase_client.is_enabled():
        clear_cloud_error("daily_ledger")
        return {"items": []}
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
        items = [x for x in rows if isinstance(x, dict)]
        clear_cloud_error("daily_ledger")
        return {"items": items}
    except Exception as e:
        set_cloud_error("daily_ledger", e)
        return {"items": []}


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


def finalize_estimated_close(date_str: Optional[str] = None) -> dict:
    d = date_str or _today_str()
    if not supabase_client.is_enabled():
        raise RuntimeError("cloud storage is not configured")

    snapshots = build_positions_as_of(d)
    codes = [s.code for s in snapshots]
    uid = paths.current_user_id()

    try:
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

        if not code_set:
            if existing_rows:
                resp = supabase_client.delete_rows("app_daily_ledger", {"user_id": f"eq.{uid}", "date": f"eq.{d}"})
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
                {"user_id": f"eq.{uid}", "date": f"eq.{d}", "code": f"eq.{stale_code}"},
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
            resp = supabase_client.upsert_rows("app_daily_ledger", upserts, on_conflict="user_id,date,code")
            if resp.status_code not in (200, 201):
                raise RuntimeError(f"finalize upsert failed({resp.status_code})")
        return _load_ledger()
    except Exception as e:
        raise RuntimeError(f"finalize_estimated_close cloud failed: {e}") from e


def settle_day(date_str: str) -> Tuple[dict, int]:
    if not supabase_client.is_enabled():
        raise RuntimeError("cloud storage is not configured")

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
            resp = supabase_client.upsert_rows("app_daily_ledger", upserts, on_conflict="user_id,date,code")
            if resp.status_code not in (200, 201):
                raise RuntimeError(f"settle upsert failed({resp.status_code})")
        return _load_ledger(), settled
    except Exception as e:
        raise RuntimeError(f"settle_day cloud failed: {e}") from e


def settle_pending_days(max_days_back: int = 7) -> Tuple[dict, int]:
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
