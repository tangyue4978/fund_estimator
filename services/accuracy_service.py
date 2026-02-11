from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Dict, List, Optional

from services import supabase_client
from services.trading_time import now_cn
from storage import paths
from storage.json_store import load_json


def _strict_web_cloud_mode() -> bool:
    return bool(os.getenv("STREAMLIT_SHARING_MODE", "").strip())


@dataclass
class GapRow:
    date: str
    estimated_nav_close: float
    official_nav: float
    gap_nav: float
    gap_pct: float
    abs_gap_pct: float


def _safe_float(x: Any) -> Optional[float]:
    try:
        if x is None:
            return None
        return float(x)
    except Exception:
        return None


def _read_daily_ledger_items() -> List[dict]:
    if _strict_web_cloud_mode() and (not supabase_client.is_enabled()):
        return []
    if supabase_client.is_enabled():
        try:
            rows = supabase_client.get_rows(
                "app_daily_ledger",
                params={
                    "user_id": f"eq.{paths.current_user_id()}",
                    "select": "date,code,shares_end,estimated_nav_close,official_nav,settle_status",
                    "order": "date.asc,code.asc",
                },
            )
            return [x for x in rows if isinstance(x, dict)]
        except Exception:
            if _strict_web_cloud_mode():
                return []

    data = load_json(paths.file_daily_ledger(), fallback={"items": []})
    items = data.get("items", []) if isinstance(data, dict) else []
    return items if isinstance(items, list) else []


def fund_gap_rows(code: str, days_back: int = 60) -> List[GapRow]:
    code = (code or "").strip()
    if not code:
        return []

    cutoff = (now_cn().date() - timedelta(days=int(days_back))).isoformat()
    items = _read_daily_ledger_items()

    rows: List[GapRow] = []
    for it in items:
        if str(it.get("code", "")).strip() != code:
            continue
        d = str(it.get("date", "")).strip()
        if (not d) or d < cutoff:
            continue

        est = _safe_float(it.get("estimated_nav_close"))
        off = _safe_float(it.get("official_nav"))
        status = str(it.get("settle_status", "")).strip()
        if status != "settled" or est is None or off is None or est == 0:
            continue

        gap_nav = off - est
        gap_pct = (off / est - 1.0) * 100.0
        rows.append(
            GapRow(
                date=d,
                estimated_nav_close=est,
                official_nav=off,
                gap_nav=gap_nav,
                gap_pct=gap_pct,
                abs_gap_pct=abs(gap_pct),
            )
        )

    rows.sort(key=lambda r: r.date)
    return rows


def fund_gap_summary(code: str, days_back: int = 60) -> Dict[str, Any]:
    rows = fund_gap_rows(code, days_back=days_back)
    if not rows:
        return {
            "count": 0,
            "mae_pct": None,
            "max_abs_gap_pct": None,
            "hit_rate_pct": None,
            "latest": None,
        }

    abs_list = [r.abs_gap_pct for r in rows]
    latest = rows[-1]
    return {
        "count": len(rows),
        "mae_pct": sum(abs_list) / len(abs_list),
        "max_abs_gap_pct": max(abs_list),
        "hit_rate_pct": (sum(1 for v in abs_list if v <= 0.30) / len(abs_list) * 100.0),
        "latest": {
            "date": latest.date,
            "estimated_nav_close": latest.estimated_nav_close,
            "official_nav": latest.official_nav,
            "gap_nav": latest.gap_nav,
            "gap_pct": latest.gap_pct,
            "abs_gap_pct": latest.abs_gap_pct,
        },
    }


def guess_gap_reasons(code: str, latest_abs_gap_pct: float) -> List[str]:
    _ = code
    reasons: List[str] = []
    if latest_abs_gap_pct <= 0.30:
        return ["误差较小，盘中估算与官方净值基本一致。"]
    if latest_abs_gap_pct <= 1.00:
        reasons.append("误差中等，常见于收盘后口径校准、成分与现金头寸变化。")
    else:
        reasons.append("误差较大，可能是估值口径差异或基金资产结构较复杂。")

    reasons.extend(
        [
            "ETF 盘中价格与官方净值计算口径不同，可能带来偏差。",
            "含境外资产基金会受汇率与时区影响，盘中偏差更常见。",
            "分红、拆分、申赎等事件会导致估算与官方值出现结构性差异。",
            "官方净值发布时间晚于盘中估算，晚间覆盖后会看到误差收敛或放大。",
        ]
    )
    return reasons


def _portfolio_gap_rows(days_back: int = 60) -> List[Dict[str, Any]]:
    cutoff = (now_cn().date() - timedelta(days=int(days_back))).isoformat()
    items = _read_daily_ledger_items()

    by_date: Dict[str, List[dict]] = {}
    for it in items:
        d = str(it.get("date", "")).strip()
        if (not d) or d < cutoff:
            continue
        by_date.setdefault(d, []).append(it)

    rows: List[Dict[str, Any]] = []
    for d, day_items in by_date.items():
        est_total = 0.0
        off_total = 0.0
        include_day = True

        for it in day_items:
            status = str(it.get("settle_status", "")).strip()
            sh = _safe_float(it.get("shares_end")) or 0.0
            est = _safe_float(it.get("estimated_nav_close"))
            off = _safe_float(it.get("official_nav"))

            # Ensure same-caliber comparison. Any missing official/settled row excludes that day.
            if status != "settled" or est is None or off is None:
                include_day = False
                break

            est_total += sh * est
            off_total += sh * off

        if (not include_day) or est_total == 0:
            continue

        gap = off_total - est_total
        gap_pct = (off_total / est_total - 1.0) * 100.0
        rows.append(
            {
                "date": d,
                "est_value": est_total,
                "off_value": off_total,
                "gap": gap,
                "gap_pct": gap_pct,
                "abs_gap_pct": abs(gap_pct),
            }
        )

    rows.sort(key=lambda r: r["date"])
    return rows


def portfolio_gap_summary(days_back: int = 60) -> Dict[str, Any]:
    rows = _portfolio_gap_rows(days_back=days_back)
    if not rows:
        return {"count": 0, "mae_pct": None, "max_abs_gap_pct": None, "hit_rate_pct": None, "latest": None}

    abs_list = [r["abs_gap_pct"] for r in rows]
    return {
        "count": len(rows),
        "mae_pct": sum(abs_list) / len(abs_list),
        "max_abs_gap_pct": max(abs_list),
        "hit_rate_pct": (sum(1 for v in abs_list if v <= 0.30) / len(abs_list) * 100.0),
        "latest": rows[-1],
    }


def fund_gap_table(code: str, days_back: int = 60) -> List[Dict[str, Any]]:
    rows = fund_gap_rows(code, days_back=days_back)
    return [
        {
            "date": r.date,
            "estimated_nav_close": r.estimated_nav_close,
            "official_nav": r.official_nav,
            "gap_nav": r.gap_nav,
            "gap_pct": r.gap_pct,
            "abs_gap_pct": r.abs_gap_pct,
        }
        for r in rows
    ]


def portfolio_gap_table(days_back: int = 60) -> List[Dict[str, Any]]:
    rows = _portfolio_gap_rows(days_back=days_back)
    return [
        {
            "date": r["date"],
            "estimated_value_close": r["est_value"],
            "official_value": r["off_value"],
            "gap": r["gap"],
            "gap_pct": r["gap_pct"],
            "abs_gap_pct": r["abs_gap_pct"],
        }
        for r in rows
    ]
