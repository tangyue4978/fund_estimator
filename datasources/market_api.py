from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List

from config import settings
from datasources.http_client import get_text
from storage import paths
from storage.json_store import ensure_json_file_with_schema, load_json
from utils.time_utils import now_iso


@dataclass
class StockQuote:
    code: str
    name: str
    price: float
    prev_close: float
    change_pct: float
    ts: str


_TENCENT_LINE_RE = re.compile(r"^v_([a-z0-9]+)=\"(.*)\";?$", re.I)


def _schema_quote_map() -> dict:
    return {
        "items": {
            # "600000": {"name": "浦发银行", "change_pct": 0.0, "price": 0.0, "prev_close": 0.0, "ts": "..."}
        },
        "updated_at": None,
    }


def _strip_prefix(code: str) -> str:
    s = str(code or "").strip().lower()
    if s.startswith(("sh", "sz", "bj")):
        return s[2:]
    return s


def normalize_stock_code(code: str) -> str:
    return _strip_prefix(code)


def _guess_prefix(code: str) -> str:
    s = _strip_prefix(code)
    if s.startswith("6"):
        return "sh"
    if s.startswith(("0", "3")):
        return "sz"
    if s.startswith("8"):
        return "bj"
    return ""


def _build_query_codes(codes: List[str]) -> List[str]:
    out: List[str] = []
    for code in codes:
        base = _strip_prefix(code)
        if not base:
            continue
        prefix = _guess_prefix(base)
        out.append(f"{prefix}{base}" if prefix else base)
    return out


def _parse_tencent_line(line: str) -> StockQuote | None:
    m = _TENCENT_LINE_RE.match(line.strip())
    if not m:
        return None
    payload = m.group(2)
    parts = payload.split("~")
    if len(parts) < 5:
        return None

    name = parts[1].strip()
    code = parts[2].strip()
    price = _safe_float(parts[3])
    prev = _safe_float(parts[4])
    if price is None or prev is None or prev <= 0:
        return None

    pct = (price / prev - 1.0) * 100.0
    return StockQuote(code=code, name=name, price=price, prev_close=prev, change_pct=pct, ts=now_iso())


def _safe_float(x: str) -> float | None:
    try:
        return float(x)
    except Exception:
        return None


def _fetch_tencent_quotes(codes: List[str]) -> Dict[str, StockQuote]:
    query_codes = _build_query_codes(codes)
    if not query_codes:
        return {}

    url = f"https://qt.gtimg.cn/q={','.join(query_codes)}"
    resp = get_text(cache_key=f"tencent_quote_{len(query_codes)}", url=url, ttl_sec=6, timeout_sec=4)
    if not resp.ok or not resp.text:
        return {}

    out: Dict[str, StockQuote] = {}
    for line in resp.text.splitlines():
        q = _parse_tencent_line(line)
        if not q:
            continue
        if q.code:
            out[q.code] = q
    return out


def _fetch_quotes_from_map(codes: List[str]) -> Dict[str, StockQuote]:
    p = paths.file_stock_quote_map()
    ensure_json_file_with_schema(p, _schema_quote_map())
    data = load_json(p, default=_schema_quote_map())
    items = data.get("items", {}) if isinstance(data, dict) else {}

    out: Dict[str, StockQuote] = {}
    for code in codes:
        base = _strip_prefix(code)
        obj = items.get(base) if isinstance(items, dict) else None
        if not isinstance(obj, dict):
            continue
        name = str(obj.get("name", "")).strip()
        price = _safe_float(obj.get("price", 0))
        prev = _safe_float(obj.get("prev_close", 0))
        pct = _safe_float(obj.get("change_pct", 0))
        if pct is None and price is not None and prev and prev > 0:
            pct = (price / prev - 1.0) * 100.0
        if pct is None:
            continue
        out[base] = StockQuote(
            code=base,
            name=name,
            price=price or 0.0,
            prev_close=prev or 0.0,
            change_pct=pct,
            ts=str(obj.get("ts") or now_iso()),
        )

    return out


def fetch_stock_quotes(codes: List[str]) -> Dict[str, StockQuote]:
    codes = [str(c).strip() for c in (codes or []) if str(c).strip()]
    if not codes:
        return {}

    if not getattr(settings, "USE_REAL_DATASOURCE", False):
        return _fetch_quotes_from_map(codes)

    out = _fetch_tencent_quotes(codes)
    if len(out) < len(codes):
        fallback = _fetch_quotes_from_map(codes)
        for k, v in fallback.items():
            out.setdefault(k, v)
    return out
