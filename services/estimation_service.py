from __future__ import annotations

from datetime import datetime
from typing import Dict, List, Tuple

from config import settings
from config import constants
from datasources.fund_api import fetch_gsz_quotes, GszQuote
from domain.estimate import EstimateResult
from utils.time_utils import is_trading_time


def _parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts)


def is_gsz_active(q: GszQuote) -> bool:
    """
    判断 gsz 是否可用且活跃：
    - gsz > 0
    - gztime 可解析
    - 若在交易时段内：gztime 距离现在不超过阈值（默认 5 分钟）
    """
    if q is None:
        return False
    if q.gsz is None or q.gsz <= 0:
        return False

    try:
        t = _parse_iso(q.gztime)
    except Exception:
        return False

    if is_trading_time(settings.TRADING_SESSIONS):
        age = (datetime.now() - t).total_seconds()
        return age <= settings.GSZ_STALE_SECONDS

    # 非交易时段：允许使用最新一条（用于收盘后回看）
    return True


def route_estimation(q: GszQuote) -> Tuple[str, float, float, str, int]:
    """
    路由策略（本阶段只实现 OFFICIAL_GSZ 与 FROZEN_NAV）
    返回：
    - method
    - est_nav
    - confidence
    - warning
    - suggested_refresh_sec
    """
    if q and is_gsz_active(q):
        # OFFICIAL_GSZ
        method = constants.METHOD_OFFICIAL_GSZ
        confidence = 0.85
        warning = ""
        suggested = settings.REFRESH_SEC_HIGH_CONF
        return method, q.gsz, confidence, warning, suggested

    # 降级：冻结（取 nav 或用 gsz 兜底）
    method = constants.METHOD_FROZEN_NAV
    est_nav = (q.nav if q and q.nav and q.nav > 0 else (q.gsz if q and q.gsz else 0.0))
    confidence = 0.25
    warning = "估值停更/不可用：已降级为冻结净值（仅供参考）"
    suggested = settings.REFRESH_SEC_FROZEN
    return method, float(est_nav), confidence, warning, suggested


def estimate_one(code: str) -> EstimateResult:
    code = (code or "").strip()
    if not code:
        raise ValueError("code is required")

    mp = estimate_many([code])
    return mp[code]


def estimate_many(codes: List[str]) -> Dict[str, EstimateResult]:
    """
    批量估值：
    - 本阶段使用 mock 数据源 fetch_gsz_quotes
    - 后续替换为真实接口时，保持函数签名不变
    """
    codes = [str(c).strip() for c in codes if str(c).strip()]
    quotes = fetch_gsz_quotes(codes)

    result: Dict[str, EstimateResult] = {}
    for code in codes:
        q = quotes.get(code)
        method, est_nav, conf, warning, suggested = route_estimation(q)

        # 涨跌幅：优先用接口给的 gszzl；冻结时置 0（按需求文档建议）
        if method == constants.METHOD_FROZEN_NAV:
            est_change_pct = 0.0
        else:
            est_change_pct = float(q.gszzl) if q else 0.0

        result[code] = EstimateResult(
            code=code,
            name=q.name if q else f"基金{code}",
            est_nav=float(est_nav),
            est_change_pct=float(est_change_pct),
            method=method,
            confidence=float(conf),
            warning=warning,
            est_time=q.gztime if q else "",
            suggested_refresh_sec=int(suggested),
        )

    return result
