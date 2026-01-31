from __future__ import annotations

from services.portfolio_service import portfolio_realtime_view_as_of


def main():
    view = portfolio_realtime_view_as_of("2026-01-31")

    print("=== portfolio realtime (snapshot) as_of=2026-01-31 ===")
    print(f"total_cost={view['total_cost']:.4f}")
    print(f"total_est_value={view['total_est_value']:.4f}")
    print(f"total_est_pnl={view['total_est_pnl']:.4f}")
    print(f"total_est_pnl_pct={view['total_est_pnl_pct']:.4f}%")
    print(f"coverage={view['realtime_coverage_value_pct']:.2f}%")

    print("\n--- positions ---")
    for r in view["positions"]:
        print(
            f"{r['code']} shares={r['shares']} cost_nav={r['avg_cost_nav']} realized={r['realized_pnl']} "
            f"est_nav={r['est_nav']} pct={r['est_change_pct']}% method={r['method']} "
            f"value={r['est_value']:.4f} pnl={r['est_pnl']:.4f} pnl_pct={r['est_pnl_pct']:.4f}% "
            f"warn={r['warning']}"
        )


if __name__ == "__main__":
    main()
