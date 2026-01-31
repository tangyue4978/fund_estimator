from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class Adjustment:
    id: str
    type: str              # BUY / SELL / CASH_ADJ
    code: str              # fund code
    effective_date: str    # YYYY-MM-DD
    shares: float = 0.0    # BUY/SELL 用
    price: float = 0.0     # BUY/SELL 用（按净值或成交净值）
    cash: float = 0.0      # CASH_ADJ 用（正负都可）
    note: Optional[str] = None
    created_at: Optional[str] = None
