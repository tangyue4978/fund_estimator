# domain/fund.py
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any


@dataclass
class FundProfile:
    """
    基金/ETF 基础信息（静态信息 + 轻量缓存实体）
    - 只放“不会频繁变化”的字段
    - 不要把盘中估值/净值放进来（那些属于 Estimate / Ledger）
    """
    code: str
    name: str = ""

    # 类型描述：例如 "ETF" / "指数" / "混合" / "QDII" / "债券" ...
    fund_type: str = ""

    # 关键标签（用于估值路由、UI分类、风控提示）
    is_etf: bool = False
    is_qdii: bool = False

    # 跟踪指数（可选），例如 "000300"（沪深300）等
    track_index: Optional[str] = None

    # ⭐ 新增：档案来源
    source: str = ""   # local_map / api / gsz_fallback

    # 这条 profile 缓存更新时间（ISO 格式字符串，例如 "2026-02-02T09:30:00+08:00"）
    updated_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """用于 JSON 持久化"""
        d = asdict(self)
        return d

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "FundProfile":
        """从 JSON 反序列化"""
        if not data:
            raise ValueError("FundProfile.from_dict: empty data")

        return FundProfile(
            code=str(data.get("code", "")).strip(),
            name=str(data.get("name", "")).strip(),
            fund_type=str(data.get("fund_type", "")).strip(),
            is_etf=bool(data.get("is_etf", False)),
            is_qdii=bool(data.get("is_qdii", False)),
            track_index=(str(data["track_index"]).strip() if data.get("track_index") else None),
            
            # ⭐ 新增
            source=str(data.get("source", "")).strip(),
            updated_at=(str(data["updated_at"]).strip() if data.get("updated_at") else None),
        )

    def validate_basic(self) -> None:
        """
        领域约束（轻量）：
        - code 必须存在
        - name 可以为空（允许先靠行情接口临时补名），但建议尽快补齐
        """
        if not self.code or not str(self.code).strip():
            raise ValueError("FundProfile.validate_basic: code is required")
