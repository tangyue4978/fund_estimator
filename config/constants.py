from __future__ import annotations

from typing import Final


# 估值方法枚举（按需求文档）
METHOD_OFFICIAL_GSZ: Final[str] = "OFFICIAL_GSZ"
METHOD_ETF_IIV: Final[str] = "ETF_IIV"
METHOD_INDEX_PROXY: Final[str] = "INDEX_PROXY"
METHOD_HOLDING_WEIGHTED: Final[str] = "HOLDING_WEIGHTED"
METHOD_FROZEN_NAV: Final[str] = "FROZEN_NAV"
METHOD_MOCK: Final[str] = "MOCK"

# 日结状态
SETTLE_ESTIMATED_ONLY: Final[str] = "estimated_only"
SETTLE_SETTLED: Final[str] = "settled"
