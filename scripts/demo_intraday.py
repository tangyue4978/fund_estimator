from __future__ import annotations

import time

from services.estimation_service import estimate_one
from services.portfolio_service import portfolio_realtime_view
from services.intraday_service import (
    record_intraday_point,
    get_intraday_series,
    clear_intraday,
)


def main():
    # 1) 先清空当日数据，避免重复跑造成点太多
    clear_intraday()

    # 2) 模拟采样 10 次：基金 + 组合
    target_code = "510300"
    for i in range(10):
        est = estimate_one(target_code)
        pv = portfolio_realtime_view()

        record_intraday_point(target_code, estimate=est)
        record_intraday_point("portfolio", portfolio_view=pv)

        print(f"[sample {i+1}/10] code={target_code} est_nav={est.est_nav} pnl={pv['total_est_pnl']:.4f}")
        time.sleep(1)

    # 3) 打印曲线点
    print("\n=== intraday series: fund ===")
    pts_fund = get_intraday_series(target_code)
    for p in pts_fund:
        print(p)

    print("\n=== intraday series: portfolio ===")
    pts_pf = get_intraday_series("portfolio")
    for p in pts_pf:
        print(p)


if __name__ == "__main__":
    main()
