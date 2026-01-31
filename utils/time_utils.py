from __future__ import annotations

from datetime import datetime, time
from typing import Tuple


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def parse_hhmm(s: str) -> time:
    hh, mm = s.split(":")
    return time(int(hh), int(mm), 0)


def is_time_in_range(t: time, start: time, end: time) -> bool:
    return start <= t <= end


def is_trading_time(trading_sessions: list[Tuple[str, str]]) -> bool:
    """
    简化：仅用本地时间判断是否在交易时段。
    后续会接入真实交易日历与节假日。
    """
    now = datetime.now().time()
    for s, e in trading_sessions:
        if is_time_in_range(now, parse_hhmm(s), parse_hhmm(e)):
            return True
    return False
