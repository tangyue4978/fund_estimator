from __future__ import annotations

from datetime import date, timedelta

from services.settlement_service import finalize_estimated_close, settle_day, settle_pending_days
from storage import paths
from storage.json_store import load_json


def main():
    today = date.today().isoformat()
    yday = (date.today() - timedelta(days=1)).isoformat()

    # 1) 生成今天/昨天的 estimated_only 日结
    print(f"=== finalize_estimated_close({today}) ===")
    finalize_estimated_close(today)

    print(f"\n=== finalize_estimated_close({yday}) ===")
    finalize_estimated_close(yday)

    # 2) 尝试结算今天（mock 默认不给今天净值，所以一般为 0）
    print(f"\n=== settle_day({today}) ===")
    _, cnt_today = settle_day(today)
    print("settled_count_today =", cnt_today)

    # 3) 尝试结算昨天（mock 会给昨天净值 -> 预期 >0）
    print(f"\n=== settle_day({yday}) ===")
    _, cnt_yday = settle_day(yday)
    print("settled_count_yday =", cnt_yday)

    # 4) 扫描近 7 天
    print("\n=== settle_pending_days(7) ===")
    _, total = settle_pending_days(7)
    print("total_settled =", total)

    # 5) 打印 ledger 最后几条
    ledger_path = paths.file_daily_ledger()
    data = load_json(ledger_path)
    items = data.get("items", [])
    print(f"\n=== daily_ledger items count={len(items)} ===")
    for it in items[-10:]:
        print(it)


if __name__ == "__main__":
    main()
