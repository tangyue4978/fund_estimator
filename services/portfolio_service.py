from __future__ import annotations

from datetime import datetime, date
from typing import Dict, List, Optional, Any

from storage import paths
from storage.json_store import ensure_json_file, update_json
from domain.position import Position
from services.estimation_service import estimate_many
from services.snapshot_service import build_positions_as_of


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _load_portfolio_raw() -> dict:
    p = paths.file_portfolio()
    res = ensure_json_file(p)
    data = res.data if isinstance(res.data, dict) else {}
    if "positions" not in data or not isinstance(data.get("positions"), dict):
        data["positions"] = {}
    return data


# ---------------- 旧：直接维护 portfolio.json（兼容保留） ----------------
def position_set(
    code: str,
    shares: float,
    avg_cost_nav: float,
    realized_pnl: float = 0.0,
    tag: Optional[str] = None,
    note: Optional[str] = None,
) -> dict:
    code = (code or "").strip()
    if not code:
        raise ValueError("code is required")
    if shares < 0:
        raise ValueError("shares must be >= 0")
    if avg_cost_nav < 0:
        raise ValueError("avg_cost_nav must be >= 0")

    p = paths.file_portfolio()

    def updater(data: dict):
        positions = data.get("positions", {})
        positions[code] = {
            "code": code,
            "shares": float(shares),
            "avg_cost_nav": float(avg_cost_nav),
            "realized_pnl": float(realized_pnl),
            "tag": tag,
            "note": note,
            "updated_at": _now_iso(),
        }
        data["positions"] = positions
        data["updated_at"] = _now_iso()
        return data

    return update_json(p, updater)


def position_update(
    code: str,
    shares: Optional[float] = None,
    avg_cost_nav: Optional[float] = None,
    realized_pnl: Optional[float] = None,
    tag: Optional[str] = None,
    note: Optional[str] = None,
) -> dict:
    code = (code or "").strip()
    if not code:
        raise ValueError("code is required")

    p = paths.file_portfolio()

    def updater(data: dict):
        positions = data.get("positions", {})
        cur = positions.get(code)
        if not cur:
            raise KeyError(f"position not found: {code}")

        if shares is not None:
            if shares < 0:
                raise ValueError("shares must be >= 0")
            cur["shares"] = float(shares)
        if avg_cost_nav is not None:
            if avg_cost_nav < 0:
                raise ValueError("avg_cost_nav must be >= 0")
            cur["avg_cost_nav"] = float(avg_cost_nav)
        if realized_pnl is not None:
            cur["realized_pnl"] = float(realized_pnl)
        if tag is not None:
            cur["tag"] = tag
        if note is not None:
            cur["note"] = note

        cur["updated_at"] = _now_iso()
        positions[code] = cur
        data["positions"] = positions
        data["updated_at"] = _now_iso()
        return data

    return update_json(p, updater)


def position_remove(code: str) -> dict:
    code = (code or "").strip()
    if not code:
        raise ValueError("code is required")

    p = paths.file_portfolio()

    def updater(data: dict):
        positions = data.get("positions", {})
        if code in positions:
            positions.pop(code, None)
        data["positions"] = positions
        data["updated_at"] = _now_iso()
        return data

    return update_json(p, updater)


def position_list() -> List[Position]:
    data = _load_portfolio_raw()
    positions = data.get("positions", {})
    result: List[Position] = []
    for code, obj in positions.items():
        result.append(
            Position(
                code=str(obj.get("code", code)),
                shares=float(obj.get("shares", 0.0)),
                avg_cost_nav=float(obj.get("avg_cost_nav", 0.0)),
                realized_pnl=float(obj.get("realized_pnl", 0.0)),
                tag=obj.get("tag"),
                note=obj.get("note"),
                updated_at=obj.get("updated_at"),
            )
        )
    result.sort(key=lambda x: x.code)
    return result


# ---------------- 新：以 adjustments 回放快照为准（主口径） ----------------
def portfolio_realtime_view_as_of(date_str: Optional[str] = None) -> dict:
    """
    组合实时视图：以流水回放快照为准（推荐）。
    """
    d = date_str or date.today().isoformat()

    snaps = build_positions_as_of(d)
    codes = [s.code for s in snaps]
    if not codes:
        return {
            "positions": [],
            "total_cost": 0.0,
            "total_est_value": 0.0,
            "total_est_pnl": 0.0,
            "total_est_pnl_pct": 0.0,
            "realtime_coverage_value_pct": 0.0,
            "as_of": d,
        }

    est_map = estimate_many(codes)

    rows: List[Dict[str, Any]] = []
    total_cost = 0.0
    total_value = 0.0
    total_pnl = 0.0
    covered_value = 0.0

    for s in snaps:
        est = est_map.get(s.code)
        est_nav = est.est_nav if est else 0.0

        shares = float(s.shares_end)
        cost_nav = float(s.avg_cost_nav_end)
        realized = float(s.realized_pnl_end)

        cost = shares * cost_nav
        value = shares * est_nav
        pnl = value - cost + realized

        total_cost += cost
        total_value += value
        total_pnl += pnl

        if est and est_nav > 0:
            covered_value += value

        rows.append(
            {
                "code": s.code,
                "shares": shares,
                "avg_cost_nav": cost_nav,
                "realized_pnl": realized,
                "est_nav": est_nav,
                "est_change_pct": (est.est_change_pct if est else 0.0),
                "method": (est.method if est else ""),
                "confidence": (est.confidence if est else 0.0),
                "warning": (est.warning if est else "无估值数据"),
                "est_time": (est.est_time if est else ""),
                "est_value": value,
                "est_pnl": pnl,
                "est_pnl_pct": (pnl / cost * 100.0) if cost > 0 else 0.0,
            }
        )

    coverage = (covered_value / total_value * 100.0) if total_value > 0 else 0.0
    total_pnl_pct = (total_pnl / total_cost * 100.0) if total_cost > 0 else 0.0

    return {
        "positions": rows,
        "total_cost": total_cost,
        "total_est_value": total_value,
        "total_est_pnl": total_pnl,
        "total_est_pnl_pct": total_pnl_pct,
        "realtime_coverage_value_pct": coverage,
        "as_of": d,
    }


def portfolio_realtime_view() -> dict:
    """
    兼容旧调用：默认返回“今天 as_of”的快照计算结果。
    """
    return portfolio_realtime_view_as_of()
