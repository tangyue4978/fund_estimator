from __future__ import annotations

from datetime import datetime
from typing import List

from storage import paths
from storage.json_store import ensure_json_file, update_json


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _load() -> dict:
    p = paths.file_watchlist()
    res = ensure_json_file(p)
    data = res.data if isinstance(res.data, dict) else {}
    if "items" not in data or not isinstance(data.get("items"), list):
        data["items"] = []
    return data


def watchlist_list() -> List[str]:
    data = _load()
    items = data.get("items", [])
    # 统一为字符串、去空、去重（保持原顺序）
    seen = set()
    out: List[str] = []
    for x in items:
        code = str(x).strip()
        if not code or code in seen:
            continue
        seen.add(code)
        out.append(code)
    return out


def watchlist_add(code: str) -> dict:
    code = (code or "").strip()
    if not code:
        return _load()

    p = paths.file_watchlist()

    def updater(data: dict):
        items = data.get("items", [])
        if not isinstance(items, list):
            items = []
        # 去重追加
        if code not in [str(x).strip() for x in items]:
            items.append(code)
        data["items"] = items
        data["updated_at"] = _now_iso()
        return data

    return update_json(p, updater)


def watchlist_remove(code: str) -> dict:
    code = (code or "").strip()
    if not code:
        return _load()

    p = paths.file_watchlist()

    def updater(data: dict):
        items = data.get("items", [])
        if not isinstance(items, list):
            items = []
        data["items"] = [str(x).strip() for x in items if str(x).strip() and str(x).strip() != code]
        data["updated_at"] = _now_iso()
        return data

    return update_json(p, updater)


# ---------- 兼容旧命名（如果你之前别处用过） ----------
def list_watchlist() -> List[str]:
    return watchlist_list()


def add_to_watchlist(code: str) -> dict:
    return watchlist_add(code)


def remove_from_watchlist(code: str) -> dict:
    return watchlist_remove(code)
