from __future__ import annotations

from services.portfolio_service import (
    position_set,
    position_update,
    position_list,
    portfolio_realtime_view,
)


def main():
    print("=== set positions ===")
    # 示例：你可以换成你真实持仓
    position_set("510300", shares=1000, avg_cost_nav=0.98, realized_pnl=0.0, tag="指数ETF")
    position_set("000001", shares=500, avg_cost_nav=1.20, realized_pnl=10.0, tag="示例主动")

    print("\n=== list positions ===")
    for p in position_list():
        print(p)

    print("\n=== update one position (000001 shares -> 600) ===")
    position_update("000001", shares=600)

    print("\n=== portfolio realtime view ===")
    view = portfolio_realtime_view()

    print(f"total_cost={view['total_cost']:.4f}")
    print(f"total_est_value={view['total_est_value']:.4f}")
    print(f"total_est_pnl={view['total_est_pnl']:.4f}")
    print(f"total_est_pnl_pct={view['total_est_pnl_pct']:.4f}%")
    print(f"realtime_coverage_value_pct={view['realtime_coverage_value_pct']:.2f}%")

    print("\n--- positions ---")
    for r in view["positions"]:
        print(
            f"{r['code']} shares={r['shares']} cost_nav={r['avg_cost_nav']} "
            f"est_nav={r['est_nav']} pct={r['est_change_pct']}% "
            f"method={r['method']} conf={r['confidence']} "
            f"value={r['est_value']:.4f} pnl={r['est_pnl']:.4f} pnl_pct={r['est_pnl_pct']:.4f}% "
            f"warn={r['warning']}"
        )


if __name__ == "__main__":
    main()
