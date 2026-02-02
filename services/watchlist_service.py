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

# ====== 新增：统一命名 + 实时视图（追加在文件末尾即可）======

from typing import Dict, Any, List

from services.estimation_service import estimate_many
from services.fund_service import get_fund_profile


def get_watchlist() -> List[str]:
    """推荐的新命名（不破坏旧代码）"""
    return watchlist_list()


def watchlist_realtime_view() -> List[Dict[str, Any]]:
    """
    自选列表实时估值视图：返回每只基金一行，给 UI 直接用
    字段：code, name, est_change_pct, est_nav, method, confidence, warning, est_time, suggested_refresh_sec
    """
    codes = watchlist_list()
    if not codes:
        return []

    # 批量估值
    est_map = estimate_many(codes)

    # 读取 profile（用于 name 统一口径）
    profiles = {c: get_fund_profile(c) for c in codes}

    rows: List[Dict[str, Any]] = []
    for code in codes:
        est = est_map.get(code)
        profile = profiles.get(code)

        # name 口径：profile > estimate.name > code
        name = (profile.name if profile else "") or (est.name if est else "") or code

        if not est:
            rows.append({
                "code": code,
                "name": name,
                "est_change_pct": None,
                "est_nav": None,
                "method": "N/A",
                "confidence": 0.0,
                "warning": "估值失败",
                "est_time": None,
                "suggested_refresh_sec": None,
            })
            continue

        rows.append({
            "code": code,
            "name": name,
            "est_change_pct": est.est_change_pct,
            "est_nav": est.est_nav,
            "method": est.method,
            "confidence": est.confidence,
            "warning": est.warning,
            "est_time": est.est_time,
            "suggested_refresh_sec": est.suggested_refresh_sec,
        })

    return rows