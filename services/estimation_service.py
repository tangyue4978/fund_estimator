# services/estimation_service.py
from __future__ import annotations

from typing import Dict, List, Tuple

from config import constants
from datasources.fund_api import fetch_gsz_quotes
from datasources.fund_holdings_jsonmap import load_holdings, load_holdings_batch
from datasources.market_api import fetch_stock_quotes, normalize_stock_code
from datasources.nav_api import fetch_official_navs
from domain.estimate import EstimateResult
from services import fund_service
from utils.time_utils import now_iso


def _latest_official_nav(code: str) -> Tuple[float, str | None]:
    items = fetch_official_navs(code, days_back=10)
    if not items:
        return 0.0, None
    last = items[-1]
    return float(last.nav), last.nav_date


def _estimate_from_gsz(code: str, name: str, q, *, method: str) -> EstimateResult:
    if q and q.gsz and q.gsz > 0:
        return EstimateResult(
            code=code,
            name=name,
            est_nav=q.gsz,
            est_change_pct=q.gszzl,
            method=method,
            confidence=0.9,
            warning="",
            suggested_refresh_sec=10,
            est_time=q.gztime,
        )

    frozen_nav = q.nav if q and q.nav and q.nav > 0 else 0.0
    return EstimateResult(
        code=code,
        name=name,
        est_nav=frozen_nav,
        est_change_pct=0.0,
        method=constants.METHOD_FROZEN_NAV,
        confidence=0.3,
        warning="estimate data unavailable, fallback to last nav",
        suggested_refresh_sec=60,
        est_time=q.gztime if q else now_iso(),
    )


def _estimate_by_holdings(code: str, name: str, holdings_obj: dict, stock_quotes: dict, q) -> EstimateResult | None:
    holdings = holdings_obj.get("holdings", []) if isinstance(holdings_obj, dict) else []
    if not holdings:
        return None

    total_weight = 0.0
    covered_weight = 0.0
    weighted_sum = 0.0

    for it in holdings:
        if not isinstance(it, dict):
            continue
        w = float(it.get("weight_pct") or it.get("weight") or 0.0)
        if w <= 0:
            continue
        total_weight += w
        scode = normalize_stock_code(it.get("code", ""))
        if not scode:
            continue
        sq = stock_quotes.get(scode)
        if not sq:
            continue
        covered_weight += w
        weighted_sum += w * sq.change_pct

    if total_weight <= 0 or covered_weight <= 0:
        return None

    weighted_pct = weighted_sum / total_weight
    coverage = covered_weight / total_weight * 100.0

    base_nav = q.nav if q and q.nav and q.nav > 0 else 0.0
    if base_nav <= 0:
        base_nav, _ = _latest_official_nav(code)
    if base_nav <= 0:
        return None

    est_nav = base_nav * (1.0 + weighted_pct / 100.0)
    if coverage >= 80:
        confidence = 0.75
    elif coverage >= 50:
        confidence = 0.55
    else:
        confidence = 0.35

    warning = ""
    if coverage < 60:
        warning = f"holdings coverage low ({coverage:.1f}%)"
    as_of = str(holdings_obj.get("as_of") or "").strip()
    if as_of:
        warning = f"{warning}; holdings as_of {as_of}" if warning else f"holdings as_of {as_of}"

    return EstimateResult(
        code=code,
        name=name,
        est_nav=est_nav,
        est_change_pct=weighted_pct,
        method=constants.METHOD_HOLDING_WEIGHTED,
        confidence=confidence,
        warning=warning,
        suggested_refresh_sec=10,
        est_time=now_iso(),
        realtime_coverage_value_pct=coverage,
    )


def estimate_one(code: str) -> EstimateResult:
    code = (code or "").strip()
    if not code:
        raise ValueError("estimate_one: code is required")

    quotes = fetch_gsz_quotes([code])
    q = quotes.get(code)

    profile = fund_service.get_fund_profile(code)
    name = (profile.name or "").strip() or (q.name if q else "") or code

    if profile.is_etf:
        return _estimate_from_gsz(code, name, q, method=constants.METHOD_ETF_IIV)

    holdings_obj = None if profile.is_qdii else load_holdings(code)
    if holdings_obj and holdings_obj.get("holdings"):
        stock_codes = [normalize_stock_code(h.get("code", "")) for h in holdings_obj.get("holdings", [])]
        stock_quotes = fetch_stock_quotes(stock_codes)
        est = _estimate_by_holdings(code, name, holdings_obj, stock_quotes, q)
        if est:
            return est

    return _estimate_from_gsz(code, name, q, method=constants.METHOD_OFFICIAL_GSZ)


def estimate_many(codes: List[str]) -> Dict[str, EstimateResult]:
    codes = [c.strip() for c in (codes or []) if c and str(c).strip()]
    if not codes:
        return {}

    quotes = fetch_gsz_quotes(codes)
    profiles = {c: fund_service.get_fund_profile(c) for c in codes}

    non_etf_codes = [
        c for c in codes
        if not (profiles.get(c) and (profiles.get(c).is_etf or profiles.get(c).is_qdii))
    ]
    holdings_map = load_holdings_batch(non_etf_codes) if non_etf_codes else {}

    stock_codes: List[str] = []
    for obj in holdings_map.values():
        for h in obj.get("holdings", []):
            scode = normalize_stock_code(h.get("code", ""))
            if scode:
                stock_codes.append(scode)

    stock_quotes = fetch_stock_quotes(stock_codes) if stock_codes else {}

    out: Dict[str, EstimateResult] = {}
    for code in codes:
        q = quotes.get(code)
        profile = profiles.get(code)
        name = (profile.name if profile else "") or (q.name if q else "") or code

        if profile and profile.is_etf:
            out[code] = _estimate_from_gsz(code, name, q, method=constants.METHOD_ETF_IIV)
            continue

        holdings_obj = holdings_map.get(code)
        if holdings_obj:
            est = _estimate_by_holdings(code, name, holdings_obj, stock_quotes, q)
            if est:
                out[code] = est
                continue

        out[code] = _estimate_from_gsz(code, name, q, method=constants.METHOD_OFFICIAL_GSZ)

    return out
