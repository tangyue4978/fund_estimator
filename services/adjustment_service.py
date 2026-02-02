from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional, Dict, Any

from storage import paths
from storage.json_store import ensure_json_file, update_json


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def list_adjustments(code: Optional[str] = None) -> List[dict]:
    p = paths.file_adjustments()
    res = ensure_json_file(p)
    data = res.data if isinstance(res.data, dict) else {}
    items = data.get("items", [])
    if not isinstance(items, list):
        return []
    if code:
        code = code.strip()
        items = [x for x in items if str(x.get("code")) == code]
    # 按日期 + 创建时间排序
    items.sort(key=lambda x: (str(x.get("effective_date", "")), str(x.get("created_at", ""))))
    return items


def add_adjustment(
    *,
    type: str,
    code: str,
    effective_date: str,
    shares: float = 0.0,
    price: float = 0.0,
    cash: float = 0.0,
    note: Optional[str] = None,
) -> dict:
    """
    写入一条流水：
    - BUY: shares>0, price>0
    - SELL: shares>0, price>0（shares 表示卖出的份额）
    - CASH_ADJ: cash 可正可负（例如分红/修正）
    """
    type = (type or "").strip().upper()
    code = (code or "").strip()
    effective_date = (effective_date or "").strip()

    if type not in ("BUY", "SELL", "CASH_ADJ"):
        raise ValueError("type must be BUY/SELL/CASH_ADJ")
    if not code:
        raise ValueError("code is required")
    if not effective_date:
        raise ValueError("effective_date is required")

    if type in ("BUY", "SELL"):
        if shares <= 0:
            raise ValueError("shares must be > 0 for BUY/SELL")
        if price <= 0:
            raise ValueError("price must be > 0 for BUY/SELL")
    if type == "CASH_ADJ":
        # cash 允许 0 但没意义，这里允许
        pass

    item = {
        "id": uuid.uuid4().hex,
        "type": type,
        "code": code,
        "effective_date": effective_date,
        "shares": float(shares),
        "price": float(price),
        "cash": float(cash),
        "note": note,
        "created_at": _now_iso(),
    }

    p = paths.file_adjustments()

    def updater(data: dict):
        items = data.get("items", [])
        if not isinstance(items, list):
            items = []
        items.append(item)
        data["items"] = items
        data["updated_at"] = _now_iso()
        return data

    return update_json(p, updater)


def remove_adjustment(adj_id: str) -> dict:
    adj_id = (adj_id or "").strip()
    if not adj_id:
        raise ValueError("adj_id is required")

    p = paths.file_adjustments()

    def updater(data: dict):
        items = data.get("items", [])
        if not isinstance(items, list):
            items = []
        data["items"] = [x for x in items if str(x.get("id")) != adj_id]
        data["updated_at"] = _now_iso()
        return data

    return update_json(p, updater)


def clear_adjustments() -> None:
    p = paths.file_adjustments()

    def updater(data: dict):
        data["items"] = []
        data["updated_at"] = _now_iso()
        return data

    update_json(p, updater)


def remove_adjustments_by_code(code: str) -> int:
    code = (code or "").strip()
    if not code:
        raise ValueError("code is required")

    p = paths.file_adjustments()
    removed = {"count": 0}

    def updater(data: dict):
        items = data.get("items", [])
        if not isinstance(items, list):
            items = []
        new_items = []
        cnt = 0
        for it in items:
            if str(it.get("code", "")).strip() == code:
                cnt += 1
            else:
                new_items.append(it)
        removed["count"] = cnt
        data["items"] = new_items
        data["updated_at"] = _now_iso()
        return data

    update_json(p, updater)
    return removed["count"]
