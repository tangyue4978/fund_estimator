from __future__ import annotations

from storage import paths
from storage.json_store import ensure_json_file, update_json


def main():
    # 1) 确保所有目录存在
    paths.ensure_dirs()

    # 2) 初始化几个核心文件（不存在则自动创建）
    for p in [
        paths.file_fund_cache(),
        paths.file_watchlist(),
        paths.file_portfolio(),
        paths.file_adjustments(),
        paths.file_daily_ledger(),
    ]:
        res = ensure_json_file(p)
        print(f"[ensure] {p.name} created={res.created}")

    # 3) 测试 update_json：给 watchlist 加一条代码（去重）
    wl_path = paths.file_watchlist()

    def add_one(wl: dict):
        items = wl.get("items", [])
        if "510300" not in items:
            items.append("510300")
        wl["items"] = items
        return wl

    new_data = update_json(wl_path, add_one)
    print("[watchlist]", new_data)


if __name__ == "__main__":
    main()
