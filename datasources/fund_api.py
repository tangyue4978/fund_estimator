from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from config import settings
from datasources.http_client import get_text
from utils.time_utils import now_iso


@dataclass
class GszQuote:
    code: str
    name: str
    gsz: float
    gszzl: float
    gztime: str
    nav: Optional[float] = None  # dwjz


_JSONPGZ_RE = re.compile(r"jsonpgz\((\{.*\})\)\s*;?\s*$", re.S)


def _headers() -> dict:
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) fund_estimator/0.1",
        "Referer": "https://fund.eastmoney.com/",
    }


def _fetch_gsz_one_real(code: str) -> Optional[GszQuote]:
    url = f"https://fundgz.1234567.com.cn/js/{code}.js"
    params = {"rt": int(datetime.now().timestamp() * 1000)}

    resp = get_text(
        cache_key=f"gsz_{code}",
        url=url,
        params=params,
        headers=_headers(),
    )
    if not resp.ok or not resp.text:
        return None

    text = resp.text.strip()
    m = _JSONPGZ_RE.search(text)
    if not m:
        return None

    obj = json.loads(m.group(1))
    gsz = float(obj.get("gsz") or 0.0)
    gszzl = float(obj.get("gszzl") or 0.0)
    name = str(obj.get("name") or f"基金{code}")

    gztime_raw = str(obj.get("gztime") or "").strip()
    gztime_iso = ""
    if gztime_raw:
        try:
            dt = datetime.strptime(gztime_raw, "%Y-%m-%d %H:%M")
            gztime_iso = dt.isoformat(timespec="seconds")
        except Exception:
            gztime_iso = now_iso()
    else:
        gztime_iso = now_iso()

    nav = None
    dwjz = obj.get("dwjz")
    if dwjz is not None and str(dwjz).strip() != "":
        try:
            nav = float(dwjz)
        except Exception:
            nav = None

    return GszQuote(code=code, name=name, gsz=gsz, gszzl=gszzl, gztime=gztime_iso, nav=nav)


def _fetch_gsz_quotes_mock(codes: List[str]) -> Dict[str, GszQuote]:
    now = datetime.now()
    result: Dict[str, GszQuote] = {}

    for i, code in enumerate(codes):
        base_nav = 1.0 + (i % 7) * 0.01
        pct = ((i % 9) - 4) * 0.12
        gsz = base_nav * (1 + pct / 100)

        stale = (code.endswith("0") or (i % 5 == 0))
        gz_time = (now - timedelta(minutes=10)).isoformat(timespec="seconds") if stale else now_iso()

        result[code] = GszQuote(
            code=code,
            name=f"基金{code}",
            gsz=round(gsz, 6),
            gszzl=round(pct, 4),
            gztime=gz_time,
            nav=round(base_nav, 6),
        )

    return result


def fetch_gsz_quotes(codes: List[str]) -> Dict[str, GszQuote]:
    codes = [str(c).strip() for c in codes if str(c).strip()]
    if not codes:
        return {}

    if not getattr(settings, "USE_REAL_DATASOURCE", False):
        return _fetch_gsz_quotes_mock(codes)

    out: Dict[str, GszQuote] = {}
    try:
        for code in codes:
            q = _fetch_gsz_one_real(code)
            if q:
                out[code] = q
            else:
                out.update(_fetch_gsz_quotes_mock([code]))
        return out
    except Exception:
        return _fetch_gsz_quotes_mock(codes)
