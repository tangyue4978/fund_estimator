from __future__ import annotations

import os
import re
from typing import Any, Dict, Optional, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


def _make_session() -> requests.Session:
    session = requests.Session()
    retries = Retry(
        total=3,
        connect=3,
        read=3,
        status=3,
        backoff_factor=0.4,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET", "PATCH", "DELETE"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retries)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    return session


_SESSION = _make_session()
_TABLE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


class SupabaseRequestError(RuntimeError):
    def __init__(self, operation: str, table: str, status_code: int) -> None:
        self.operation = operation
        self.table = table
        self.status_code = int(status_code)
        super().__init__(f"cloud {operation} failed for {table} ({self.status_code})")


def _load_from_streamlit_secrets(key: str) -> str:
    try:
        import streamlit as st  # type: ignore

        value = st.secrets.get(key, "")
        return str(value or "").strip()
    except Exception:
        return ""


def get_config() -> Tuple[str, str]:
    url = os.getenv("SUPABASE_URL", "").strip()
    key = os.getenv("SUPABASE_KEY", "").strip()
    if not url:
        url = _load_from_streamlit_secrets("SUPABASE_URL")
    if not key:
        key = _load_from_streamlit_secrets("SUPABASE_KEY")
    return url.rstrip("/"), key


def is_enabled() -> bool:
    url, key = get_config()
    return bool(url and key)


def _headers(extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    _, key = get_config()
    h = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    if extra:
        h.update(extra)
    return h


def _resource_url(resource: str, *, rpc: bool = False) -> str:
    name = str(resource or "").strip()
    if not _TABLE_RE.fullmatch(name):
        raise ValueError("invalid Supabase resource name")
    url, _ = get_config()
    prefix = "rest/v1/rpc" if rpc else "rest/v1"
    return f"{url}/{prefix}/{name}"


def _raise_for_status(resp: requests.Response, *, operation: str, table: str) -> None:
    if int(resp.status_code) >= 400:
        # requests' default HTTPError contains the full query URL, which can
        # expose user identifiers in UI error messages and diagnostic exports.
        raise SupabaseRequestError(operation, table, int(resp.status_code))


def get_rows(table: str, params: Optional[Dict[str, str]] = None) -> list[dict]:
    resp = _SESSION.get(
        _resource_url(table),
        params=params or {},
        headers=_headers(),
        timeout=12,
    )
    _raise_for_status(resp, operation="read", table=table)
    data = resp.json()
    return data if isinstance(data, list) else []


def get_rows_paginated(
    table: str,
    params: Optional[Dict[str, str]] = None,
    *,
    page_size: int = 1000,
    max_rows: int = 50_000,
) -> list[dict]:
    """Read a PostgREST collection without silently stopping at its row cap."""
    size = max(1, min(1000, int(page_size)))
    cap = max(size, int(max_rows))
    base = dict(params or {})
    start_offset = max(0, int(base.pop("offset", 0) or 0))
    requested_limit = base.pop("limit", None)
    if requested_limit is not None:
        cap = min(cap, max(0, int(requested_limit)))
    if cap <= 0:
        return []

    rows: list[dict] = []
    offset = start_offset
    while len(rows) < cap:
        batch_limit = min(size, cap - len(rows))
        batch = get_rows(
            table,
            params={
                **base,
                "limit": str(batch_limit),
                "offset": str(offset),
            },
        )
        rows.extend(item for item in batch if isinstance(item, dict))
        if len(batch) < batch_limit:
            return rows
        offset += len(batch)

    if requested_limit is not None:
        return rows

    # A full last page means more rows may exist. Refuse to return a silently
    # truncated financial history.
    probe = get_rows(
        table,
        params={
            **base,
            "select": str(base.get("select") or "*"),
            "limit": "1",
            "offset": str(offset),
        },
    )
    if probe:
        raise RuntimeError(f"cloud read exceeded safe row limit ({cap})")
    return rows


def insert_row(table: str, row: Dict[str, Any]) -> requests.Response:
    resp = _SESSION.post(
        _resource_url(table),
        json=row,
        headers=_headers({"Prefer": "return=representation"}),
        timeout=12,
    )
    return resp


def upsert_rows(table: str, rows: list[Dict[str, Any]], on_conflict: str) -> requests.Response:
    resp = _SESSION.post(
        _resource_url(table),
        params={"on_conflict": on_conflict},
        json=rows,
        headers=_headers({"Prefer": "resolution=merge-duplicates,return=representation"}),
        timeout=12,
    )
    return resp


def delete_rows(table: str, params: Dict[str, str]) -> requests.Response:
    resp = _SESSION.delete(
        _resource_url(table),
        params=params,
        headers=_headers({"Prefer": "return=representation"}),
        timeout=12,
    )
    return resp


def update_rows(table: str, data: Dict[str, Any], params: Dict[str, str]) -> requests.Response:
    resp = _SESSION.patch(
        _resource_url(table),
        params=params,
        json=data,
        headers=_headers({"Prefer": "return=representation"}),
        timeout=12,
    )
    return resp


def call_rpc(name: str, payload: Dict[str, Any]) -> requests.Response:
    return _SESSION.post(
        _resource_url(name, rpc=True),
        json=payload,
        headers=_headers({"Prefer": "return=representation"}),
        timeout=20,
    )
