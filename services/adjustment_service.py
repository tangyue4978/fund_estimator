from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from services import supabase_client
from storage import paths
from storage.json_store import ensure_json_file, update_json


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _looks_like_ui_edit(item: dict) -> bool:
    src = str(item.get("source", "") or "").strip().lower()
    if src == "ui_edit":
        return True
    note = str(item.get("note", "") or "").strip().lower()
    if not note:
        return False
    return (
        "[ui_edit]" in note
        or note.startswith("edit->")
        or note.startswith("ui_edit")
        or note.startswith("ui edit")
        or (note.startswith("ui") and ("edit" in note or "\u7f16\u8f91" in note))
    )


def migrate_ui_edit_source(code: Optional[str] = None, effective_date: Optional[str] = None) -> int:
    """
    Backfill historical ui_edit records whose source is empty/manual.
    Heuristic is based on note markers to avoid touching manual flows.
    """
    code = (code or "").strip()
    effective_date = (effective_date or "").strip()

    if supabase_client.is_enabled():
        try:
            params = {
                "user_id": f"eq.{paths.current_user_id()}",
                "order": "effective_date.asc,created_at.asc",
                "select": "id,note,source",
            }
            if code:
                params["code"] = f"eq.{code}"
            if effective_date:
                params["effective_date"] = f"eq.{effective_date}"
            rows = supabase_client.get_rows("app_adjustments", params=params)
            changed = 0
            for row in rows:
                if not isinstance(row, dict):
                    continue
                src = str(row.get("source", "") or "").strip().lower()
                if src == "ui_edit":
                    continue
                if not _looks_like_ui_edit(row):
                    continue
                rid = str(row.get("id", "")).strip()
                if not rid:
                    continue
                resp = supabase_client.update_rows(
                    "app_adjustments",
                    {"source": "ui_edit"},
                    {"user_id": f"eq.{paths.current_user_id()}", "id": f"eq.{rid}"},
                )
                if resp.status_code in (200, 204):
                    changed += 1
            return changed
        except Exception:
            pass

    p = paths.file_adjustments()
    changed = {"count": 0}

    def updater(data: dict):
        items = data.get("items", [])
        if not isinstance(items, list):
            items = []
        out = []
        cnt = 0
        for it in items:
            if not isinstance(it, dict):
                out.append(it)
                continue
            it_code = str(it.get("code", "")).strip()
            it_date = str(it.get("effective_date", "")).strip()
            if code and it_code != code:
                out.append(it)
                continue
            if effective_date and it_date != effective_date:
                out.append(it)
                continue
            src = str(it.get("source", "") or "").strip().lower()
            if src == "ui_edit":
                out.append(it)
                continue
            if _looks_like_ui_edit(it):
                it["source"] = "ui_edit"
                cnt += 1
            out.append(it)
        changed["count"] = cnt
        data["items"] = out
        if cnt > 0:
            data["updated_at"] = _now_iso()
        return data

    update_json(p, updater)
    return changed["count"]


def list_adjustments(code: Optional[str] = None) -> List[dict]:
    if supabase_client.is_enabled():
        try:
            params_base = {
                "user_id": f"eq.{paths.current_user_id()}",
                "order": "effective_date.asc,created_at.asc",
            }
            if code:
                params_base["code"] = f"eq.{code.strip()}"
            # New schema includes source.
            try:
                rows = supabase_client.get_rows(
                    "app_adjustments",
                    params={**params_base, "select": "id,type,code,effective_date,shares,price,cash,note,source,created_at"},
                )
            except Exception:
                # Backward-compatible fallback when source column doesn't exist.
                rows = supabase_client.get_rows(
                    "app_adjustments",
                    params={**params_base, "select": "id,type,code,effective_date,shares,price,cash,note,created_at"},
                )
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
    source: Optional[str] = None,
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
    source = (source or "manual").strip().lower()
    if source not in ("manual", "ui_edit"):
        raise ValueError("source must be manual/ui_edit")

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
        "source": source,
        "created_at": _now_iso(),
    }

    if supabase_client.is_enabled():
        try:
            payload = dict(item)
            payload["user_id"] = paths.current_user_id()
            resp = supabase_client.insert_row("app_adjustments", payload)
            if resp.status_code in (400, 404):
                # Backward-compatible fallback when source column doesn't exist.
                payload.pop("source", None)
                if source == "ui_edit":
                    note_raw = str(payload.get("note") or "").strip()
                    if not note_raw.startswith("[ui_edit]"):
                        payload["note"] = f"[ui_edit] {note_raw}".strip()
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


def remove_adjustments_by_code_date(code: str, effective_date: str, source: Optional[str] = None) -> int:
    code = (code or "").strip()
    effective_date = (effective_date or "").strip()
    if not code:
        raise ValueError("code is required")
    if not effective_date:
        raise ValueError("effective_date is required")
    source = (source or "").strip().lower()
    if source == "ui_edit":
        # Backfill old rows first; otherwise null/manual source rows won't be cleaned.
        migrate_ui_edit_source(code=code, effective_date=effective_date)

    if supabase_client.is_enabled():
        try:
            uid = paths.current_user_id()
            query_params = {
                "user_id": f"eq.{uid}",
                "code": f"eq.{code}",
                "effective_date": f"eq.{effective_date}",
            }
            if source:
                query_params["source"] = f"eq.{source}"
            rows = supabase_client.get_rows("app_adjustments", params={**query_params, "select": "id"})
            resp = supabase_client.delete_rows("app_adjustments", query_params)
            if resp.status_code in (400, 404) and source:
                # Backward-compatible fallback when source column doesn't exist.
                query_params.pop("source", None)
                if source == "ui_edit":
                    rows = []
                    ok = True
                    for note_pattern in ("like.*[ui_edit]*", "like.UI\u7f16\u8f91*", "like.edit->*"):
                        qp = dict(query_params)
                        qp["note"] = note_pattern
                        cur_rows = supabase_client.get_rows("app_adjustments", params={**qp, "select": "id"})
                        if isinstance(cur_rows, list):
                            rows.extend(cur_rows)
                        cur_resp = supabase_client.delete_rows("app_adjustments", qp)
                        if cur_resp.status_code not in (200, 204):
                            ok = False
                    if not ok:
                        raise RuntimeError("remove by code+date fallback delete failed")
                    # de-duplicate ids gathered from multiple note filters
                    uniq = {}
                    for r in rows:
                        if isinstance(r, dict) and r.get("id") is not None:
                            uniq[str(r.get("id"))] = r
                    rows = list(uniq.values())
                    resp = type("Resp", (), {"status_code": 200})()
                else:
                    rows = supabase_client.get_rows("app_adjustments", params={**query_params, "select": "id"})
                    resp = supabase_client.delete_rows("app_adjustments", query_params)
            if resp.status_code not in (200, 204):
                raise RuntimeError(f"remove by code+date failed({resp.status_code})")
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
            it_code = str(it.get("code", "")).strip()
            it_date = str(it.get("effective_date", "")).strip()
            it_source = str(it.get("source", "manual")).strip().lower() or "manual"
            source_match = (not source) or (it_source == source)
            if it_code == code and it_date == effective_date and source_match:
                cnt += 1
            else:
                new_items.append(it)
        removed["count"] = cnt
        data["items"] = new_items
        data["updated_at"] = _now_iso()
        return data

    update_json(p, updater)
    return removed["count"]
