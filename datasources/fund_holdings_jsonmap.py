# datasources/fund_holdings_jsonmap.py
from __future__ import annotations

from typing import Any, Dict, List, Optional

from storage import paths
from storage.json_store import ensure_json_file_with_schema, load_json


def _schema() -> Dict[str, Any]:
    return {
        "items": {
            # "000001": {
            #   "as_of": "2025-12-31",
            #   "holdings": [
            #     {"code": "600000", "name": "浦发银行", "weight_pct": 3.2},
            #   ]
            # }
        },
        "updated_at": None,
    }


def ensure_holdings_file() -> str:
    p = paths.file_fund_holdings_map()
    ensure_json_file_with_schema(p, _schema())
    return p


def _normalize_holdings(items: Any) -> List[dict]:
    if not isinstance(items, list):
        return []
    out: List[dict] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        code = str(it.get("code", "")).strip()
        if not code:
            continue
        out.append(it)
    return out


def load_holdings(code: str) -> Optional[dict]:
    code = (code or "").strip()
    if not code:
        return None

    path = ensure_holdings_file()
    data = load_json(path, default=_schema())
    if not isinstance(data, dict):
        return None
    items = data.get("items", {})
    if not isinstance(items, dict):
        return None
    obj = items.get(code)
    if not isinstance(obj, dict):
        return None

    obj = dict(obj)
    obj["holdings"] = _normalize_holdings(obj.get("holdings", []))
    return obj


def load_holdings_batch(codes: List[str]) -> Dict[str, dict]:
    path = ensure_holdings_file()
    data = load_json(path, default=_schema())
    items = data.get("items", {}) if isinstance(data, dict) else {}
    out: Dict[str, dict] = {}

    for code in codes:
        c = str(code or "").strip()
        if not c:
            continue
        obj = items.get(c) if isinstance(items, dict) else None
        if not isinstance(obj, dict):
            continue
        obj = dict(obj)
        obj["holdings"] = _normalize_holdings(obj.get("holdings", []))
        out[c] = obj

    return out
