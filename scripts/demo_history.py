from __future__ import annotations

from services.history_service import get_history, get_portfolio_history


def main():
    code = "510300"

    print(f"=== fund history: {code} ===")
    rows = get_history(code, days=90)
    for r in rows:
        print(r)

    print("\n=== portfolio history ===")
    pf = get_portfolio_history(days=90)
    for r in pf:
        # 展示时顺手 round 一下，避免浮点长尾
        r2 = dict(r)
        r2["total_cost"] = round(r2["total_cost"], 6)
        r2["total_value"] = round(r2["total_value"], 6)
        r2["total_pnl"] = round(r2["total_pnl"], 6)
        r2["total_pnl_pct"] = round(r2["total_pnl_pct"], 6)
        print(r2)


if __name__ == "__main__":
    main()
