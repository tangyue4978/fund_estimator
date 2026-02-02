from __future__ import annotations
from typing import Optional
from datasources.fund_profile_provider import FundProfileProvider, FundProfileDTO

_LOCAL_MAP = {
    "510300": FundProfileDTO(code="510300", name="沪深300ETF", fund_type="ETF", is_etf=True),
}

class LocalMapFundProfileProvider(FundProfileProvider):
    def fetch(self, code: str) -> Optional[FundProfileDTO]:
        return _LOCAL_MAP.get(code)
