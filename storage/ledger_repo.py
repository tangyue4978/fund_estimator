from __future__ import annotations

from typing import List, Optional

from services.settlement_service import get_ledger_items


def get_daily_ledger_items(code: Optional[str] = None) -> List[dict]:
    items = [x for x in get_ledger_items() if isinstance(x, dict)]
    c = str(code or "").strip()
    if not c:
        return items
    return [x for x in items if str(x.get("code", "")).strip() == c]

