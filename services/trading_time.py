from __future__ import annotations

from datetime import datetime, time as dtime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None


_CN_TZ = ZoneInfo("Asia/Shanghai") if ZoneInfo is not None else timezone(timedelta(hours=8))


def now_cn() -> datetime:
    return datetime.now(_CN_TZ)


def is_cn_trading_time(now: datetime) -> bool:
    if now.weekday() >= 5:
        return False
    t = now.timetz().replace(tzinfo=None) if now.tzinfo is not None else now.time()
    return (dtime(9, 30) <= t <= dtime(11, 30)) or (dtime(13, 0) <= t <= dtime(15, 0))


def cn_market_phase(now: datetime | None = None) -> str:
    cur = now or now_cn()
    if cur.weekday() >= 5:
        return "closed"
    t = cur.timetz().replace(tzinfo=None) if cur.tzinfo is not None else cur.time()
    if dtime(9, 30) <= t <= dtime(11, 30):
        return "trading"
    if dtime(11, 30) < t < dtime(13, 0):
        return "lunch"
    if dtime(13, 0) <= t <= dtime(15, 0):
        return "trading"
    return "closed"
