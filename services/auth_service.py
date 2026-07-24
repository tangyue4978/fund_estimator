from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
from datetime import datetime
from typing import Tuple

from config import settings
from services import supabase_client
from services.auth_rate_limiter import LoginAttemptLimiter


_PBKDF2_ITER = 600_000
_PBKDF2_MAX_VERIFY_ITER = 2_000_000
_MIN_REGISTRATION_PASSWORD_LENGTH = 12
_MAX_PASSWORD_LENGTH = 128
_DUMMY_PASSWORD_HASH = (
    "pbkdf2_sha256$600000$66756e642d657374696d61746f7221$"
    "eadc6161f71d4d1d464fd4dea6486fb7009c987eb58006158dccaa3436ba697e"
)

LOGIN_FAILED_MESSAGE = "手机号或密码错误"
LOGIN_THROTTLED_MESSAGE = "登录尝试过于频繁，请稍后再试"
LOGIN_UNAVAILABLE_MESSAGE = "登录服务暂时不可用，请稍后再试"
REGISTER_UNAVAILABLE_MESSAGE = "注册服务暂时不可用，请稍后再试"

_LOGIN_LIMITER = LoginAttemptLimiter(
    max_failures=getattr(settings, "AUTH_LOGIN_MAX_FAILURES", 5),
    window_seconds=getattr(settings, "AUTH_LOGIN_FAILURE_WINDOW_SEC", 300),
    lockout_seconds=getattr(settings, "AUTH_LOGIN_LOCKOUT_SEC", 300),
    max_entries=getattr(settings, "AUTH_LOGIN_LIMITER_MAX_ENTRIES", 10_000),
)
DEFAULT_DEVELOPER = "老王养基"


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _normalize_phone(phone: str) -> str:
    raw = str(phone or "")[:256].strip()
    return re.sub(r"\D+", "", raw)


def _validate_phone(phone: str) -> bool:
    return bool(re.fullmatch(r"1[3-9]\d{9}", phone or ""))


def _validate_password(password: str) -> bool:
    length = len(str(password or ""))
    return 6 <= length <= _MAX_PASSWORD_LENGTH


def _validate_registration_password(password: str) -> bool:
    length = len(str(password or ""))
    return _MIN_REGISTRATION_PASSWORD_LENGTH <= length <= _MAX_PASSWORD_LENGTH


def _password_hash(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _PBKDF2_ITER)
    return f"pbkdf2_sha256${_PBKDF2_ITER}${salt.hex()}${digest.hex()}"


def _password_verify(password: str, encoded: str) -> bool:
    try:
        algo, iter_raw, salt_hex, digest_hex = str(encoded or "").split("$", 3)
        if algo != "pbkdf2_sha256":
            return False
        iterations = int(iter_raw)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
        if not (1 <= iterations <= _PBKDF2_MAX_VERIFY_ITER):
            return False
        if not (8 <= len(salt) <= 64) or len(expected) != hashlib.sha256().digest_size:
            return False
    except Exception:
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


def _password_needs_rehash(encoded: str) -> bool:
    try:
        algo, iter_raw, _, _ = str(encoded or "").split("$", 3)
        return algo != "pbkdf2_sha256" or int(iter_raw) < _PBKDF2_ITER
    except Exception:
        return True


def _new_user_id() -> str:
    # Do not derive identifiers from a phone number: user_id is persisted in
    # business rows and may also appear in infrastructure logs.
    return f"u_{secrets.token_hex(16)}"


def _failed_login(identifier: str) -> Tuple[bool, str, str | None]:
    decision = _LOGIN_LIMITER.record_failure(identifier)
    message = LOGIN_THROTTLED_MESSAGE if not decision.allowed else LOGIN_FAILED_MESSAGE
    return False, message, None


def register_user(phone: str, password: str) -> Tuple[bool, str, str | None]:
    norm_phone = _normalize_phone(phone)
    if not _validate_phone(norm_phone):
        return False, "手机号格式不正确（需为 11 位中国大陆手机号）", None
    if not _validate_registration_password(password):
        return (
            False,
            f"新密码需为 {_MIN_REGISTRATION_PASSWORD_LENGTH} 至 {_MAX_PASSWORD_LENGTH} 位",
            None,
        )
    if not supabase_client.is_enabled():
        return False, REGISTER_UNAVAILABLE_MESSAGE, None

    try:
        user_id = _new_user_id()
        payload = {
            "phone": norm_phone,
            "user_id": user_id,
            "password_hash": _password_hash(password),
            "developer": DEFAULT_DEVELOPER,
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
        }
        resp = supabase_client.insert_row("app_users", payload)
        if resp.status_code in (400, 404):
            payload.pop("developer", None)
            payload["inviter"] = DEFAULT_DEVELOPER
            resp = supabase_client.insert_row("app_users", payload)
        if resp.status_code in (400, 404):
            payload.pop("inviter", None)
            resp = supabase_client.insert_row("app_users", payload)
        if resp.status_code in (200, 201):
            return True, "注册成功", user_id
        if resp.status_code == 409:
            # Do not confirm whether a submitted phone number has an account.
            return False, "注册请求未完成，请检查信息或尝试登录", None
        return False, REGISTER_UNAVAILABLE_MESSAGE, None
    except Exception:
        return False, REGISTER_UNAVAILABLE_MESSAGE, None


def login_user(phone: str, password: str) -> Tuple[bool, str, str | None]:
    norm_phone = _normalize_phone(phone)
    limiter_identifier = norm_phone or str(phone or "")[:256]
    if not _LOGIN_LIMITER.check(limiter_identifier).allowed:
        return False, LOGIN_THROTTLED_MESSAGE, None

    if not _validate_phone(norm_phone) or not _validate_password(password):
        # Keep malformed and unknown-account paths close to the same cost and
        # always return the same credential error.
        if len(str(password or "")) <= _MAX_PASSWORD_LENGTH:
            _password_verify(str(password or ""), _DUMMY_PASSWORD_HASH)
        return _failed_login(limiter_identifier)
    if not supabase_client.is_enabled():
        return False, LOGIN_UNAVAILABLE_MESSAGE, None

    try:
        rows = supabase_client.get_rows(
            "app_users",
            params={
                "phone": f"eq.{norm_phone}",
                "select": "user_id,password_hash",
                "limit": "1",
            },
        )
        user = rows[0] if rows and isinstance(rows[0], dict) else {}
        encoded = str(user.get("password_hash", "")) if user else _DUMMY_PASSWORD_HASH
        password_ok = _password_verify(password, encoded)
        user_id = str(user.get("user_id") or "").strip()
        if not password_ok or not user_id:
            return _failed_login(limiter_identifier)

        _LOGIN_LIMITER.record_success(limiter_identifier)
        if _password_needs_rehash(encoded):
            try:
                supabase_client.update_rows(
                    "app_users",
                    {"password_hash": _password_hash(password), "updated_at": _now_iso()},
                    {"user_id": f"eq.{user_id}"},
                )
            except Exception:
                # A best-effort work-factor upgrade must not turn a valid
                # credential into an authentication failure.
                pass
        return True, "登录成功", user_id
    except Exception:
        return False, LOGIN_UNAVAILABLE_MESSAGE, None
