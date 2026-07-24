from __future__ import annotations

import unittest
from unittest.mock import patch

from scripts.preflight import run_checks


class PreflightTests(unittest.TestCase):
    def test_local_preflight_allows_optional_cloud_secrets(self) -> None:
        with patch("scripts.preflight._secret", return_value=""):
            checks = run_checks(require_cloud=False)

        self.assertFalse([
            check for check in checks
            if check.required and not check.ok
        ])

    def test_production_preflight_requires_independent_cookie_secret(self) -> None:
        values = {
            "SUPABASE_URL": "https://example.supabase.co",
            "SUPABASE_KEY": "same-secret",
            "AUTH_COOKIE_SECRET": "same-secret",
        }
        with patch("scripts.preflight._secret", side_effect=lambda key: values.get(key, "")):
            checks = run_checks(require_cloud=True)

        failed_names = {check.name for check in checks if check.required and not check.ok}
        self.assertIn("auth-cookie-secret", failed_names)
        self.assertIn("secret-separation", failed_names)


if __name__ == "__main__":
    unittest.main()
