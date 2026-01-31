from __future__ import annotations

from services.watchlist_service import (
    get_watchlist,
    watchlist_add,
    watchlist_remove,
    watchlist_realtime_view,
)


def main():
    print("=== current watchlist ===")
    print(get_watchlist())

    print("\n=== add 000001 / 510300 (duplicate) ===")
    watchlist_add("000001")
    watchlist_add("510300")
    print(get_watchlist())

    print("\n=== remove 000001 ===")
    watchlist_remove("000001")
    print(get_watchlist())

    print("\n=== realtime view (engine) ===")
    rows = watchlist_realtime_view()
    for r in rows:
        print(
            f"{r.code} {r.name}  est_nav={r.est_nav}  "
            f"pct={r.est_change_pct}%  method={r.method}  "
            f"conf={r.confidence}  refresh={r.suggested_refresh_sec}s  "
            f"time={r.est_time}  warn={r.warning}"
        )


if __name__ == "__main__":
    main()
