from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

from config import settings
from datasources.http_client import get_text


@dataclass
class OfficialNav:
    code: str
    nav_date: str
    nav: float


_NETWORTH_RE = re.compile(r"Data_netWorthTrend\s*=\s*(\[[\s\S]*?\])\s*;", re.M)


def _headers() -> dict:
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) fund_estimator/0.1",
        "Referer": "http://fund.eastmoney.com/",
    }


def _fetch_pingzhongdata_js(code: str) -> str:
    url = f"http://fund.eastmoney.com/pingzhongdata/{code}.js"
    params = {"v": datetime.now().strftime("%Y%m%d%H%M%S")}

    resp = get_text(
        cache_key=f"navjs_{code}",
        url=url,
        params=params,
        headers=_headers(),
        ttl_sec=6 * 60 * 60,  # 这个文件一天内变化不大，给长一点缓存
    )
    return resp.text if resp.ok else ""


def _parse_networth_trend(code: str, js_text: str) -> List[OfficialNav]:
    m = _NETWORTH_RE.search(js_text)
    if not m:
        return []

    arr = json.loads(m.group(1))
    out: List[OfficialNav] = []

    for it in arr:
        x = it.get("x")
        y = it.get("y")
        if x is None or y is None:
            continue
        try:
            dt = datetime.fromtimestamp(int(x) / 1000)
            d = dt.date().isoformat()
            nav = float(y)
            if nav > 0:
                out.append(OfficialNav(code=code, nav_date=d, nav=nav))
        except Exception:
            continue

    out.sort(key=lambda z: z.nav_date)
    return out


def _mock_fetch_official_navs(code: str) -> List[OfficialNav]:
    return []


def fetch_official_navs(code: str, days_back: int = 30) -> List[OfficialNav]:
    code = (code or "").strip()
    if not code:
        return []

    if not getattr(settings, "USE_REAL_DATASOURCE", False):
        return _mock_fetch_official_navs(code)

    try:
        js_text = _fetch_pingzhongdata_js(code)
        if not js_text:
            return _mock_fetch_official_navs(code)
        all_items = _parse_networth_trend(code, js_text)
        if days_back <= 0:
            return all_items
        return all_items[-days_back:]
    except Exception:
        return _mock_fetch_official_navs(code)


def fetch_official_nav_for_date(code: str, target_date: str) -> Optional[OfficialNav]:
    items = fetch_official_navs(code, days_back=180)
    for it in items:
        if it.nav_date == target_date:
            return it
    return None
