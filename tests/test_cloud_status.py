from __future__ import annotations

import unittest

from services.cloud_status_service import clear_cloud_error, get_cloud_error, set_cloud_error


class CloudStatusTests(unittest.TestCase):
    def tearDown(self) -> None:
        clear_cloud_error("test")

    def test_user_facing_error_does_not_expose_raw_request_details(self) -> None:
        raw = "401 https://example.supabase.co/rest/v1/table?user_id=eq.private-user"
        set_cloud_error("test", raw)

        message = get_cloud_error("test")

        self.assertIn("权限异常", message)
        self.assertNotIn("private-user", message)
        self.assertNotIn("example.supabase.co", message)


if __name__ == "__main__":
    unittest.main()
