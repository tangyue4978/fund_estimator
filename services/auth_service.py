from __future__ import annotations

import hashlib
import hmac
import os
import re
from datetime import datetime
from typing import Tuple

from services import supabase_client


_PBKDF2_ITER = 200_000
DEFAULT_DEVELOPER = "老王养基"


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _normalize_phone(phone: str) -> str:
    raw = str(phone or "").strip()
    return re.sub(r"\D+", "", raw)


def _validate_phone(phone: str) -> bool:
    return bool(re.fullmatch(r"1[3-9]\d{9}", phone or ""))


def _validate_password(password: str) -> bool:
    return len(str(password or "")) >= 6


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
    except Exception:
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


def _to_user_id(phone: str) -> str:
    return f"u_{phone}"


def register_user(phone: str, password: str) -> Tuple[bool, str, str | None]:
    norm_phone = _normalize_phone(phone)
    if not _validate_phone(norm_phone):
        return False, "手机号格式不正确（11位）", None
    if not _validate_password(password):
        return False, "密码至少6位", None
    if not supabase_client.is_enabled():
        return False, "云端未配置，暂不可注册", None

    try:
        user_id = _to_user_id(norm_phone)
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
            return False, "手机号已注册", user_id
        return False, f"注册失败({resp.status_code})", None
    except Exception:
        return False, "注册失败：数据库连接异常", None


def login_user(phone: str, password: str) -> Tuple[bool, str, str | None]:
    norm_phone = _normalize_phone(phone)
    if not _validate_phone(norm_phone):
        return False, "手机号格式不正确", None
    if not _validate_password(password):
        return False, "密码至少6位", None
    if not supabase_client.is_enabled():
        return False, "云端未配置，暂不可登录", None

    try:
        rows = supabase_client.get_rows(
            "app_users",
            params={
                "phone": f"eq.{norm_phone}",
                "select": "phone,user_id,password_hash",
                "limit": "1",
            },
        )
        if not rows:
            return False, "账号不存在，请先注册", None
        user = rows[0] if isinstance(rows[0], dict) else {}
        if not _password_verify(password, str(user.get("password_hash", ""))):
            return False, "密码错误", None
        user_id = str(user.get("user_id") or _to_user_id(norm_phone))
        return True, "登录成功", user_id
    except Exception:
        return False, "登录失败：数据库连接异常", None
