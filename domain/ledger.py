from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class DailyLedgerItem:
    date: str        # 交易日 YYYY-MM-DD
    code: str

    shares_end: float
    avg_cost_nav_end: float

    estimated_nav_close: float
    estimated_pnl_close: float

    official_nav: Optional[float] = None
    official_pnl: Optional[float] = None

    settle_status: str = "estimated_only"
    updated_at: Optional[str] = None
