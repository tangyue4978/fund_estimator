from __future__ import annotations

import unittest
from datetime import datetime
from unittest.mock import patch

from config import constants
from services.accuracy_service import (
    clear_ledger_query_cache,
    fund_gap_summary,
    fund_gap_table,
)


class AccuracyPerformanceTests(unittest.TestCase):
    def setUp(self) -> None:
        clear_ledger_query_cache()

    def tearDown(self) -> None:
        clear_ledger_query_cache()

    def test_summary_and_table_reuse_one_server_filtered_query(self) -> None:
        rows = [
            {
                "date": "2026-07-23",
                "code": "000001",
                "shares_end": 10.0,
                "estimated_nav_close": 1.0,
                "official_nav": 1.01,
                "settle_status": constants.SETTLE_SETTLED,
            }
        ]
        with (
            patch("services.accuracy_service.supabase_client.is_enabled", return_value=True),
            patch("services.accuracy_service.paths.current_user_id", return_value="user-1"),
            patch(
                "services.accuracy_service.now_cn",
                return_value=datetime(2026, 7, 24, 16, 0),
            ),
            patch(
                "services.accuracy_service.supabase_client.get_rows_paginated",
                return_value=rows,
            ) as get_rows,
        ):
            summary = fund_gap_summary("000001", days_back=30)
            table = fund_gap_table("000001", days_back=30)

        self.assertEqual(summary["count"], 1)
        self.assertEqual(len(table), 1)
        get_rows.assert_called_once()
        params = get_rows.call_args.kwargs["params"]
        self.assertEqual(params["user_id"], "eq.user-1")
        self.assertEqual(params["code"], "eq.000001")
        self.assertEqual(params["date"], "gte.2026-06-24")
        self.assertEqual(params["order"], "date.asc,code.asc")


if __name__ == "__main__":
    unittest.main()
