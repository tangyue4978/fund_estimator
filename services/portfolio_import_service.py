from __future__ import annotations

from typing import Any, Dict, List, Optional

from services.edit_bridge_service import apply_position_edit
from services.estimation_service import estimate_many
from services.fund_service import get_fund_profile
from services.snapshot_service import build_positions_as_of
from services.vision_holdings_service import is_vision_enabled


def holdings_image_import_enabled() -> bool:
    return is_vision_enabled()


def _to_float(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    s = str(v).strip().replace(",", "")
    if not s:
        return None
    try:
        return float(s)
    except Exception:
        return None


def _normalize_pct(v: Any) -> Optional[float]:
    if v is None:
        return None
    s = str(v).strip().replace("%", "").replace(",", "")
    if not s:
        return None
    try:
        return float(s)
    except Exception:
        return None


def _normalize_code(v: Any) -> str:
    raw = str(v or "").strip()
    if not raw:
        return ""
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) >= 6:
        return digits[-6:]
    return raw


def _safe_name(code: str, fallback: str = "") -> str:
    fallback = str(fallback or "").strip()
    try:
        profile = get_fund_profile(code)
        name = str(profile.name or "").strip()
        if name:
            return name
    except Exception:
        pass
    return fallback


def _price_hint(*, row: Dict[str, Any], est_map: Dict[str, Any], code: str, fallback: float = 0.0) -> float:
    for key in ("avg_cost_nav", "avg_price", "price", "unit_price"):
        value = _to_float(row.get(key))
        if value and value > 0:
            return value
    est = est_map.get(code)
    try:
        est_nav = float(est.est_nav or 0.0)
    except Exception:
        est_nav = 0.0
    if est_nav > 0:
        return est_nav
    return fallback if fallback > 0 else 1.0


def combine_recognized_rows(rows: List[Dict[str, Any]], mode: str) -> List[Dict[str, Any]]:
    grouped: Dict[str, Dict[str, Any]] = {}
    for raw in rows or []:
        if not isinstance(raw, dict):
            continue
        code = _normalize_code(raw.get("code"))
        key = code or f"__row_{len(grouped)}"
        cur = grouped.get(key)
        if cur is None:
            item = dict(raw)
            item["code"] = code
            item["_duplicate_count"] = 1
            grouped[key] = item
            continue

        cur["_duplicate_count"] = int(cur.get("_duplicate_count", 1)) + 1
        if mode == "delta":
            for field in ("delta_shares", "delta_amount"):
                left = _to_float(cur.get(field)) or 0.0
                right = _to_float(raw.get(field)) or 0.0
                if abs(right) > 1e-9:
                    cur[field] = left + right
            if not str(cur.get("side", "")).strip() and str(raw.get("side", "")).strip():
                cur["side"] = raw.get("side")
            if not _to_float(cur.get("avg_price")) and _to_float(raw.get("avg_price")):
                cur["avg_price"] = raw.get("avg_price")
        else:
            for field in ("fund_name", "shares", "avg_cost_nav", "amount", "cumulative_pnl", "daily_pnl", "pnl_pct"):
                if cur.get(field) in (None, "", 0, 0.0) and raw.get(field) not in (None, ""):
                    cur[field] = raw.get(field)
    return list(grouped.values())


def _build_sync_row(*, row: Dict[str, Any], snap_map: Dict[str, Any], est_map: Dict[str, Any]) -> Dict[str, Any]:
    code = _normalize_code(row.get("code"))
    name = _safe_name(code, row.get("fund_name"))
    cur = snap_map.get(code)
    cur_shares = float(cur.shares_end) if cur else 0.0
    cur_cost = float(cur.avg_cost_nav_end) if cur else 0.0
    cur_realized = float(cur.realized_pnl_end) if cur else 0.0

    shares = _to_float(row.get("shares"))
    amount = _to_float(row.get("amount"))
    avg_cost = _to_float(row.get("avg_cost_nav"))
    cumulative_pnl = _to_float(row.get("cumulative_pnl"))
    pnl_pct = _normalize_pct(row.get("pnl_pct"))

    errors: List[str] = []
    warnings: List[str] = []

    if not code:
        errors.append("缺少基金代码")

    resolved_shares: Optional[float] = shares
    if resolved_shares is None and amount is not None:
        nav = _price_hint(row=row, est_map=est_map, code=code, fallback=cur_cost)
        if nav <= 0:
            errors.append("无法从持仓金额换算份额")
        else:
            resolved_shares = amount / nav
            warnings.append("份额由持仓金额按当前估值换算")

    if resolved_shares is None:
        errors.append("缺少份额或持仓金额")
        resolved_shares = 0.0

    resolved_avg_cost = avg_cost
    if resolved_avg_cost is None or resolved_avg_cost <= 0:
        if cumulative_pnl is not None and resolved_shares > 0 and amount is not None:
            cost_value = amount - cumulative_pnl
            resolved_avg_cost = cost_value / resolved_shares
            warnings.append("成本净值由持仓金额和累计收益反推")
        elif pnl_pct is not None and resolved_shares > 0 and amount is not None and abs(100.0 + pnl_pct) > 1e-9:
            cost_value = amount / (1.0 + pnl_pct / 100.0)
            resolved_avg_cost = cost_value / resolved_shares
            warnings.append("成本净值由持仓金额和收益率反推")
        elif cur_shares > 0 and cur_cost > 0:
            resolved_avg_cost = cur_cost
            warnings.append("成本净值沿用当前持仓")
        else:
            resolved_avg_cost = _price_hint(row=row, est_map=est_map, code=code, fallback=cur_cost)
            warnings.append("成本净值缺失，已用当前估值/价格兜底")

    if resolved_shares < 0:
        errors.append("目标份额不能为负数")
    if resolved_avg_cost is not None and resolved_avg_cost < 0:
        errors.append("根据收益反推后的成本净值为负数，请手工修正")
    if int(row.get("_duplicate_count", 1) or 1) > 1:
        warnings.append("识别到重复代码，已按单条合并")

    return {
        "code": code,
        "fund_name": name,
        "mode": "sync",
        "current_shares": round(cur_shares, 4),
        "delta_shares": round(float(resolved_shares) - cur_shares, 4),
        "target_shares": round(float(resolved_shares), 4),
        "target_avg_cost_nav": round(float(resolved_avg_cost), 6),
        "target_realized_pnl": round(cur_realized, 4),
        "operation": "覆盖到图片识别持仓",
        "apply": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "recognized_amount": amount,
        "recognized_cumulative_pnl": cumulative_pnl,
        "recognized_pnl_pct": pnl_pct,
    }


def _delta_sign(row: Dict[str, Any]) -> float:
    side = str(row.get("side", "") or "").strip().lower()
    if side in ("sell", "reduce", "minus", "out"):
        return -1.0
    if side in ("buy", "add", "plus", "in"):
        return 1.0
    delta_shares = _to_float(row.get("delta_shares"))
    if delta_shares is not None:
        return -1.0 if delta_shares < 0 else 1.0
    delta_amount = _to_float(row.get("delta_amount"))
    if delta_amount is not None:
        return -1.0 if delta_amount < 0 else 1.0
    return 1.0


def _build_delta_row(*, row: Dict[str, Any], snap_map: Dict[str, Any], est_map: Dict[str, Any]) -> Dict[str, Any]:
    code = _normalize_code(row.get("code"))
    name = _safe_name(code, row.get("fund_name"))
    cur = snap_map.get(code)
    cur_shares = float(cur.shares_end) if cur else 0.0
    cur_cost = float(cur.avg_cost_nav_end) if cur else 0.0
    cur_realized = float(cur.realized_pnl_end) if cur else 0.0

    errors: List[str] = []
    warnings: List[str] = []
    sign = _delta_sign(row)
    delta_shares = _to_float(row.get("delta_shares"))

    if delta_shares is None:
        delta_amount = _to_float(row.get("delta_amount"))
        if delta_amount is not None:
            price = _price_hint(row=row, est_map=est_map, code=code, fallback=cur_cost)
            if price <= 0:
                errors.append("无法从金额换算加减仓份额")
                delta_shares = 0.0
            else:
                delta_shares = abs(delta_amount) / price * sign
                warnings.append("加减仓份额由金额按价格换算")
        else:
            delta_shares = 0.0
            errors.append("缺少加减仓份额或金额")
    else:
        delta_shares = abs(delta_shares) * sign

    trade_price = _price_hint(row=row, est_map=est_map, code=code, fallback=cur_cost)
    target_shares = cur_shares + float(delta_shares)
    if target_shares < -1e-9:
        errors.append("减仓份额超过当前持仓")
        target_shares = 0.0

    if delta_shares >= 0:
        buy_shares = float(delta_shares)
        buy_amount = buy_shares * trade_price
        old_amount = cur_shares * cur_cost
        target_avg_cost = (old_amount + buy_amount) / target_shares if target_shares > 0 else 0.0
        target_realized = cur_realized
        operation = "加仓"
    else:
        sell_shares = min(cur_shares, abs(float(delta_shares)))
        target_avg_cost = cur_cost if target_shares > 0 else 0.0
        target_realized = cur_realized + (trade_price - cur_cost) * sell_shares
        operation = "减仓"

    if not code:
        errors.append("缺少基金代码")
    if int(row.get("_duplicate_count", 1) or 1) > 1:
        warnings.append("识别到重复代码，已按单条合并")

    return {
        "code": code,
        "fund_name": name,
        "mode": "delta",
        "current_shares": round(cur_shares, 4),
        "delta_shares": round(float(delta_shares), 4),
        "target_shares": round(float(target_shares), 4),
        "target_avg_cost_nav": round(float(target_avg_cost), 6),
        "target_realized_pnl": round(float(target_realized), 4),
        "operation": operation,
        "apply": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
    }


def build_import_preview(*, rows: List[Dict[str, Any]], mode: str, effective_date: str) -> Dict[str, Any]:
    normalized_rows = combine_recognized_rows(rows, mode)
    snap_map = {s.code: s for s in build_positions_as_of(effective_date)}
    codes = sorted({_normalize_code(it.get("code")) for it in normalized_rows if _normalize_code(it.get("code"))})
    est_map = estimate_many(codes) if codes else {}

    preview_rows: List[Dict[str, Any]] = []
    seen_codes = set()
    for row in normalized_rows:
        built = _build_delta_row(row=row, snap_map=snap_map, est_map=est_map) if mode == "delta" else _build_sync_row(row=row, snap_map=snap_map, est_map=est_map)
        code = built.get("code", "")
        if code:
            seen_codes.add(code)
        preview_rows.append(built)

    if mode == "sync":
        for code, snap in snap_map.items():
            if code in seen_codes:
                continue
            preview_rows.append(
                {
                    "code": code,
                    "fund_name": _safe_name(code),
                    "mode": "sync",
                    "current_shares": round(float(snap.shares_end), 4),
                    "delta_shares": round(-float(snap.shares_end), 4),
                    "target_shares": 0.0,
                    "target_avg_cost_nav": 0.0,
                    "target_realized_pnl": round(float(snap.realized_pnl_end), 4),
                    "operation": "清零未出现在图片中的原持仓",
                    "apply": True,
                    "errors": [],
                    "warnings": ["同步持仓模式会覆盖未识别到的现有持仓"],
                }
            )

    valid_rows = [it for it in preview_rows if bool(it.get("apply"))]
    error_rows = [it for it in preview_rows if it.get("errors")]
    clear_rows = [it for it in preview_rows if str(it.get("operation", "")).startswith("清零")]
    return {
        "effective_date": effective_date,
        "mode": mode,
        "rows": preview_rows,
        "valid_count": len(valid_rows),
        "error_count": len(error_rows),
        "clear_count": len(clear_rows),
    }


def apply_import_preview(preview: Dict[str, Any]) -> Dict[str, int]:
    rows = preview.get("rows", [])
    if not isinstance(rows, list):
        raise ValueError("preview rows is required")
    effective_date = str(preview.get("effective_date") or "").strip()
    if not effective_date:
        raise ValueError("effective_date is required")

    applied = 0
    skipped = 0
    for row in rows:
        if not isinstance(row, dict) or not bool(row.get("apply")) or row.get("errors"):
            skipped += 1
            continue
        apply_position_edit(
            effective_date=effective_date,
            code=str(row.get("code") or "").strip(),
            shares_end=float(row.get("target_shares", 0.0) or 0.0),
            avg_cost_nav_end=float(row.get("target_avg_cost_nav", 0.0) or 0.0),
            realized_pnl_end=float(row.get("target_realized_pnl", 0.0) or 0.0),
            note=f"图片导入-{preview.get('mode')}",
        )
        applied += 1
    return {"applied": applied, "skipped": skipped}
