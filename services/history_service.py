from __future__ import annotations

import os
from datetime import timedelta
from typing import Dict, List, Optional

from config import constants
from services import supabase_client
from services.trading_time import now_cn
from storage import paths
from storage.json_store import ensure_json_file


def _strict_web_cloud_mode() -> bool:
    return bool(os.getenv("STREAMLIT_SHARING_MODE", "").strip())


def _load_ledger_items() -> List[dict]:
    if _strict_web_cloud_mode() and (not supabase_client.is_enabled()):
        return []
    if supabase_client.is_enabled():
        try:
            rows = supabase_client.get_rows(
                "app_daily_ledger",
                params={
                    "user_id": f"eq.{paths.current_user_id()}",
                    "select": (
                        "date,code,shares_end,avg_cost_nav_end,realized_pnl_end,"
                        "estimated_nav_close,estimated_pnl_close,official_nav,official_pnl,settle_status"
                    ),
                    "order": "date.asc,code.asc",
                },
            )
            return [x for x in rows if isinstance(x, dict)]
        except Exception:
            if _strict_web_cloud_mode():
                return []

    res = ensure_json_file(paths.file_daily_ledger())
    data = res.data if isinstance(res.data, dict) else {}
    items = data.get("items", [])
    return items if isinstance(items, list) else []


def get_fund_cumulative_pnl_on(code: str, date_str: str) -> Optional[float]:
    code = (code or "").strip()
    date_str = (date_str or "").strip()
    if not code or not date_str:
        return None

    for it in _load_ledger_items():
        if str(it.get("code", "")).strip() != code:
            continue
        if str(it.get("date", "")).strip() != date_str:
            continue

        status = str(it.get("settle_status", "")).strip()
        if status == constants.SETTLE_SETTLED and it.get("official_pnl") is not None:
            try:
                return float(it.get("official_pnl"))
            except Exception:
                pass

        if it.get("estimated_pnl_close") is not None:
            try:
                return float(it.get("estimated_pnl_close"))
            except Exception:
                pass
        return None
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
    for it in _load_ledger_items():
        if str(it.get("code")) != code:
            continue

        d = str(it.get("date"))
        if d < start or d > end:
            continue

        status = str(it.get("settle_status", constants.SETTLE_ESTIMATED_ONLY))
        if status == constants.SETTLE_SETTLED and it.get("official_nav") is not None:
            rows.append({"date": d, "nav": float(it["official_nav"]), "source": "official", "settle_status": status})
        else:
            rows.append({"date": d, "nav": float(it.get("estimated_nav_close", 0.0)), "source": "estimated", "settle_status": status})

    rows.sort(key=lambda x: x["date"])
    return rows


def get_portfolio_history(days: int = 90) -> List[dict]:
    if days <= 0:
        return []

    today = now_cn().date()
    start = (today - timedelta(days=days - 1)).isoformat()
    end = today.isoformat()

    by_date: Dict[str, List[dict]] = {}
    for it in _load_ledger_items():
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

            if all_settled and it.get("official_nav") is not None:
                nav = float(it["official_nav"])
            else:
                nav = float(it.get("estimated_nav_close", 0.0) or 0.0)

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


def fund_history(code: str, days_back: int = 60):
    code = (code or "").strip()
    if not code:
        return []
    try:
        return get_history(code, days=max(1, int(days_back)))
    except Exception:
        return []
