from __future__ import annotations
from dataclasses import dataclass
from typing import Optional


@dataclass
class FundProfileDTO:
    code: str
    name: str
    fund_type: str = ""
    is_etf: bool = False
    is_qdii: bool = False
    track_index: Optional[str] = None


class FundProfileProvider:
    def fetch(self, code: str) -> Optional[FundProfileDTO]:
        raise NotImplementedError
