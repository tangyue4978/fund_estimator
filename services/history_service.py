from __future__ import annotations

from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

from config import constants
from storage import paths
from storage.json_store import ensure_json_file


def _load_ledger_items() -> List[dict]:
    p = paths.file_daily_ledger()
    res = ensure_json_file(p)
    data = res.data if isinstance(res.data, dict) else {}
    items = data.get("items", [])
    return items if isinstance(items, list) else []


def get_history(code: str, days: int = 90) -> List[dict]:
    """
    单只基金历史走势（近 N 天）：
    - 优先使用 settled 的 official_nav
    - 若某天仅 estimated_only：可返回 estimated_nav_close 并标记 source='estimated'
    - 返回按 date 升序
    """
    code = (code or "").strip()
    if not code:
        raise ValueError("code is required")

    if days <= 0:
        return []

    start = (date.today() - timedelta(days=days - 1)).isoformat()
    end = date.today().isoformat()

    items = _load_ledger_items()
    rows: List[dict] = []

    for it in items:
        if str(it.get("code")) != code:
            continue
        d = str(it.get("date"))
        if d < start or d > end:
            continue

        status = it.get("settle_status", constants.SETTLE_ESTIMATED_ONLY)
        if status == constants.SETTLE_SETTLED and it.get("official_nav") is not None:
            rows.append(
                {
                    "date": d,
                    "nav": float(it["official_nav"]),
                    "source": "official",
                    "settle_status": status,
                }
            )
        else:
            # 未结算：用 estimated_nav_close（可用于灰/虚线点）
            rows.append(
                {
                    "date": d,
                    "nav": float(it.get("estimated_nav_close", 0.0)),
                    "source": "estimated",
                    "settle_status": status,
                }
            )

    rows.sort(key=lambda x: x["date"])
    return rows


def get_portfolio_history(days: int = 90) -> List[dict]:
    """
    组合历史（MVP）：
    - 从 daily_ledger 按日期聚合：sum(official_pnl) / sum(cost) 之类口径
    - 优先 official（若某日有任一项未结算，则该日标记 estimated）
    返回字段：
      date, total_pnl, total_value(可选), source, settle_status
    """
    if days <= 0:
        return []

    start = (date.today() - timedelta(days=days - 1)).isoformat()
    end = date.today().isoformat()

    items = _load_ledger_items()

    by_date: Dict[str, List[dict]] = {}
    for it in items:
        d = str(it.get("date"))
        if d < start or d > end:
            continue
        by_date.setdefault(d, []).append(it)

    out: List[dict] = []
    for d, lst in by_date.items():
        # 如果当日所有记录都 settled，则用 official；否则 estimated
        all_settled = all(x.get("settle_status") == constants.SETTLE_SETTLED for x in lst)

        total_cost = 0.0
        total_value = 0.0
        total_pnl = 0.0

        for it in lst:
            shares = float(it.get("shares_end", 0.0))
            cost_nav = float(it.get("avg_cost_nav_end", 0.0))
            realized = float(it.get("realized_pnl_end", 0.0))

            cost = shares * cost_nav
            total_cost += cost

            if all_settled and it.get("official_nav") is not None:
                nav = float(it["official_nav"])
                value = shares * nav
                pnl = value - cost + realized
            else:
                nav = float(it.get("estimated_nav_close", 0.0))
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

# =========================
# 标准导出（给 Fund Detail 用）
# =========================

def fund_history(code: str, days_back: int = 60):
    """
    返回基金历史净值列表（优先 official，其次 estimated）
    输出 list[dict]，字段：
      - date: YYYY-MM-DD
      - nav: 数值
      - source: 'official' / 'estimated'
      - settle_status: 'settled' / 'estimated_only'
    数据源（MVP）：从 daily_ledger.json 聚合（你已在日结里写入）。
    """
    code = (code or "").strip()
    if not code:
        return []

    # 1) 若你 history_service 里已有类似函数，优先调用（兼容旧命名）
    if "get_fund_history" in globals():
        return get_fund_history(code, days_back=days_back)
    if "history_of_fund" in globals():
        return history_of_fund(code, days_back=days_back)

    # 2) 保底：从 daily_ledger 聚合
    try:
        from datetime import date, timedelta
        from storage import paths
        from storage.json_store import load_json

        cutoff = (date.today() - timedelta(days=int(days_back))).isoformat()

        data = load_json(paths.file_daily_ledger(), fallback={"items": []})
        items = data.get("items", [])
        if not isinstance(items, list):
            return []

        rows = []
        for it in items:
            if str(it.get("code")) != code:
                continue
            d = str(it.get("date", ""))
            if d and d < cutoff:
                continue

            status = str(it.get("settle_status", ""))
            official_nav = it.get("official_nav", None)
            est_nav = it.get("estimated_nav_close", None)

            if status == "settled" and official_nav is not None:
                rows.append({"date": d, "nav": float(official_nav), "source": "official", "settle_status": status})
            elif est_nav is not None:
                rows.append({"date": d, "nav": float(est_nav), "source": "estimated", "settle_status": status})

        # 按日期升序
        rows.sort(key=lambda x: x.get("date", ""))
        return rows

    except Exception:
        return []
