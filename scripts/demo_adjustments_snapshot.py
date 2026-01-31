from __future__ import annotations

from datetime import date, timedelta

from services.adjustment_service import clear_adjustments, add_adjustment, list_adjustments
from services.snapshot_service import build_positions_as_of


def main():
    clear_adjustments()

    # 假设：1/29 买入 510300 1000 份，成本 4.50
    add_adjustment(type="BUY", code="510300", effective_date="2026-01-29", shares=1000, price=4.50, note="买入")

    # 1/30 分红/现金修正 +5
    add_adjustment(type="CASH_ADJ", code="510300", effective_date="2026-01-30", cash=5.0, note="分红")

    # 1/31 卖出 200 份，成交 4.70
    add_adjustment(type="SELL", code="510300", effective_date="2026-01-31", shares=200, price=4.70, note="卖出部分")

    print("=== adjustments ===")
    for a in list_adjustments():
        print(a)

    for d in ["2026-01-29", "2026-01-30", "2026-01-31"]:
        print(f"\n=== snapshot as of {d} ===")
        snaps = build_positions_as_of(d)
        for s in snaps:
            print(s)


if __name__ == "__main__":
    main()
