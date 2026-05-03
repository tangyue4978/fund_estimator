from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from services.adjustment_service import list_adjustments
from services.history_service import get_portfolio_history
from services.settlement_service import get_ledger_items
from services.snapshot_service import build_positions_as_of_safe
from services.trading_time import now_cn
from storage import paths
from storage.json_store import ensure_json_file_with_schema, save_json


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _target_allocations_path() -> str:
    return str(Path(paths.user_data_dir()) / "target_allocations.json")


def load_target_allocations() -> Dict[str, float]:
    data = ensure_json_file_with_schema(_target_allocations_path(), {"items": {}})
    items = data.get("items", {})
    if not isinstance(items, dict):
        return {}
    out: Dict[str, float] = {}
    for code, pct in items.items():
        code_s = str(code or "").strip()
        if not code_s:
            continue
        value = max(0.0, _safe_float(pct))
        if value > 0:
            out[code_s] = value
    return out


def save_target_allocations(items: Dict[str, Any]) -> Dict[str, float]:
    normalized: Dict[str, float] = {}
    for code, pct in (items or {}).items():
        code_s = str(code or "").strip()
        if not code_s:
            continue
        value = max(0.0, round(_safe_float(pct), 4))
        if value > 0:
            normalized[code_s] = value
    save_json(_target_allocations_path(), {"items": normalized, "updated_at": now_cn().isoformat(timespec="seconds")})
    return normalized


def portfolio_nav_curve(days: int = 180) -> List[dict]:
    rows = get_portfolio_history(days=days)
    out: List[dict] = []
    prev_value = 0.0
    base_value = 0.0
    for row in rows:
        value = _safe_float(row.get("total_value"))
        if value <= 0:
            continue
        if base_value <= 0:
            base_value = value
        daily_return_pct = ((value / prev_value - 1.0) * 100.0) if prev_value > 0 else 0.0
        out.append(
            {
                "date": str(row.get("date", "")),
                "portfolio_index": (value / base_value * 100.0) if base_value > 0 else 100.0,
                "total_value": value,
                "total_cost": _safe_float(row.get("total_cost")),
                "total_pnl": _safe_float(row.get("total_pnl")),
                "total_pnl_pct": _safe_float(row.get("total_pnl_pct")),
                "daily_return_pct": daily_return_pct,
                "source": row.get("source", ""),
                "settle_status": row.get("settle_status", ""),
            }
        )
        prev_value = value
    return out


def portfolio_attribution_rows(view: dict) -> List[dict]:
    positions = view.get("positions", []) if isinstance(view, dict) else []
    if not isinstance(positions, list):
        return []

    total_value = sum(_safe_float(p.get("est_value")) for p in positions if isinstance(p, dict))
    today_items: List[tuple[dict, float]] = []
    total_today = 0.0
    for pos in positions:
        if not isinstance(pos, dict):
            continue
        value = _safe_float(pos.get("est_value"))
        pct = _safe_float(pos.get("est_change_pct"))
        denom = 100.0 + pct
        today_pnl = value * pct / denom if abs(denom) > 1e-9 else 0.0
        today_items.append((pos, today_pnl))
        total_today += today_pnl

    out: List[dict] = []
    for pos, today_pnl in today_items:
        value = _safe_float(pos.get("est_value"))
        cost = _safe_float(pos.get("shares")) * _safe_float(pos.get("avg_cost_nav"))
        est_pnl = _safe_float(pos.get("est_pnl"))
        out.append(
            {
                "code": str(pos.get("code", "")),
                "weight_pct": (value / total_value * 100.0) if total_value > 0 else 0.0,
                "est_value": value,
                "today_pnl": today_pnl,
                "today_contribution_pct": (today_pnl / total_today * 100.0) if abs(total_today) > 1e-9 else 0.0,
                "total_pnl": est_pnl,
                "total_pnl_pct": (est_pnl / cost * 100.0) if cost > 0 else 0.0,
                "confidence": _safe_float(pos.get("confidence")),
                "warning": str(pos.get("warning", "") or ""),
            }
        )
    out.sort(key=lambda x: abs(_safe_float(x.get("today_pnl"))), reverse=True)
    return out


def target_allocation_rows(view: dict, targets: Dict[str, float] | None = None) -> List[dict]:
    positions = view.get("positions", []) if isinstance(view, dict) else []
    targets = targets if targets is not None else load_target_allocations()
    total_value = sum(_safe_float(p.get("est_value")) for p in positions if isinstance(p, dict))
    rows: List[dict] = []
    seen = set()
    for pos in positions if isinstance(positions, list) else []:
        if not isinstance(pos, dict):
            continue
        code = str(pos.get("code", "") or "").strip()
        if not code:
            continue
        seen.add(code)
        value = _safe_float(pos.get("est_value"))
        current_pct = (value / total_value * 100.0) if total_value > 0 else 0.0
        target_pct = _safe_float(targets.get(code))
        rows.append(
            {
                "code": code,
                "current_pct": current_pct,
                "target_pct": target_pct,
                "deviation_pct": current_pct - target_pct,
                "deviation_amount": total_value * (current_pct - target_pct) / 100.0,
                "est_value": value,
            }
        )
    for code, target_pct in targets.items():
        if code in seen:
            continue
        rows.append(
            {
                "code": code,
                "current_pct": 0.0,
                "target_pct": _safe_float(target_pct),
                "deviation_pct": -_safe_float(target_pct),
                "deviation_amount": -total_value * _safe_float(target_pct) / 100.0,
                "est_value": 0.0,
            }
        )
    rows.sort(key=lambda x: abs(_safe_float(x.get("deviation_amount"))), reverse=True)
    return rows


def portfolio_health_check(days_back: int = 7) -> List[dict]:
    issues: List[dict] = []
    today = now_cn().date().isoformat()

    snapshot = build_positions_as_of_safe(today)
    for warning in snapshot.warnings:
        issues.append({"level": "warning", "scope": "持仓流水", "message": str(warning), "suggestion": "检查并删除异常流水后重新日结。"})

    adjustments = list_adjustments()
    seen_adjustments = {}
    for item in adjustments:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code", "") or "").strip()
        adj_type = str(item.get("type", "") or "").strip().upper()
        shares = _safe_float(item.get("shares"))
        price = _safe_float(item.get("price"))
        cash = _safe_float(item.get("cash"))
        date_s = str(item.get("effective_date", "") or "").strip()
        if not code:
            issues.append({"level": "error", "scope": "持仓流水", "message": f"流水缺少基金代码：id={item.get('id')}", "suggestion": "删除或修正该流水。"})
        if adj_type in {"BUY", "SELL"} and (shares <= 0 or price <= 0):
            issues.append({"level": "error", "scope": "持仓流水", "message": f"{code} {date_s} 的 {adj_type} 流水份额或价格无效。", "suggestion": "删除后重新录入。"})
        if adj_type == "CASH_ADJ" and abs(cash) <= 1e-12:
            issues.append({"level": "info", "scope": "持仓流水", "message": f"{code} {date_s} 存在金额为 0 的现金调整。", "suggestion": "如无实际用途可以删除。"})

        dedupe_key = (adj_type, code, date_s, round(shares, 8), round(price, 8), round(cash, 8), str(item.get("source", "")), str(item.get("note", "")))
        seen_adjustments.setdefault(dedupe_key, []).append(str(item.get("id", "")))

    for key, ids in seen_adjustments.items():
        if len(ids) > 1:
            _, code, date_s, *_ = key
            issues.append({"level": "warning", "scope": "持仓流水", "message": f"{code} {date_s} 疑似重复流水 {len(ids)} 条。", "suggestion": "确认是否重复录入，必要时删除多余记录。"})

    ledger_items = get_ledger_items()
    cutoff = ""
    try:
        from datetime import timedelta

        cutoff = (now_cn().date() - timedelta(days=max(1, int(days_back)) - 1)).isoformat()
    except Exception:
        cutoff = today

    pending_count = 0
    for row in ledger_items:
        if not isinstance(row, dict):
            continue
        date_s = str(row.get("date", "") or "").strip()
        code = str(row.get("code", "") or "").strip()
        if _safe_float(row.get("shares_end")) < 0:
            issues.append({"level": "error", "scope": "日结台账", "message": f"{code} {date_s} 日结份额为负。", "suggestion": "检查该日期之前的买卖流水。"})
        if date_s >= cutoff and str(row.get("settle_status", "")) == "estimated_only":
            pending_count += 1
        if date_s >= cutoff and _safe_float(row.get("estimated_nav_close")) <= 0:
            issues.append({"level": "warning", "scope": "日结台账", "message": f"{code} {date_s} 缺少有效收盘估值。", "suggestion": "重新生成当日收盘估算。"})

    if pending_count:
        issues.append({"level": "info", "scope": "日结台账", "message": f"最近 {days_back} 天仍有 {pending_count} 条未覆盖官方净值的日结记录。", "suggestion": "在日结页执行扫描结算。"})

    target_sum = sum(load_target_allocations().values())
    if target_sum > 0 and abs(target_sum - 100.0) > 0.01:
        issues.append({"level": "info", "scope": "目标仓位", "message": f"目标仓位合计为 {target_sum:.2f}%，不是 100%。", "suggestion": "按组合管理口径调整目标比例。"})

    if not issues:
        issues.append({"level": "success", "scope": "系统", "message": "未发现明显数据异常。", "suggestion": "保持日结和官方净值覆盖即可。"})
    return issues
