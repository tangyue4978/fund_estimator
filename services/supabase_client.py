from __future__ import annotations

import os
from typing import Any, Dict, Optional, Tuple

import requests


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


def get_rows(table: str, params: Optional[Dict[str, str]] = None) -> list[dict]:
    url, _ = get_config()
    resp = requests.get(
        f"{url}/rest/v1/{table}",
        params=params or {},
        headers=_headers(),
        timeout=12,
    )
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, list) else []


def insert_row(table: str, row: Dict[str, Any]) -> requests.Response:
    url, _ = get_config()
    resp = requests.post(
        f"{url}/rest/v1/{table}",
        json=row,
        headers=_headers({"Prefer": "return=representation"}),
        timeout=12,
    )
    return resp


def upsert_rows(table: str, rows: list[Dict[str, Any]], on_conflict: str) -> requests.Response:
    url, _ = get_config()
    resp = requests.post(
        f"{url}/rest/v1/{table}",
        params={"on_conflict": on_conflict},
        json=rows,
        headers=_headers({"Prefer": "resolution=merge-duplicates,return=representation"}),
        timeout=12,
    )
    return resp


def delete_rows(table: str, params: Dict[str, str]) -> requests.Response:
    url, _ = get_config()
    resp = requests.delete(
        f"{url}/rest/v1/{table}",
        params=params,
        headers=_headers({"Prefer": "return=representation"}),
        timeout=12,
    )
    return resp
