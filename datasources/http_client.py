from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Callable, Optional
from weakref import WeakValueDictionary

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
    stale: bool = False
    error: str = ""


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
_REQUEST_LOCKS: WeakValueDictionary[str, Lock] = WeakValueDictionary()
_REQUEST_LOCKS_GUARD = Lock()


def _request_lock(cache_key: str) -> Lock:
    key = str(cache_key)
    with _REQUEST_LOCKS_GUARD:
        lock = _REQUEST_LOCKS.get(key)
        if lock is None:
            lock = Lock()
            _REQUEST_LOCKS[key] = lock
        return lock


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
    text_validator: Optional[Callable[[str], bool]] = None,
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

    def _is_valid(text: str) -> bool:
        if not text:
            return False
        if text_validator is None:
            return True
        try:
            return bool(text_validator(text))
        except Exception:
            return False

    def _stale_or_failure(*, error: str, response_text: str = "") -> CachedResponse:
        if isinstance(cached, dict):
            cached_ts = int(cached.get("ts", 0) or 0)
            cached_text = str(cached.get("text", "") or "")
            if cached_ts > 0 and _is_valid(cached_text):
                return CachedResponse(
                    ok=True,
                    text=cached_text,
                    from_cache=True,
                    ts=cached_ts,
                    stale=True,
                    error=error,
                )
        return CachedResponse(
            ok=False,
            text=response_text,
            from_cache=False,
            ts=now,
            error=error,
        )

    def _fresh_cached_response(value: object) -> Optional[CachedResponse]:
        if not isinstance(value, dict):
            return None
        ts = int(value.get("ts", 0) or 0)
        text = str(value.get("text", "") or "")
        if ts > 0 and (now - ts) <= ttl and _is_valid(text):
            return CachedResponse(ok=True, text=text, from_cache=True, ts=ts)
        return None

    # Fast path avoids lock contention while the cache is fresh.
    cached = _read_cache(cache_path)
    fresh = _fresh_cached_response(cached)
    if fresh is not None:
        return fresh

    # Only one caller per cache key may refresh an expired entry. Waiting
    # callers re-read the cache so a successful refresh is reused.
    with _request_lock(cache_key):
        now = int(time.time())
        cached = _read_cache(cache_path)
        fresh = _fresh_cached_response(cached)
        if fresh is not None:
            return fresh

        try:
            r = _SESSION.get(url, params=params, headers=headers, timeout=timeout)
            if r.status_code >= 400:
                try:
                    _write_raw(cache_key, r.text)
                except Exception:
                    pass
                return _stale_or_failure(
                    error=f"http_status_{r.status_code}",
                    response_text=r.text or "",
                )

            text = r.text or ""
            if not _is_valid(text):
                try:
                    _write_raw(cache_key, text)
                except Exception:
                    pass
                return _stale_or_failure(error="response_validation_failed", response_text=text)

            # Cache diagnostics must never turn a valid network response into a
            # datasource outage.
            try:
                save_json(cache_path, {"ts": now, "text": text})
            except Exception:
                pass
            try:
                _write_raw(cache_key, text)
            except Exception:
                pass
            return CachedResponse(ok=True, text=text, from_cache=False, ts=now)
        except Exception as e:
            return _stale_or_failure(error=type(e).__name__)
