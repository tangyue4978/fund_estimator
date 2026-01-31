from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class EstimateResult:
    code: str
    name: str

    # 估算净值与涨跌幅（相对昨收/昨净值）
    est_nav: float
    est_change_pct: float  # 单位：百分比，例如 1.23 表示 +1.23%

    # 方法与可信度
    method: str
    confidence: float  # 0~1

    # 数据时间（字符串 ISO 或源返回时间）
    est_time: str

    # 提示/警告信息（可为空字符串）
    warning: str

    # 建议刷新间隔（秒）
    suggested_refresh_sec: int

    # 可选：覆盖率（组合或ETF合成时更有意义）
    realtime_coverage_value_pct: Optional[float] = None
