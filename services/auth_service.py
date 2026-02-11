from __future__ import annotations

import hashlib
import hmac
import os
import re
from datetime import datetime
from typing import Any, Dict, Tuple

from services import supabase_client
from storage import paths
from storage.json_store import ensure_json_file, update_json


_PBKDF2_ITER = 200_000
DEFAULT_DEVELOPER = "老王养基"


def _strict_web_cloud_mode() -> bool:
    return bool(os.getenv("STREAMLIT_SHARING_MODE", "").strip())


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


def _db() -> Dict[str, Any]:
    p = paths.file_auth_users()
    res = ensure_json_file(p, default_data={"items": []})
    data = res.data if isinstance(res.data, dict) else {"items": []}
    if not isinstance(data.get("items", []), list):
        data["items"] = []
    return data


def _to_user_id(phone: str) -> str:
    return f"u_{phone}"


def register_user(phone: str, password: str) -> Tuple[bool, str, str | None]:
    norm_phone = _normalize_phone(phone)
    if not _validate_phone(norm_phone):
        return False, "手机号格式不正确（11位）", None
    if not _validate_password(password):
        return False, "密码至少6位", None

    if _strict_web_cloud_mode() and (not supabase_client.is_enabled()):
        return False, "网页端云端未配置，暂不可注册", None

    if supabase_client.is_enabled():
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
            "developer": DEFAULT_DEVELOPER,
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

    if _strict_web_cloud_mode() and (not supabase_client.is_enabled()):
        return False, "网页端云端未配置，暂不可登录", None

    if supabase_client.is_enabled():
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

    data = _db()
    items = data.get("items", [])
    user = next((x for x in items if str(x.get("phone", "")) == norm_phone), None)
    if not user:
        return False, "账号不存在，请先注册", None
    if not _password_verify(password, str(user.get("password_hash", ""))):
        return False, "密码错误", None
    user_id = str(user.get("user_id") or _to_user_id(norm_phone))
    return True, "登录成功", user_id
