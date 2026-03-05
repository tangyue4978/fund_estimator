from __future__ import annotations

from typing import Optional

from datasources.fund_api import GszQuote, fetch_gsz_quotes


def get_gsz_quote(code: str) -> Optional[GszQuote]:
    code = str(code or "").strip()
    if not code:
        return None
    return fetch_gsz_quotes([code]).get(code)

