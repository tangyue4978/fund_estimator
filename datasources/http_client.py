from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import settings
from storage import paths
from storage.json_store import load_json, save_json


@dataclass
class CachedResponse:
    ok: bool
    text: str
    from_cache: bool
    ts: int


def _make_session() -> requests.Session:
    s = requests.Session()
    retries = Retry(
        total=getattr(settings, "HTTP_RETRIES", 3),
        backoff_factor=0.4,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retries)
    s.mount("http://", adapter)
    s.mount("https://", adapter)
    return s


_SESSION = _make_session()


def _read_cache(path: Path) -> Optional[dict]:
    try:
        return load_json(path, fallback=None)
    except Exception:
        return None


def _write_raw(key: str, text: str) -> None:
    if not getattr(settings, "SAVE_RAW_HTTP", True):
        return
    p = paths.file_raw_snapshot(key)
    p.write_text(text, encoding="utf-8", errors="ignore")


def get_text(
    *,
    cache_key: str,
    url: str,
    params: Optional[dict] = None,
    headers: Optional[dict] = None,
    ttl_sec: Optional[int] = None,
    timeout_sec: Optional[int] = None,
) -> CachedResponse:
    """
    带缓存 + 重试 + raw落盘的 GET(text)。
    - cache_key：决定 cache/raw 文件名
    - ttl_sec：默认 settings.HTTP_CACHE_TTL_SEC
    - timeout_sec：默认 settings.HTTP_TIMEOUT_SEC
    """
    paths.ensure_dirs()

    ttl = int(ttl_sec if ttl_sec is not None else getattr(settings, "HTTP_CACHE_TTL_SEC", 60))
    timeout = float(timeout_sec if timeout_sec is not None else getattr(settings, "HTTP_TIMEOUT_SEC", 6))

    cache_path = paths.file_http_cache(cache_key)
    now = int(time.time())

    # 1) cache hit
    cached = _read_cache(cache_path)
    if isinstance(cached, dict):
        ts = int(cached.get("ts", 0))
        text = str(cached.get("text", ""))
        if ts > 0 and (now - ts) <= ttl and text:
            return CachedResponse(ok=True, text=text, from_cache=True, ts=ts)

    # 2) fetch
    try:
        r = _SESSION.get(url, params=params, headers=headers, timeout=timeout)
        if r.status_code >= 400:
            # 仍然把响应落盘便于排查
            _write_raw(cache_key, r.text)
            return CachedResponse(ok=False, text=r.text or "", from_cache=False, ts=now)

        text = r.text or ""
        # cache
        save_json(cache_path, {"ts": now, "text": text})
        # raw
        _write_raw(cache_key, text)
        return CachedResponse(ok=True, text=text, from_cache=False, ts=now)
    except Exception as e:
        return CachedResponse(ok=False, text="", from_cache=False, ts=now)
