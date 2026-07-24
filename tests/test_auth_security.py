from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from services import auth_guard, auth_service
from services.auth_rate_limiter import LoginAttemptLimiter
from storage.json_store import load_json


class LoginAttemptLimiterTests(unittest.TestCase):
    def test_locks_then_expires_without_storing_raw_identifier(self) -> None:
        now = [100.0]
        limiter = LoginAttemptLimiter(
            max_failures=3,
            window_seconds=60,
            lockout_seconds=10,
            max_entries=100,
            clock=lambda: now[0],
            key_secret=b"k" * 32,
        )
        identifier = "13800138000"

        self.assertTrue(limiter.record_failure(identifier).allowed)
        self.assertTrue(limiter.record_failure(identifier).allowed)
        locked = limiter.record_failure(identifier)

        self.assertFalse(locked.allowed)
        self.assertEqual(locked.retry_after_seconds, 10)
        self.assertFalse(limiter.check(identifier).allowed)
        self.assertNotIn(identifier, repr(limiter._states))

        now[0] += 11
        self.assertTrue(limiter.check(identifier).allowed)

    def test_success_clears_prior_failures(self) -> None:
        limiter = LoginAttemptLimiter(
            max_failures=2,
            window_seconds=60,
            lockout_seconds=10,
            key_secret=b"k" * 32,
        )
        limiter.record_failure("account")
        limiter.record_success("account")
        self.assertTrue(limiter.record_failure("account").allowed)


class AuthServiceSecurityTests(unittest.TestCase):
    def _limiter(self, max_failures: int = 10) -> LoginAttemptLimiter:
        return LoginAttemptLimiter(
            max_failures=max_failures,
            window_seconds=60,
            lockout_seconds=30,
            key_secret=b"t" * 32,
        )

    def test_invalid_unknown_and_wrong_password_use_same_public_error(self) -> None:
        with (
            patch.object(auth_service, "_LOGIN_LIMITER", self._limiter()),
            patch.object(auth_service, "_password_verify", return_value=False),
            patch.object(auth_service.supabase_client, "is_enabled", return_value=True),
            patch.object(auth_service.supabase_client, "get_rows", return_value=[]),
        ):
            invalid = auth_service.login_user("not-a-phone", "secret1")
            missing = auth_service.login_user("13800138000", "secret1")

        with (
            patch.object(auth_service, "_LOGIN_LIMITER", self._limiter()),
            patch.object(auth_service, "_password_verify", return_value=False),
            patch.object(auth_service.supabase_client, "is_enabled", return_value=True),
            patch.object(
                auth_service.supabase_client,
                "get_rows",
                return_value=[{"user_id": "u_random", "password_hash": "encoded"}],
            ),
        ):
            wrong = auth_service.login_user("13900139000", "secret1")

        self.assertEqual(invalid[1], auth_service.LOGIN_FAILED_MESSAGE)
        self.assertEqual(missing[1], auth_service.LOGIN_FAILED_MESSAGE)
        self.assertEqual(wrong[1], auth_service.LOGIN_FAILED_MESSAGE)
        for result in (invalid, missing, wrong):
            self.assertNotIn("13800138000", result[1])
            self.assertIsNone(result[2])

    def test_repeated_failures_lock_before_another_database_read(self) -> None:
        get_rows = Mock(return_value=[])
        with (
            patch.object(auth_service, "_LOGIN_LIMITER", self._limiter(max_failures=2)),
            patch.object(auth_service, "_password_verify", return_value=False),
            patch.object(auth_service.supabase_client, "is_enabled", return_value=True),
            patch.object(auth_service.supabase_client, "get_rows", get_rows),
        ):
            first = auth_service.login_user("13800138000", "secret1")
            second = auth_service.login_user("13800138000", "secret1")
            third = auth_service.login_user("13800138000", "secret1")

        self.assertEqual(first[1], auth_service.LOGIN_FAILED_MESSAGE)
        self.assertEqual(second[1], auth_service.LOGIN_THROTTLED_MESSAGE)
        self.assertEqual(third[1], auth_service.LOGIN_THROTTLED_MESSAGE)
        self.assertEqual(get_rows.call_count, 2)

    def test_password_parser_rejects_unbounded_work_factor(self) -> None:
        encoded = "pbkdf2_sha256$999999999$0011223344556677$" + ("00" * 32)
        with patch.object(auth_service.hashlib, "pbkdf2_hmac") as pbkdf2:
            self.assertFalse(auth_service._password_verify("secret1", encoded))
        pbkdf2.assert_not_called()

    def test_success_upgrades_legacy_hash_without_putting_hash_in_query(self) -> None:
        update_rows = Mock(return_value=SimpleNamespace(status_code=200))
        with (
            patch.object(auth_service, "_LOGIN_LIMITER", self._limiter()),
            patch.object(auth_service, "_password_verify", return_value=True),
            patch.object(auth_service, "_password_needs_rehash", return_value=True),
            patch.object(auth_service, "_password_hash", return_value="new-hash"),
            patch.object(auth_service.supabase_client, "is_enabled", return_value=True),
            patch.object(
                auth_service.supabase_client,
                "get_rows",
                return_value=[{"user_id": "u_random", "password_hash": "legacy-hash"}],
            ),
            patch.object(auth_service.supabase_client, "update_rows", update_rows),
        ):
            ok, _, user_id = auth_service.login_user("13800138000", "secret1")

        self.assertTrue(ok)
        self.assertEqual(user_id, "u_random")
        self.assertEqual(update_rows.call_args.args[2], {"user_id": "eq.u_random"})

    def test_new_user_id_is_opaque(self) -> None:
        phone = "13800138000"
        user_id = auth_service._new_user_id()
        self.assertTrue(user_id.startswith("u_"))
        self.assertNotIn(phone, user_id)
        self.assertEqual(len(user_id), 34)

    def test_registration_conflict_does_not_confirm_account(self) -> None:
        response = SimpleNamespace(status_code=409)
        with (
            patch.object(auth_service.supabase_client, "is_enabled", return_value=True),
            patch.object(auth_service.supabase_client, "insert_row", return_value=response),
            patch.object(auth_service, "_password_hash", return_value="hash"),
        ):
            ok, message, user_id = auth_service.register_user("13800138000", "long-secret-1")

        self.assertFalse(ok)
        self.assertIsNone(user_id)
        self.assertNotIn("13800138000", message)
        self.assertNotIn("已注册", message)


class AuthSessionSecurityTests(unittest.TestCase):
    def test_v2_cookie_is_opaque_and_tamper_evident(self) -> None:
        sid = "a" * 32
        with patch.object(auth_guard, "_auth_cookie_secret", return_value="s" * 40):
            token = auth_guard._build_opaque_session_token(sid)
            self.assertTrue(token.startswith("v2."))
            self.assertEqual(auth_guard._extract_opaque_session_id(token), sid)
            replacement = "0" if token[-1] != "0" else "1"
            self.assertEqual(auth_guard._extract_opaque_session_id(token[:-1] + replacement), "")

        self.assertNotIn("13800138000", token)
        self.assertNotIn("u_13800138000", token)

    def test_persisted_session_cookie_has_no_identity_and_key_is_hashed(self) -> None:
        fixed_now = datetime(2026, 7, 24, 12, 0, 0)
        with tempfile.TemporaryDirectory() as tmp:
            sessions_path = str(Path(tmp) / "sessions.json")
            with (
                patch.object(auth_guard, "_sessions_path", return_value=sessions_path),
                patch.object(auth_guard, "_auth_cookie_secret", return_value="s" * 40),
                patch.object(auth_guard, "_now", return_value=fixed_now),
            ):
                token = auth_guard._persist_login_session("13800138000", "u_legacy-phone")
                storage_key = auth_guard._session_storage_key(token)
                opaque_sid = auth_guard._extract_opaque_session_id(token)

            data = load_json(sessions_path, default={})

        self.assertNotIn("13800138000", token)
        self.assertNotIn("u_legacy-phone", token)
        self.assertIn(storage_key, data["sessions"])
        self.assertNotIn(opaque_sid, data["sessions"])
        self.assertNotIn("13800138000", str(data))
        self.assertEqual(data["sessions"][storage_key]["phone_masked"], "138****8000")
        self.assertEqual(data["sessions"][storage_key]["user_id"], "u_legacy-phone")


if __name__ == "__main__":
    unittest.main()
