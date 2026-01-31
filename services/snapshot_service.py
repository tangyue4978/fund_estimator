from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from services.adjustment_service import list_adjustments


@dataclass
class PositionSnapshot:
    code: str
    shares_end: float
    avg_cost_nav_end: float
    realized_pnl_end: float


@dataclass
class SnapshotResult:
    positions: List[PositionSnapshot]
    warnings: List[str]


def build_positions_as_of(target_date: str) -> List[PositionSnapshot]:
    # 兼容旧调用：只返回 positions
    return build_positions_as_of_safe(target_date).positions


def build_positions_as_of_safe(target_date: str) -> SnapshotResult:
    """
    回放到 target_date（含当日）的持仓快照（容错版本）：
    - SELL 超过持仓：截断到当前持仓，不抛异常；写 warning
    """
    adjs = list_adjustments()
    adjs = [a for a in adjs if str(a.get("effective_date")) <= target_date]

    shares: Dict[str, float] = {}
    avg_cost: Dict[str, float] = {}
    realized: Dict[str, float] = {}

    warnings: List[str] = []

    for a in adjs:
        t = str(a.get("type"))
        code = str(a.get("code"))
        sh = float(a.get("shares", 0.0))
        price = float(a.get("price", 0.0))
        cash = float(a.get("cash", 0.0))

        cur_sh = shares.get(code, 0.0)
        cur_avg = avg_cost.get(code, 0.0)
        cur_real = realized.get(code, 0.0)

        if t == "BUY":
            buy_amt = sh * price
            old_amt = cur_sh * cur_avg
            new_sh = cur_sh + sh
            new_avg = (old_amt + buy_amt) / new_sh if new_sh > 0 else 0.0

            shares[code] = new_sh
            avg_cost[code] = new_avg
            realized[code] = cur_real

        elif t == "SELL":
            if sh <= 0 or price <= 0:
                warnings.append(f"忽略无效 SELL：code={code}, shares={sh}, price={price}")
                continue

            # 容错：卖出超过持仓 → 截断
            if sh > cur_sh + 1e-9:
                warnings.append(
                    f"SELL 超过持仓，已截断：code={code}, sell={sh}, hold={cur_sh}, date={a.get('effective_date')}, id={a.get('id')}"
                )
                sh = max(cur_sh, 0.0)

            pnl = (price - cur_avg) * sh
            new_sh = cur_sh - sh
            shares[code] = new_sh
            avg_cost[code] = cur_avg if new_sh > 0 else 0.0
            realized[code] = cur_real + pnl

        elif t == "CASH_ADJ":
            realized[code] = cur_real + cash
            shares[code] = cur_sh
            avg_cost[code] = cur_avg

        else:
            warnings.append(f"未知流水类型：{t}（已跳过） id={a.get('id')}")

    out: List[PositionSnapshot] = []
    for code in sorted(set(list(shares.keys()) + list(realized.keys()))):
        sh = shares.get(code, 0.0)
        rc = realized.get(code, 0.0)
        if sh > 0 or abs(rc) > 1e-9:
            out.append(
                PositionSnapshot(
                    code=code,
                    shares_end=sh,
                    avg_cost_nav_end=avg_cost.get(code, 0.0),
                    realized_pnl_end=rc,
                )
            )

    return SnapshotResult(positions=out, warnings=warnings)
