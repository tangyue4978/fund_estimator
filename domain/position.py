from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class Position:
    code: str
    shares: float
    avg_cost_nav: float
    realized_pnl: float = 0.0
    tag: Optional[str] = None
    note: Optional[str] = None
    updated_at: Optional[str] = None
