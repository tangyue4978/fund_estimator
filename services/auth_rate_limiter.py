from __future__ import annotations

import hashlib
import hmac
import math
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Deque


@dataclass(frozen=True)
class LoginAttemptDecision:
    allowed: bool
    retry_after_seconds: int = 0


@dataclass
class _AttemptState:
    failures: Deque[float] = field(default_factory=deque)
    locked_until: float = 0.0
    last_seen: float = 0.0


class LoginAttemptLimiter:
    """Bounded, thread-safe login throttling without retaining raw identifiers."""

    def __init__(
        self,
        *,
        max_failures: int = 5,
        window_seconds: int = 300,
        lockout_seconds: int = 300,
        max_entries: int = 10_000,
        clock: Callable[[], float] = time.monotonic,
        key_secret: bytes | None = None,
    ) -> None:
        self.max_failures = max(1, int(max_failures))
        self.window_seconds = max(1, int(window_seconds))
        self.lockout_seconds = max(1, int(lockout_seconds))
        self.max_entries = max(100, int(max_entries))
        self._clock = clock
        self._key_secret = key_secret or os.urandom(32)
        self._states: dict[str, _AttemptState] = {}
        self._lock = threading.RLock()

    def _identifier_key(self, identifier: str) -> str:
        # Bound the work performed on attacker-controlled input. The raw value
        # is never used as a dictionary key, logged, or returned.
        raw = str(identifier or "")[:256].encode("utf-8", errors="ignore")
        return hmac.new(self._key_secret, raw, hashlib.sha256).hexdigest()

    def _prune_failures(self, state: _AttemptState, now: float) -> None:
        cutoff = now - self.window_seconds
        while state.failures and state.failures[0] <= cutoff:
            state.failures.popleft()

    def _evict_if_needed(self, now: float) -> None:
        expired_keys = [
            key
            for key, state in self._states.items()
            if state.locked_until <= now and state.last_seen <= now - self.window_seconds
        ]
        for key in expired_keys:
            self._states.pop(key, None)

        while len(self._states) >= self.max_entries:
            oldest_key = min(self._states, key=lambda key: self._states[key].last_seen)
            self._states.pop(oldest_key, None)

    def check(self, identifier: str) -> LoginAttemptDecision:
        key = self._identifier_key(identifier)
        now = self._clock()
        with self._lock:
            state = self._states.get(key)
            if state is None:
                return LoginAttemptDecision(allowed=True)
            state.last_seen = now
            if state.locked_until > now:
                return LoginAttemptDecision(
                    allowed=False,
                    retry_after_seconds=max(1, math.ceil(state.locked_until - now)),
                )
            if state.locked_until:
                self._states.pop(key, None)
                return LoginAttemptDecision(allowed=True)
            self._prune_failures(state, now)
            if not state.failures:
                self._states.pop(key, None)
            return LoginAttemptDecision(allowed=True)

    def record_failure(self, identifier: str) -> LoginAttemptDecision:
        key = self._identifier_key(identifier)
        now = self._clock()
        with self._lock:
            self._evict_if_needed(now)
            state = self._states.setdefault(key, _AttemptState())
            state.last_seen = now
            if state.locked_until > now:
                return LoginAttemptDecision(
                    allowed=False,
                    retry_after_seconds=max(1, math.ceil(state.locked_until - now)),
                )

            self._prune_failures(state, now)
            state.failures.append(now)
            if len(state.failures) >= self.max_failures:
                state.failures.clear()
                state.locked_until = now + self.lockout_seconds
                return LoginAttemptDecision(
                    allowed=False,
                    retry_after_seconds=self.lockout_seconds,
                )
            return LoginAttemptDecision(allowed=True)

    def record_success(self, identifier: str) -> None:
        key = self._identifier_key(identifier)
        with self._lock:
            self._states.pop(key, None)

