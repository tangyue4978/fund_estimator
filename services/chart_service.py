from __future__ import annotations

from datetime import datetime, timedelta
from typing import List, Optional

from datasources.gsz_provider import get_gsz_quote
from datasources.nav_history_provider import get_official_nav_history
from services.trading_time import is_cn_trading_time, now_cn
from services.intraday_service import intraday_load_fund_series
from config import constants
from storage.ledger_repo import get_daily_ledger_items


CHART_OFFICIAL_NAV = "official_nav"
CHART_REALTIME_EST = "realtime_estimation"
CHART_MY_PROFIT = "my_profit"

_RANGE_TO_DAYS = {
    "1W": 7,
    "1M": 30,
    "3M": 90,
    "6M": 180,
    "1Y": 365,
    "ALL": 0,
}


def _safe_float(v, default: Optional[float] = None) -> Optional[float]:
    try:
        return float(v)
    except Exception:
        return default


def _point_date(s: str) -> Optional[datetime]:
    raw = str(s or "").strip()
    if not raw:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw[:19], fmt)
        except Exception:
            continue
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def _filter_range(points: List[dict], range_value: str) -> List[dict]:
    days = _RANGE_TO_DAYS.get(str(range_value or "1M").upper(), 30)
    if days <= 0:
        return points
    cutoff = now_cn().replace(tzinfo=None) - timedelta(days=days)
    out: List[dict] = []
    for it in points:
        dt = _point_date(str(it.get("date", "")))
        if dt is None:
            continue
        if dt >= cutoff:
            out.append(it)
    return out


def _date_from_iso_like(raw: str) -> Optional[str]:
    s = str(raw or "").strip()
    if not s:
        return None
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date().isoformat()
    except Exception:
        return None


def _datetime_from_iso_like(raw: str) -> Optional[datetime]:
    s = str(raw or "").strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s[:19], fmt)
        except Exception:
            continue
    return None


def _load_realtime_series(code: str) -> List[dict]:
    today_str = now_cn().date().isoformat()
    out: List[dict] = []
    for item in intraday_load_fund_series(code, limit=720, date_str=today_str):
        value = _safe_float(item.get("est_nav"), None)
        if value is None or value <= 0:
            continue
        timestamp = str(item.get("date") or "").strip()
        if not timestamp:
            point_time = str(item.get("t") or "").strip()
            timestamp = f"{today_str} {point_time}" if point_time else ""
        if _datetime_from_iso_like(timestamp) is None:
            continue
        out.append({"date": timestamp, "value": value})

    # Add the latest provider point even before the first persisted refresh.
    q = get_gsz_quote(code)
    gsz_val = _safe_float(getattr(q, "gsz", None) if q else None, None)
    if gsz_val is not None and gsz_val > 0:
        quote_date = _date_from_iso_like(getattr(q, "gztime", "") if q else "")
        use_today_est = bool(quote_date == today_str) or is_cn_trading_time(now_cn())
        if use_today_est:
            qdt = _datetime_from_iso_like(getattr(q, "gztime", "") if q else "")
            if qdt is None:
                qdt = now_cn().replace(tzinfo=None)
            out.append({"date": qdt.strftime("%Y-%m-%d %H:%M:%S"), "value": gsz_val})

    deduped = {str(item["date"]): item for item in out}
    return sorted(deduped.values(), key=lambda item: str(item["date"]))


def _load_profit_series(code: str) -> List[dict]:
    rows = get_daily_ledger_items(code)
    out: List[dict] = []
    for it in rows:
        d = str(it.get("date", "")).strip()
        if not d:
            continue
        status = str(it.get("settle_status", "")).strip()
        if status == constants.SETTLE_SETTLED:
            val = _safe_float(it.get("official_pnl"), None)
            if val is None:
                val = _safe_float(it.get("estimated_pnl_close"), None)
        else:
            val = _safe_float(it.get("estimated_pnl_close"), None)
        if val is None:
            continue
        out.append({"date": d, "value": val})

    out.sort(key=lambda x: str(x.get("date", "")))
    return out


def get_chart_data(fund_code: str, chart_type: str, range: str) -> List[dict]:
    code = str(fund_code or "").strip()
    kind = str(chart_type or "").strip().lower()
    range_value = str(range or "1M").strip().upper()
    if not code:
        return []

    if kind == CHART_OFFICIAL_NAV:
        points = get_official_nav_history(code)
    elif kind == CHART_REALTIME_EST:
        points = _load_realtime_series(code)
        return points
    elif kind == CHART_MY_PROFIT:
        points = _load_profit_series(code)
    else:
        points = []

    return _filter_range(points, range_value)
