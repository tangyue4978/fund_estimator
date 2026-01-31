from __future__ import annotations

from services.adjustment_service import clear_adjustments, list_adjustments
from services.edit_bridge_service import apply_position_edit
from services.snapshot_service import build_positions_as_of


def main():
    clear_adjustments()

    # 假设你在 UI 里这样“编辑”：
    # 1/30：持仓 510300 1000份，成本 4.50，分红导致 realized=5
    apply_position_edit(
        effective_date="2026-01-30",
        code="510300",
        shares_end=1000,
        avg_cost_nav_end=4.50,
        realized_pnl_end=5.0,
        note="UI编辑-1/30",
    )

    # 1/31：持仓变为 800份（卖出200），成本仍 4.50，最终 realized=45
    apply_position_edit(
        effective_date="2026-01-31",
        code="510300",
        shares_end=800,
        avg_cost_nav_end=4.50,
        realized_pnl_end=45.0,
        note="UI编辑-1/31",
    )

    print("=== adjustments generated ===")
    for a in list_adjustments():
        print(a)

    print("\n=== snapshots ===")
    for d in ["2026-01-30", "2026-01-31"]:
        print(f"\n-- {d} --")
        for s in build_positions_as_of(d):
            print(s)


if __name__ == "__main__":
    main()
