from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from datetime import date, timedelta

from storage import paths
from storage.json_store import load_json


@dataclass
class GapRow:
    date: str
    estimated_nav_close: float
    official_nav: float
    gap_nav: float          # official - estimated
    gap_pct: float          # (official/estimated - 1) * 100
    abs_gap_pct: float


def _safe_float(x: Any) -> Optional[float]:
    try:
        if x is None:
            return None
        return float(x)
    except Exception:
        return None


def _read_daily_ledger_items() -> List[dict]:
    """
    读取 daily_ledger.json 的 items
    """
    p = paths.file_daily_ledger()
    data = load_json(p, fallback={"items": []})
    items = data.get("items", [])
    return items if isinstance(items, list) else []


def fund_gap_rows(code: str, days_back: int = 60) -> List[GapRow]:
    """
    从 daily_ledger 聚合：有 estimated_nav_close 且 official_nav 的记录（即已 settled）
    """
    code = (code or "").strip()
    if not code:
        return []

    cutoff = (date.today() - timedelta(days=int(days_back))).isoformat()
    items = _read_daily_ledger_items()

    rows: List[GapRow] = []
    for it in items:
        if str(it.get("code", "")).strip() != code:
            continue
        d = str(it.get("date", "")).strip()
        if not d or d < cutoff:
            continue

        est = _safe_float(it.get("estimated_nav_close"))
        off = _safe_float(it.get("official_nav"))
        status = str(it.get("settle_status", "")).strip()

        # 我们只统计 settled 且两者都存在
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
    """
    汇总统计：MAE（平均绝对误差%）、最大误差、命中率等
    """
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
    mae = sum(abs_list) / len(abs_list)
    max_abs = max(abs_list)

    # “命中率”：误差绝对值 <= 0.30% 视为靠谱（阈值可以后配置）
    hit = sum(1 for v in abs_list if v <= 0.30)
    hit_rate = hit / len(abs_list) * 100.0

    latest = rows[-1]
    return {
        "count": len(rows),
        "mae_pct": mae,
        "max_abs_gap_pct": max_abs,
        "hit_rate_pct": hit_rate,
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
    """
    不依赖外部数据的“规则解释”，给用户可读提示（避免瞎猜）
    这里只做通用原因提示，后续你接入基金类型/标的后可以更精确。
    """
    reasons: List[str] = []

    # 误差很小：说明估算口径稳定
    if latest_abs_gap_pct <= 0.30:
        reasons.append("误差很小：盘中估算与官方净值基本一致，说明该基金估值口径较稳定。")
        return reasons

    # 中等误差
    if latest_abs_gap_pct <= 1.00:
        reasons.append("误差中等：常见于成分股收盘后撮合/指数复核、基金当日申赎/现金头寸变化导致的估算偏差。")

    # 大误差
    if latest_abs_gap_pct > 1.00:
        reasons.append("误差较大：可能存在估值源口径差异或基金本身更难估（例如跨市场资产/复杂持仓/衍生品敞口）。")

    # 通用原因列表
    reasons.extend(
        [
            "若是 ETF：盘中看到的更多是“价格/指数即时变化”，官方净值会受现金、费用、分红、申赎影响，且口径不同。",
            "若基金含港股/美股/QDII：估值会受汇率和海外收盘时间影响，盘中估算更容易偏离。",
            "若当日有分红/拆分/大额申赎：估算与官方净值可能出现结构性跳变。",
            "若官方净值发布延迟：你看到的“晚间更新缩水”其实是从估算口径切换到了官方口径。",
        ]
    )
    return reasons

def portfolio_gap_summary(days_back: int = 60) -> Dict[str, Any]:
    """
    组合层面误差：按日期聚合同一天所有 code 的 estimated_nav_close/official_nav 计算组合总价值误差
    使用 daily_ledger 里的 shares_end 作为权重近似组合价值。
    """
    items = _read_daily_ledger_items()
    cutoff = (date.today() - timedelta(days=int(days_back))).isoformat()

    # date -> list(items)
    by_date: Dict[str, List[dict]] = {}
    for it in items:
        d = str(it.get("date", "")).strip()
        if not d or d < cutoff:
            continue
        by_date.setdefault(d, []).append(it)

    rows = []
    for d, its in by_date.items():
        # 只统计当日全部条目中至少有一条 settled（否则官方口径没意义）
        settled_any = any(str(x.get("settle_status", "")).strip() == "settled" and _safe_float(x.get("official_nav")) is not None for x in its)
        if not settled_any:
            continue

        est_total = 0.0
        off_total = 0.0
        est_ok = False
        off_ok = False

        for x in its:
            sh = _safe_float(x.get("shares_end")) or 0.0
            est = _safe_float(x.get("estimated_nav_close"))
            off = _safe_float(x.get("official_nav"))
            if est is not None:
                est_total += sh * est
                est_ok = True
            if off is not None and str(x.get("settle_status", "")).strip() == "settled":
                off_total += sh * off
                off_ok = True

        if not (est_ok and off_ok) or est_total == 0:
            continue

        gap = off_total - est_total
        gap_pct = (off_total / est_total - 1.0) * 100.0
        rows.append({"date": d, "est_value": est_total, "off_value": off_total, "gap": gap, "gap_pct": gap_pct, "abs_gap_pct": abs(gap_pct)})

    rows.sort(key=lambda r: r["date"])
    if not rows:
        return {"count": 0, "mae_pct": None, "max_abs_gap_pct": None, "hit_rate_pct": None, "latest": None}

    abs_list = [r["abs_gap_pct"] for r in rows]
    mae = sum(abs_list) / len(abs_list)
    max_abs = max(abs_list)
    hit = sum(1 for v in abs_list if v <= 0.30)
    hit_rate = hit / len(abs_list) * 100.0

    latest = rows[-1]
    return {
        "count": len(rows),
        "mae_pct": mae,
        "max_abs_gap_pct": max_abs,
        "hit_rate_pct": hit_rate,
        "latest": latest,
    }


from datetime import timedelta  # 如果文件顶部已有 timedelta 就不用再加


def portfolio_gap_summary(days_back: int = 60) -> Dict[str, Any]:
    """
    组合层面误差：按日期聚合同一天所有 code 的 estimated_nav_close/official_nav 计算组合总价值误差
    使用 daily_ledger 里的 shares_end 作为权重近似组合价值。
    """
    items = _read_daily_ledger_items()
    cutoff = (date.today() - timedelta(days=int(days_back))).isoformat()

    by_date: Dict[str, List[dict]] = {}
    for it in items:
        d = str(it.get("date", "")).strip()
        if not d or d < cutoff:
            continue
        by_date.setdefault(d, []).append(it)

    rows = []
    for d, its in by_date.items():
        # 至少有一条 settled 且 official_nav 存在，才算“官方口径可用”
        settled_any = any(
            str(x.get("settle_status", "")).strip() == "settled"
            and _safe_float(x.get("official_nav")) is not None
            for x in its
        )
        if not settled_any:
            continue

        est_total = 0.0
        off_total = 0.0
        est_ok = False
        off_ok = False

        for x in its:
            sh = _safe_float(x.get("shares_end")) or 0.0
            est = _safe_float(x.get("estimated_nav_close"))
            off = _safe_float(x.get("official_nav"))

            if est is not None:
                est_total += sh * est
                est_ok = True

            if off is not None and str(x.get("settle_status", "")).strip() == "settled":
                off_total += sh * off
                off_ok = True

        if not (est_ok and off_ok) or est_total == 0:
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
    if not rows:
        return {"count": 0, "mae_pct": None, "max_abs_gap_pct": None, "hit_rate_pct": None, "latest": None}

    abs_list = [r["abs_gap_pct"] for r in rows]
    mae = sum(abs_list) / len(abs_list)
    max_abs = max(abs_list)
    hit = sum(1 for v in abs_list if v <= 0.30)
    hit_rate = hit / len(abs_list) * 100.0

    latest = rows[-1]
    return {
        "count": len(rows),
        "mae_pct": mae,
        "max_abs_gap_pct": max_abs,
        "hit_rate_pct": hit_rate,
        "latest": latest,
    }

def fund_gap_table(code: str, days_back: int = 60) -> List[Dict[str, Any]]:
    """
    给 UI 用：返回可直接 DataFrame 的 list[dict]
    """
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
    """
    给 UI 用：组合层面误差历史（按日期）
    """
    items = _read_daily_ledger_items()
    cutoff = (date.today() - timedelta(days=int(days_back))).isoformat()

    by_date: Dict[str, List[dict]] = {}
    for it in items:
        d = str(it.get("date", "")).strip()
        if not d or d < cutoff:
            continue
        by_date.setdefault(d, []).append(it)

    rows: List[Dict[str, Any]] = []
    for d, its in by_date.items():
        settled_any = any(
            str(x.get("settle_status", "")).strip() == "settled"
            and _safe_float(x.get("official_nav")) is not None
            for x in its
        )
        if not settled_any:
            continue

        est_total = 0.0
        off_total = 0.0
        est_ok = False
        off_ok = False

        for x in its:
            sh = _safe_float(x.get("shares_end")) or 0.0
            est = _safe_float(x.get("estimated_nav_close"))
            off = _safe_float(x.get("official_nav"))

            if est is not None:
                est_total += sh * est
                est_ok = True

            if off is not None and str(x.get("settle_status", "")).strip() == "settled":
                off_total += sh * off
                off_ok = True

        if not (est_ok and off_ok) or est_total == 0:
            continue

        gap = off_total - est_total
        gap_pct = (off_total / est_total - 1.0) * 100.0

        rows.append(
            {
                "date": d,
                "estimated_value_close": est_total,
                "official_value": off_total,
                "gap": gap,
                "gap_pct": gap_pct,
                "abs_gap_pct": abs(gap_pct),
            }
        )

    rows.sort(key=lambda r: r["date"])
    return rows
