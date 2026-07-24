from __future__ import annotations

from datetime import timedelta
from typing import Dict, List, Optional

from config import constants
from services.cloud_status_service import clear_cloud_error, set_cloud_error
from services import supabase_client
from services.trading_time import now_cn
from storage import paths


_LEDGER_SELECT = (
    "date,code,shares_end,avg_cost_nav_end,realized_pnl_end,"
    "estimated_nav_close,estimated_pnl_close,official_nav,official_pnl,settle_status"
)
_PNL_SELECT = "date,code,estimated_pnl_close,official_pnl,settle_status"


def _load_ledger_items(
    *,
    code: str = "",
    date_eq: str = "",
    start_date: str = "",
    end_date: str = "",
    before_date: str = "",
    order: str = "date.asc,code.asc",
    limit: Optional[int] = None,
    cumulative_pnl_only: bool = False,
) -> List[dict]:
    if not supabase_client.is_enabled():
        clear_cloud_error("daily_ledger")
        return []
    try:
        params = {
            "user_id": f"eq.{paths.current_user_id()}",
            "select": _PNL_SELECT if cumulative_pnl_only else _LEDGER_SELECT,
            "order": order,
        }
        if code:
            params["code"] = f"eq.{code}"
        if date_eq:
            params["date"] = f"eq.{date_eq}"
        elif before_date:
            params["date"] = f"lt.{before_date}"
        elif start_date and end_date:
            params["and"] = f"(date.gte.{start_date},date.lte.{end_date})"
        elif start_date:
            params["date"] = f"gte.{start_date}"
        elif end_date:
            params["date"] = f"lte.{end_date}"
        if cumulative_pnl_only:
            params["or"] = (
                f"(and(settle_status.eq.{constants.SETTLE_SETTLED},official_pnl.not.is.null),"
                "estimated_pnl_close.not.is.null)"
            )
        if limit is not None:
            if int(limit) <= 0:
                return []
            params["limit"] = str(int(limit))

        rows = supabase_client.get_rows_paginated(
            "app_daily_ledger",
            params=params,
        )
        items = [x for x in rows if isinstance(x, dict)]
        clear_cloud_error("daily_ledger")
        return items
    except Exception as e:
        set_cloud_error("daily_ledger", e)
        return []


def _cumulative_pnl_from_row(item: dict) -> Optional[float]:
    status = str(item.get("settle_status", "")).strip()
    if status == constants.SETTLE_SETTLED and item.get("official_pnl") is not None:
        try:
            return float(item.get("official_pnl"))
        except Exception:
            pass
    if item.get("estimated_pnl_close") is not None:
        try:
            return float(item.get("estimated_pnl_close"))
        except Exception:
            pass
    return None


def get_fund_cumulative_pnl_on(code: str, date_str: str) -> Optional[float]:
    code = (code or "").strip()
    date_str = (date_str or "").strip()
    if not code or not date_str:
        return None

    for it in _load_ledger_items(
        code=code,
        date_eq=date_str,
        limit=1,
        cumulative_pnl_only=True,
    ):
        if str(it.get("code", "")).strip() != code:
            continue
        if str(it.get("date", "")).strip() != date_str:
            continue
        return _cumulative_pnl_from_row(it)
    return None


def get_latest_fund_cumulative_pnl_before(code: str, date_str: str) -> Optional[float]:
    code = (code or "").strip()
    date_str = (date_str or "").strip()
    if not code or not date_str:
        return None

    rows = [
        item
        for item in _load_ledger_items(
            code=code,
            before_date=date_str,
            order="date.desc,code.asc",
            limit=1,
            cumulative_pnl_only=True,
        )
        if str(item.get("code", "")).strip() == code
        and str(item.get("date", "")).strip()
        and str(item.get("date", "")).strip() < date_str
    ]
    rows.sort(key=lambda item: str(item.get("date", "")), reverse=True)
    for item in rows:
        value = _cumulative_pnl_from_row(item)
        if value is not None:
            return value
    return None


def get_history(code: str, days: int = 90) -> List[dict]:
    code = (code or "").strip()
    if not code:
        raise ValueError("code is required")
    if days <= 0:
        return []

    today = now_cn().date()
    start = (today - timedelta(days=days - 1)).isoformat()
    end = today.isoformat()

    rows: List[dict] = []
    for it in _load_ledger_items(code=code, start_date=start, end_date=end):
        if str(it.get("code")) != code:
            continue
        d = str(it.get("date"))
        if d < start or d > end:
            continue
        status = str(it.get("settle_status", constants.SETTLE_ESTIMATED_ONLY))
        if status == constants.SETTLE_SETTLED and it.get("official_nav") is not None:
            nav = float(it["official_nav"] or 0.0)
            if nav > 0:
                rows.append({"date": d, "nav": nav, "source": "official", "settle_status": status})
        else:
            nav = float(it.get("estimated_nav_close", 0.0) or 0.0)
            if nav > 0:
                rows.append({"date": d, "nav": nav, "source": "estimated", "settle_status": status})

    rows.sort(key=lambda x: x["date"])
    return rows


def get_portfolio_history(days: int = 90) -> List[dict]:
    if days <= 0:
        return []

    today = now_cn().date()
    start = (today - timedelta(days=days - 1)).isoformat()
    end = today.isoformat()

    by_date: Dict[str, List[dict]] = {}
    for it in _load_ledger_items(start_date=start, end_date=end):
        d = str(it.get("date"))
        if d < start or d > end:
            continue
        by_date.setdefault(d, []).append(it)

    out: List[dict] = []
    for d, lst in by_date.items():
        all_settled = all(x.get("settle_status") == constants.SETTLE_SETTLED for x in lst)
        total_cost = 0.0
        total_value = 0.0
        total_pnl = 0.0

        for it in lst:
            shares = float(it.get("shares_end", 0.0) or 0.0)
            cost_nav = float(it.get("avg_cost_nav_end", 0.0) or 0.0)
            realized = float(it.get("realized_pnl_end", 0.0) or 0.0)
            cost = shares * cost_nav
            total_cost += cost
            nav = float(it["official_nav"]) if all_settled and it.get("official_nav") is not None else float(it.get("estimated_nav_close", 0.0) or 0.0)
            value = shares * nav
            pnl = value - cost + realized
            total_value += value
            total_pnl += pnl

        out.append(
            {
                "date": d,
                "total_cost": total_cost,
                "total_value": total_value,
                "total_pnl": total_pnl,
                "total_pnl_pct": (total_pnl / total_cost * 100.0) if total_cost > 0 else 0.0,
                "source": "official" if all_settled else "estimated",
                "settle_status": constants.SETTLE_SETTLED if all_settled else constants.SETTLE_ESTIMATED_ONLY,
            }
        )

    out.sort(key=lambda x: x["date"])
    return out


def fund_history(code: str, days_back: int = 60) -> List[dict]:
    code = (code or "").strip()
    if not code:
        return []
    try:
        return get_history(code, days=max(1, int(days_back)))
    except Exception as e:
        set_cloud_error("daily_ledger", e)
        return []
