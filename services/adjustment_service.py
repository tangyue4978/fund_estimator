from __future__ import annotations

import math
import re
import uuid
import time
from datetime import date, datetime, timezone
from typing import List, Optional

from services import supabase_client
from services.cloud_status_service import clear_cloud_error, set_cloud_error
from storage import paths


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


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
        or (note.startswith("ui") and ("edit" in note or "编辑" in note))
    )


def _cache_key(code: Optional[str] = None, through_date: Optional[str] = None) -> str:
    suffix = (code or "__all__").strip() or "__all__"
    if through_date:
        suffix = f"{suffix}__through_{through_date}"
    return f"_adjustments_cache_{paths.current_user_id()}_{suffix}"


def _read_cached_adjustments(code: Optional[str] = None, through_date: Optional[str] = None) -> List[dict]:
    try:
        import streamlit as st  # type: ignore

        cached = st.session_state.get(_cache_key(code, through_date), [])
        return [x for x in cached if isinstance(x, dict)] if isinstance(cached, list) else []
    except Exception:
        return []


def _read_fresh_cached_adjustments(
    code: Optional[str] = None,
    through_date: Optional[str] = None,
    *,
    max_age_sec: float = 2.0,
) -> Optional[List[dict]]:
    try:
        import streamlit as st  # type: ignore

        key = _cache_key(code, through_date)
        ts = float(st.session_state.get(f"{key}__ts", 0.0) or 0.0)
        if ts <= 0 or (time.monotonic() - ts) > max_age_sec:
            return None
        return _read_cached_adjustments(code, through_date)
    except Exception:
        return None


def _write_cached_adjustments(
    code: Optional[str],
    rows: List[dict],
    through_date: Optional[str] = None,
) -> None:
    try:
        import streamlit as st  # type: ignore

        key = _cache_key(code, through_date)
        st.session_state[key] = [x for x in rows if isinstance(x, dict)]
        st.session_state[f"{key}__ts"] = time.monotonic()
        if code:
            all_key = _cache_key(None)
            st.session_state.pop(all_key, None)
            st.session_state.pop(f"{all_key}__ts", None)
    except Exception:
        pass


def _clear_adjustments_cache() -> None:
    try:
        import streamlit as st  # type: ignore

        prefix = f"_adjustments_cache_{paths.current_user_id()}_"
        for key in list(st.session_state.keys()):
            if str(key).startswith(prefix):
                st.session_state.pop(key, None)
    except Exception:
        pass


def migrate_ui_edit_source(code: Optional[str] = None, effective_date: Optional[str] = None) -> int:
    code = (code or "").strip()
    effective_date = (effective_date or "").strip()
    if not supabase_client.is_enabled():
        return 0
    try:
        params = {
            "user_id": f"eq.{paths.current_user_id()}",
            "order": "effective_date.asc,created_at.asc,id.asc",
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
            if src == "ui_edit" or not _looks_like_ui_edit(row):
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
        return 0


def list_adjustments(
    code: Optional[str] = None,
    *,
    through_date: Optional[str] = None,
) -> List[dict]:
    if not supabase_client.is_enabled():
        return []
    code = (code or "").strip() or None
    through_date = (through_date or "").strip() or None
    cached = _read_fresh_cached_adjustments(code, through_date)
    if cached is not None:
        return cached
    try:
        params_base = {
            "user_id": f"eq.{paths.current_user_id()}",
            "order": "effective_date.asc,created_at.asc,id.asc",
        }
        if code:
            params_base["code"] = f"eq.{code}"
        if through_date:
            params_base["effective_date"] = f"lte.{through_date}"
        try:
            rows = supabase_client.get_rows_paginated(
                "app_adjustments",
                params={**params_base, "select": "id,type,code,effective_date,shares,price,cash,note,source,created_at"},
            )
        except Exception:
            rows = supabase_client.get_rows_paginated(
                "app_adjustments",
                params={**params_base, "select": "id,type,code,effective_date,shares,price,cash,note,created_at"},
            )
        out = [x for x in rows if isinstance(x, dict)]
        _write_cached_adjustments(code, out, through_date)
        clear_cloud_error("adjustments")
        return out
    except Exception as e:
        set_cloud_error("adjustments", e)
        return _read_cached_adjustments(code, through_date)


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
    if not re.fullmatch(r"\d{6}", code):
        raise ValueError("code must be 6 digits")
    try:
        date.fromisoformat(effective_date)
    except (TypeError, ValueError) as exc:
        raise ValueError("effective_date must be an ISO date") from exc
    try:
        shares = float(shares)
        price = float(price)
        cash = float(cash)
    except (TypeError, ValueError) as exc:
        raise ValueError("adjustment values must be numeric") from exc
    if not all(math.isfinite(value) for value in (shares, price, cash)):
        raise ValueError("adjustment values must be finite")
    if shares < 0 or shares > 1e15:
        raise ValueError("shares is outside the supported range")
    if price < 0 or price > 1e12:
        raise ValueError("price is outside the supported range")
    if abs(cash) > 1e18:
        raise ValueError("cash is outside the supported range")
    note = str(note) if note is not None else None
    if note is not None and len(note) > 500:
        raise ValueError("note is too long")
    source = (source or "manual").strip().lower()
    if source not in ("manual", "ui_edit"):
        raise ValueError("source must be manual/ui_edit")
    if type in ("BUY", "SELL"):
        if shares <= 0:
            raise ValueError("shares must be > 0 for BUY/SELL")
        if price <= 0:
            raise ValueError("price must be > 0 for BUY/SELL")
    if not supabase_client.is_enabled():
        raise RuntimeError("cloud storage is not configured")

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

    try:
        payload = dict(item)
        payload["user_id"] = paths.current_user_id()
        resp = supabase_client.insert_row("app_adjustments", payload)
        if resp.status_code in (400, 404):
            payload.pop("source", None)
            if source == "ui_edit":
                note_raw = str(payload.get("note") or "").strip()
                if not note_raw.startswith("[ui_edit]"):
                    payload["note"] = f"[ui_edit] {note_raw}".strip()
            resp = supabase_client.insert_row("app_adjustments", payload)
        if resp.status_code not in (200, 201):
            raise RuntimeError(f"add adjustment failed({resp.status_code})")
        _clear_adjustments_cache()
        return {"items": list_adjustments(), "updated_at": _now_iso()}
    except Exception as e:
        raise RuntimeError(f"add_adjustment cloud failed: {e}") from e


def replace_ui_position_edit_atomic(
    *,
    effective_date: str,
    code: str,
    shares_end: float,
    avg_cost_nav_end: float,
    realized_pnl_end: float,
    note: Optional[str] = None,
) -> bool:
    """Use the database transaction RPC when the security migration is installed.

    Returns False only when the RPC is not present, allowing an explicitly
    documented compatibility fallback. Other failures are not downgraded to
    the legacy multi-request write path because the transaction outcome may be
    unknown after a timeout.
    """
    if not supabase_client.is_enabled():
        raise RuntimeError("cloud storage is not configured")

    resp = supabase_client.call_rpc(
        "app_apply_position_edit",
        {
            "p_user_id": paths.current_user_id(),
            "p_effective_date": effective_date,
            "p_code": code,
            "p_shares_end": float(shares_end),
            "p_avg_cost_nav_end": float(avg_cost_nav_end),
            "p_realized_pnl_end": float(realized_pnl_end),
            "p_note": note,
        },
    )
    if resp.status_code in (200, 201, 204):
        _clear_adjustments_cache()
        return True
    if resp.status_code == 404:
        try:
            error_code = str((resp.json() or {}).get("code", "")).strip().upper()
        except Exception:
            error_code = ""
        if error_code == "PGRST202":
            return False
    raise RuntimeError("atomic position edit failed; retry only after checking cloud state")


def remove_adjustment(adj_id: str) -> dict:
    adj_id = (adj_id or "").strip()
    if not adj_id:
        raise ValueError("adj_id is required")
    if not supabase_client.is_enabled():
        raise RuntimeError("cloud storage is not configured")
    try:
        resp = supabase_client.delete_rows("app_adjustments", {"user_id": f"eq.{paths.current_user_id()}", "id": f"eq.{adj_id}"})
        if resp.status_code not in (200, 204):
            raise RuntimeError(f"remove adjustment failed({resp.status_code})")
        _clear_adjustments_cache()
        return {"items": list_adjustments(), "updated_at": _now_iso()}
    except Exception as e:
        raise RuntimeError(f"remove_adjustment cloud failed: {e}") from e


def clear_adjustments() -> None:
    if not supabase_client.is_enabled():
        raise RuntimeError("cloud storage is not configured")
    try:
        resp = supabase_client.delete_rows("app_adjustments", {"user_id": f"eq.{paths.current_user_id()}"})
        if resp.status_code not in (200, 204):
            raise RuntimeError(f"clear adjustments failed({resp.status_code})")
        _clear_adjustments_cache()
    except Exception as e:
        raise RuntimeError(f"clear_adjustments cloud failed: {e}") from e


def remove_adjustments_by_code(code: str) -> int:
    code = (code or "").strip()
    if not code:
        raise ValueError("code is required")
    if not supabase_client.is_enabled():
        raise RuntimeError("cloud storage is not configured")
    try:
        uid = paths.current_user_id()
        rows = supabase_client.get_rows("app_adjustments", params={"user_id": f"eq.{uid}", "code": f"eq.{code}", "select": "id"})
        resp = supabase_client.delete_rows("app_adjustments", {"user_id": f"eq.{uid}", "code": f"eq.{code}"})
        if resp.status_code not in (200, 204):
            raise RuntimeError(f"remove by code failed({resp.status_code})")
        _clear_adjustments_cache()
        return len(rows)
    except Exception as e:
        raise RuntimeError(f"remove_adjustments_by_code cloud failed: {e}") from e


def remove_adjustments_by_code_date(code: str, effective_date: str, source: Optional[str] = None) -> int:
    code = (code or "").strip()
    effective_date = (effective_date or "").strip()
    if not code:
        raise ValueError("code is required")
    if not effective_date:
        raise ValueError("effective_date is required")
    source = (source or "").strip().lower()
    if source == "ui_edit":
        migrate_ui_edit_source(code=code, effective_date=effective_date)
    if not supabase_client.is_enabled():
        raise RuntimeError("cloud storage is not configured")
    try:
        uid = paths.current_user_id()
        query_params = {"user_id": f"eq.{uid}", "code": f"eq.{code}", "effective_date": f"eq.{effective_date}"}
        if source:
            query_params["source"] = f"eq.{source}"
        rows = supabase_client.get_rows("app_adjustments", params={**query_params, "select": "id"})
        resp = supabase_client.delete_rows("app_adjustments", query_params)
        if resp.status_code in (400, 404) and source:
            query_params.pop("source", None)
            if source == "ui_edit":
                rows = []
                ok = True
                for note_pattern in ("like.*[ui_edit]*", "like.UI编辑*", "like.edit->*"):
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
        _clear_adjustments_cache()
        return len(rows)
    except Exception as e:
        raise RuntimeError(f"remove_adjustments_by_code_date cloud failed: {e}") from e
