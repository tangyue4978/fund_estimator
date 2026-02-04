# storage/json_store.py
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Optional


@dataclass
class EnsureResult:
    path: str
    data: Any
    created: bool


def _ensure_parent(p: str) -> None:
    Path(p).parent.mkdir(parents=True, exist_ok=True)


def _read_text_with_fallback(path: Path) -> str:
    data = path.read_bytes()
    for enc in ("utf-8", "utf-8-sig", "gbk", "gb18030"):
        try:
            return data.decode(enc)
        except Exception:
            continue
    return ""


def load_json(path: str, default: Optional[Any] = None, fallback: Optional[Any] = None) -> Any:
    """
    读取 JSON 文件。
    - default: 文件不存在/读取失败时返回
    - fallback: default 的别名（兼容项目里现有调用）
    """
    if default is None and fallback is not None:
        default = fallback

    try:
        p = Path(path)
        if not p.exists():
            return default
        text = _read_text_with_fallback(p).strip()
        if not text:
            return default
        return json.loads(text)
    except Exception:
        return default


def save_json(path: str, data: Any) -> None:
    _ensure_parent(path)
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def ensure_json_file(path: str, default_data: Optional[Any] = None) -> EnsureResult:
    """
    确保 JSON 文件存在。若不存在则创建并写入 default_data（默认 {}）。
    返回 EnsureResult，其中 data 为读到/写入的数据。
    """
    default_data = {} if default_data is None else default_data
    _ensure_parent(path)

    p = Path(path)
    if not p.exists():
        save_json(path, default_data)
        return EnsureResult(path=path, data=default_data, created=True)

    data = load_json(path, default=default_data)
    if data is None:
        data = default_data
        save_json(path, data)
        return EnsureResult(path=path, data=data, created=False)

    return EnsureResult(path=path, data=data, created=False)


def update_json(path: str, updater: Callable[[Dict[str, Any]], Dict[str, Any]]) -> Dict[str, Any]:
    """
    读 -> updater -> 写，返回写入后的 dict
    """
    res = ensure_json_file(path, default_data={})
    data = res.data if isinstance(res.data, dict) else {}
    new_data = updater(data) or data
    if not isinstance(new_data, dict):
        raise ValueError("update_json: updater must return dict")
    save_json(path, new_data)
    return new_data


def ensure_json_file_with_schema(path: str, schema_default: Dict[str, Any]) -> Dict[str, Any]:
    """
    用于需要固定 schema 的 json：缺字段就补齐
    """
    res = ensure_json_file(path, default_data=schema_default)
    data = res.data if isinstance(res.data, dict) else {}

    changed = False
    for k, v in schema_default.items():
        if k not in data:
            data[k] = v
            changed = True

    if changed:
        save_json(path, data)
    return data
