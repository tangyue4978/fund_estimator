from __future__ import annotations

from datetime import date, timedelta
from typing import Optional, Dict

from services.snapshot_service import build_positions_as_of
from services.adjustment_service import add_adjustment


def _get_snapshot_map(d: str) -> Dict[str, dict]:
    snaps = build_positions_as_of(d)
    mp = {}
    for s in snaps:
        mp[s.code] = {
            "shares_end": float(s.shares_end),
            "avg_cost_nav_end": float(s.avg_cost_nav_end),
            "realized_pnl_end": float(s.realized_pnl_end),
        }
    return mp


def apply_position_edit(
    *,
    effective_date: str,
    code: str,
    shares_end: float,
    avg_cost_nav_end: float,
    realized_pnl_end: float = 0.0,
    note: Optional[str] = None,
) -> None:
    """
    把“编辑持仓结果”转成流水：
    对比 effective_date 的前一日快照（<=前一日）与当前编辑目标，生成 BUY/SELL/CASH_ADJ。

    约定（MVP）：
    - BUY/SELL 的 price 使用 avg_cost_nav_end（即你输入的成本净值）
    - realized 差额用 CASH_ADJ 补齐
    """
    code = (code or "").strip()
    if not code:
        raise ValueError("code is required")

    if shares_end < 0:
        raise ValueError("shares_end must be >= 0")
    if avg_cost_nav_end < 0:
        raise ValueError("avg_cost_nav_end must be >= 0")

    # 前一日
    d = date.fromisoformat(effective_date)
    prev = (d - timedelta(days=1)).isoformat()

    prev_map = _get_snapshot_map(prev)
    prev_pos = prev_map.get(code, {"shares_end": 0.0, "avg_cost_nav_end": 0.0, "realized_pnl_end": 0.0})

    prev_sh = float(prev_pos["shares_end"])
    prev_real = float(prev_pos["realized_pnl_end"])

    # 1) shares 差异 → BUY/SELL
    delta_sh = float(shares_end) - prev_sh
    if abs(delta_sh) > 1e-9:
        if delta_sh > 0:
            add_adjustment(
                type="BUY",
                code=code,
                effective_date=effective_date,
                shares=delta_sh,
                price=float(avg_cost_nav_end) if avg_cost_nav_end > 0 else 1.0,
                note=note or "edit->BUY",
            )
        else:
            add_adjustment(
                type="SELL",
                code=code,
                effective_date=effective_date,
                shares=abs(delta_sh),
                price=float(avg_cost_nav_end) if avg_cost_nav_end > 0 else 1.0,
                note=note or "edit->SELL",
            )

    # 2) realized 差异 → CASH_ADJ
    # 注意：卖出会自动产生 realized（根据 SELL price 与 avg_cost），但我们这里用 CASH_ADJ 做“最终对齐”
    # 所以先计算当前回放到 effective_date 的 realized（含刚写入的 BUY/SELL）
    cur_map = _get_snapshot_map(effective_date)
    cur_real_after = float(cur_map.get(code, {}).get("realized_pnl_end", 0.0))

    target_real = float(realized_pnl_end)
    delta_real = target_real - cur_real_after
    if abs(delta_real) > 1e-6:
        add_adjustment(
            type="CASH_ADJ",
            code=code,
            effective_date=effective_date,
            cash=delta_real,
            note=note or "edit->CASH_ADJ",
        )
