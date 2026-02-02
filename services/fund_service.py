# services/fund_service.py
from __future__ import annotations

from datetime import datetime
from typing import Dict, Optional

from storage import paths
from storage.json_store import ensure_json_file_with_schema, update_json
from domain.fund import FundProfile

from datasources.fund_api import fetch_gsz_quotes

from datasources.fund_profile_jsonmap import JsonMapFundProfileProvider
from datasources.fund_profile_provider import FundProfileDTO

_PROVIDER = JsonMapFundProfileProvider()


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _cache_schema() -> dict:
    # fund_cache.json 的 schema
    return {
        "items": {},       # code -> FundProfile(dict)
        "updated_at": None
    }


def _load_cache() -> dict:
    p = paths.file_fund_cache()
    data = ensure_json_file_with_schema(p, _cache_schema())
    # 保底修复
    if "items" not in data or not isinstance(data.get("items"), dict):
        data["items"] = {}
    return data


def _save_profile_to_cache(profile: FundProfile) -> dict:
    profile.validate_basic()
    p = paths.file_fund_cache()

    def updater(data: dict):
        items = data.get("items", {})
        if not isinstance(items, dict):
            items = {}
        items[profile.code] = profile.to_dict()
        data["items"] = items
        data["updated_at"] = _now_iso()
        return data

    return update_json(p, updater)


def fund_cache_get(code: str) -> Optional[FundProfile]:
    code = (code or "").strip()
    if not code:
        return None
    data = _load_cache()
    items: Dict[str, dict] = data.get("items", {})
    obj = items.get(code)
    if not obj or not isinstance(obj, dict):
        return None
    try:
        return FundProfile.from_dict(obj)
    except Exception:
        return None


def _guess_is_etf(code: str) -> bool:
    """
    非严格：先给一个保守的 ETF 识别（后续可用真实基金档案接口替换）
    常见 ETF 前缀：510/511/512/513/515/516/517/518/588/159/56x 等
    """
    code = (code or "").strip()
    if len(code) < 3:
        return False
    prefixes = ("510", "511", "512", "513", "515", "516", "517", "518", "588", "159", "560", "561", "562", "563")
    return code.startswith(prefixes)


def _guess_is_qdii(code: str) -> bool:
    """
    非严格：QDII 通常是特定基金，但仅凭 code 很难判断。
    先默认 False，后续接 fund_profile 接口再补齐。
    """
    return False


def _build_profile_from_quote(code: str) -> FundProfile:
    # 1) 先尝试档案 provider（本地映射 / 未来真实 API）
    dto: FundProfileDTO | None = _PROVIDER.fetch(code)
    if dto:
        return FundProfile(
            code=code,
            name=dto.name or "",
            fund_type=dto.fund_type or ("ETF" if dto.is_etf else ""),
            is_etf=bool(dto.is_etf),
            is_qdii=bool(dto.is_qdii),
            track_index=dto.track_index,
            source="json_map",
            updated_at=_now_iso(),
        )

    # 2) provider 没有 → 兜底用 gsz 行情补 name
    quotes = fetch_gsz_quotes([code])
    q = quotes.get(code)
    name = q.name if q else ""

    return FundProfile(
        code=code,
        name=name or "",
        fund_type="ETF" if _guess_is_etf(code) else "",
        is_etf=_guess_is_etf(code),
        is_qdii=_guess_is_qdii(code),
        track_index=None,
        source="gsz_fallback",
        updated_at=_now_iso(),
    )


def get_fund_profile(code: str, *, force_refresh: bool = False) -> FundProfile:
    """
    获取基金基础信息（带本地缓存）。
    - 先读 fund_cache.json
    - 未命中/force_refresh 时用行情接口兜底补齐 name
    """
    code = (code or "").strip()
    if not code:
        raise ValueError("get_fund_profile: code is required")

    if not force_refresh:
        cached = fund_cache_get(code)
        if cached:
            return cached

    profile = _build_profile_from_quote(code)
    _save_profile_to_cache(profile)
    return profile
