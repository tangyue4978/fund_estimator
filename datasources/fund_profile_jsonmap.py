# datasources/fund_profile_jsonmap.py
from __future__ import annotations

from typing import Optional, Dict, Any

from datasources.fund_profile_provider import FundProfileProvider, FundProfileDTO
from storage import paths
from storage.json_store import ensure_json_file_with_schema, load_json, save_json


def _schema() -> Dict[str, Any]:
    return {
        "items": {
            # "510300": {"name": "...", "fund_type": "ETF", "is_etf": true, "is_qdii": false, "track_index": null}
        },
        "updated_at": None
    }


def ensure_map_file() -> str:
    p = paths.data_dir() / "fund_profile_map.json"
    ensure_json_file_with_schema(str(p), _schema())
    return str(p)


class JsonMapFundProfileProvider(FundProfileProvider):
    def __init__(self) -> None:
        self.path = ensure_map_file()

    def fetch(self, code: str) -> Optional[FundProfileDTO]:
        code = (code or "").strip()
        if not code:
            return None

        data = load_json(self.path, default=_schema())
        items = data.get("items", {}) if isinstance(data, dict) else {}
        obj = items.get(code)
        if not obj or not isinstance(obj, dict):
            return None

        name = str(obj.get("name", "")).strip()
        if not name:
            return None

        return FundProfileDTO(
            code=code,
            name=name,
            fund_type=str(obj.get("fund_type", "")).strip(),
            is_etf=bool(obj.get("is_etf", False)),
            is_qdii=bool(obj.get("is_qdii", False)),
            track_index=(str(obj["track_index"]).strip() if obj.get("track_index") else None),
        )


def upsert_one(code: str, dto: FundProfileDTO) -> None:
    """
    方便你写脚本批量维护 map
    """
    path = ensure_map_file()
    data = load_json(path, default=_schema())
    if not isinstance(data, dict):
        data = _schema()
    items = data.get("items", {})
    if not isinstance(items, dict):
        items = {}

    items[code] = {
        "name": dto.name,
        "fund_type": dto.fund_type,
        "is_etf": dto.is_etf,
        "is_qdii": dto.is_qdii,
        "track_index": dto.track_index,
    }
    data["items"] = items
    save_json(path, data)
