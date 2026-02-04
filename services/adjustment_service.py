from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from services import supabase_client
from storage import paths
from storage.json_store import ensure_json_file, update_json


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def list_adjustments(code: Optional[str] = None) -> List[dict]:
    if supabase_client.is_enabled():
        try:
            params = {
                "user_id": f"eq.{paths.current_user_id()}",
                "select": "id,type,code,effective_date,shares,price,cash,note,created_at",
                "order": "effective_date.asc,created_at.asc",
            }
            if code:
                params["code"] = f"eq.{code.strip()}"
            rows = supabase_client.get_rows("app_adjustments", params=params)
            return [x for x in rows if isinstance(x, dict)]
        except Exception:
            pass

    p = paths.file_adjustments()
    res = ensure_json_file(p)
    data = res.data if isinstance(res.data, dict) else {}
    items = data.get("items", [])
    if not isinstance(items, list):
        return []
    if code:
        code = code.strip()
        items = [x for x in items if str(x.get("code")) == code]
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

    if supabase_client.is_enabled():
        try:
            payload = dict(item)
            payload["user_id"] = paths.current_user_id()
            resp = supabase_client.insert_row("app_adjustments", payload)
            if resp.status_code not in (200, 201):
                raise RuntimeError(f"add adjustment failed({resp.status_code})")
            return {"items": list_adjustments(), "updated_at": _now_iso()}
        except Exception:
            pass

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

    if supabase_client.is_enabled():
        try:
            resp = supabase_client.delete_rows(
                "app_adjustments",
                {"user_id": f"eq.{paths.current_user_id()}", "id": f"eq.{adj_id}"},
            )
            if resp.status_code not in (200, 204):
                raise RuntimeError(f"remove adjustment failed({resp.status_code})")
            return {"items": list_adjustments(), "updated_at": _now_iso()}
        except Exception:
            pass

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
    if supabase_client.is_enabled():
        try:
            resp = supabase_client.delete_rows(
                "app_adjustments",
                {"user_id": f"eq.{paths.current_user_id()}"},
            )
            if resp.status_code not in (200, 204):
                raise RuntimeError(f"clear adjustments failed({resp.status_code})")
            return
        except Exception:
            pass

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

    if supabase_client.is_enabled():
        try:
            uid = paths.current_user_id()
            rows = supabase_client.get_rows(
                "app_adjustments",
                params={"user_id": f"eq.{uid}", "code": f"eq.{code}", "select": "id"},
            )
            resp = supabase_client.delete_rows(
                "app_adjustments",
                {"user_id": f"eq.{uid}", "code": f"eq.{code}"},
            )
            if resp.status_code not in (200, 204):
                raise RuntimeError(f"remove by code failed({resp.status_code})")
            return len(rows)
        except Exception:
            pass

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
