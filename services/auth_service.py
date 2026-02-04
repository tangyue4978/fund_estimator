from __future__ import annotations

import hashlib
import hmac
import os
import re
from datetime import datetime
from typing import Any, Dict, Tuple

from storage import paths
from storage.json_store import ensure_json_file, update_json


_PBKDF2_ITER = 200_000


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _normalize_phone(phone: str) -> str:
    raw = str(phone or "").strip()
    digits = re.sub(r"\D+", "", raw)
    return digits


def _validate_phone(phone: str) -> bool:
    # Keep it simple: digits only, 6-20 chars.
    return bool(re.fullmatch(r"\d{6,20}", phone or ""))


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


def _db() -> Dict[str, Any]:
    p = paths.file_auth_users()
    res = ensure_json_file(p, default_data={"items": []})
    data = res.data if isinstance(res.data, dict) else {"items": []}
    items = data.get("items", [])
    if not isinstance(items, list):
        data["items"] = []
    return data


def _to_user_id(phone: str) -> str:
    return f"u_{phone}"


def register_user(phone: str, password: str) -> Tuple[bool, str, str | None]:
    norm_phone = _normalize_phone(phone)
    if not _validate_phone(norm_phone):
        return False, "手机号格式不正确（仅数字，长度6-20）", None
    if not _validate_password(password):
        return False, "密码至少6位", None

    p = paths.file_auth_users()
    result: Dict[str, Any] = {"ok": False, "msg": "注册失败", "user_id": None}

    def updater(data: dict):
        items = data.get("items", [])
        if not isinstance(items, list):
            items = []
        exists = next((x for x in items if str(x.get("phone", "")) == norm_phone), None)
        if exists:
            result["ok"] = False
            result["msg"] = "手机号已注册"
            result["user_id"] = str(exists.get("user_id") or _to_user_id(norm_phone))
            data["items"] = items
            return data

        user = {
            "phone": norm_phone,
            "user_id": _to_user_id(norm_phone),
            "password_hash": _password_hash(password),
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
        }
        items.append(user)
        data["items"] = items
        result["ok"] = True
        result["msg"] = "注册成功"
        result["user_id"] = user["user_id"]
        return data

    update_json(p, updater)
    return bool(result["ok"]), str(result["msg"]), result["user_id"]


def login_user(phone: str, password: str) -> Tuple[bool, str, str | None]:
    norm_phone = _normalize_phone(phone)
    if not _validate_phone(norm_phone):
        return False, "手机号格式不正确", None
    if not _validate_password(password):
        return False, "密码至少6位", None

    data = _db()
    items = data.get("items", [])
    user = next((x for x in items if str(x.get("phone", "")) == norm_phone), None)
    if not user:
        return False, "账号不存在，请先注册", None
    if not _password_verify(password, str(user.get("password_hash", ""))):
        return False, "密码错误", None
    user_id = str(user.get("user_id") or _to_user_id(norm_phone))
    return True, "登录成功", user_id
