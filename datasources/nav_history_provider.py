from __future__ import annotations

import json
import re
from datetime import datetime
from typing import List

from datasources.http_client import get_text


_NETWORTH_RE = re.compile(r"Data_netWorthTrend\s*=\s*(\[[\s\S]*?\])\s*;", re.M)


def _headers() -> dict:
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) fund_estimator/0.1",
        "Referer": "https://fund.eastmoney.com/",
    }


def get_official_nav_history(code: str) -> List[dict]:
    code = str(code or "").strip()
    if not code:
        return []

    url = f"https://fund.eastmoney.com/pingzhongdata/{code}.js"
    resp = get_text(
        cache_key=f"official_nav_hist_{code}",
        url=url,
        params={"v": datetime.now().strftime("%Y%m%d%H%M%S")},
        headers=_headers(),
        ttl_sec=24 * 60 * 60,
    )
    if not resp.ok or not resp.text:
        return []

    m = _NETWORTH_RE.search(resp.text)
    if not m:
        return []

    try:
        arr = json.loads(m.group(1))
    except Exception:
        return []

    out: List[dict] = []
    for it in arr:
        x = it.get("x")
        y = it.get("y")
        if x is None or y is None:
            continue
        try:
            d = datetime.fromtimestamp(int(x) / 1000).date().isoformat()
            nav = float(y)
        except Exception:
            continue
        if nav > 0:
            out.append({"date": d, "value": nav})

    out.sort(key=lambda z: str(z.get("date", "")))
    return out

