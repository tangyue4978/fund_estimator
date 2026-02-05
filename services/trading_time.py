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

