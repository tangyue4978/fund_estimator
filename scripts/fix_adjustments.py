from __future__ import annotations

from storage import paths
from storage.json_store import load_json, save_json


def main():
    p = paths.file_adjustments()
    data = load_json(p, fallback={"items": []})
    items = data.get("items", [])
    if not isinstance(items, list):
        print("adjustments.json items invalid")
        return

    # 模拟回放检查：遇到 SELL > hold 的直接标记删除
    shares = {}
    bad_ids = []

    # 排序：effective_date + created_at
    items_sorted = sorted(items, key=lambda x: (str(x.get("effective_date", "")), str(x.get("created_at", ""))))

    for a in items_sorted:
        t = str(a.get("type", ""))
        code = str(a.get("code", ""))
        sh = float(a.get("shares", 0.0) or 0.0)

        cur = float(shares.get(code, 0.0))

        if t == "BUY":
            shares[code] = cur + sh
        elif t == "SELL":
            if sh > cur + 1e-9:
                bad_ids.append(str(a.get("id")))
                # 不执行这条 SELL
            else:
                shares[code] = cur - sh
        else:
            # CASH_ADJ 不影响 shares
            pass

    if not bad_ids:
        print("No bad SELL found.")
        return

    print(f"Found bad SELL adjustments: {len(bad_ids)}")
    for bid in bad_ids:
        print(" -", bid)

    # 删除 bad
    new_items = [x for x in items if str(x.get("id")) not in set(bad_ids)]
    data["items"] = new_items
    save_json(p, data)
    print("Bad SELL removed. Saved adjustments.json")


if __name__ == "__main__":
    main()
