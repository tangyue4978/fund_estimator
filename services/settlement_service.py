from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional, Tuple

from config import constants
from datasources.nav_api import fetch_official_nav_for_date
from services.portfolio_service import position_list
from services.estimation_service import estimate_many
from storage import paths
from storage.json_store import ensure_json_file, update_json

from services.snapshot_service import build_positions_as_of


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _load_ledger() -> dict:
    p = paths.file_daily_ledger()
    res = ensure_json_file(p)
    data = res.data if isinstance(res.data, dict) else {}
    if "items" not in data or not isinstance(data.get("items"), list):
        data["items"] = []
    return data


def _find_item_index(items: list, date_str: str, code: str) -> Optional[int]:
    for i, it in enumerate(items):
        if str(it.get("date")) == date_str and str(it.get("code")) == code:
            return i
    return None


def finalize_estimated_close(date_str: Optional[str] = None) -> dict:
    d = date_str or date.today().isoformat()

    snapshots = build_positions_as_of(d)
    codes = [s.code for s in snapshots]

    if not codes:
        return _load_ledger()

    est_map = estimate_many(codes)
    ledger_path = paths.file_daily_ledger()

    def updater(data: dict):
        items = data.get("items", [])
        if not isinstance(items, list):
            items = []

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
    for i in range(max_days_back):
        d = (date.today() - timedelta(days=i)).isoformat()
        _, cnt = settle_day(d)
        total += cnt

    return _load_ledger(), total
