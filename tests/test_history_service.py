from __future__ import annotations

import unittest
from datetime import datetime
from unittest.mock import patch

from config import constants
from services.history_service import get_history, get_latest_fund_cumulative_pnl_before


class HistoryServiceTests(unittest.TestCase):
    def test_history_uses_code_and_date_filters_on_the_server(self) -> None:
        with (
            patch("services.history_service.supabase_client.is_enabled", return_value=True),
            patch("services.history_service.paths.current_user_id", return_value="user-1"),
            patch(
                "services.history_service.supabase_client.get_rows_paginated",
                return_value=[],
            ) as get_rows,
            patch("services.history_service.now_cn", return_value=datetime(2026, 7, 24, 16, 0)),
            patch("services.history_service.clear_cloud_error"),
        ):
            history = get_history("000001", days=7)

        self.assertEqual(history, [])
        get_rows.assert_called_once()
        params = get_rows.call_args.kwargs["params"]
        self.assertEqual(params["code"], "eq.000001")
        self.assertEqual(params["and"], "(date.gte.2026-07-18,date.lte.2026-07-24)")
        self.assertEqual(params["order"], "date.asc,code.asc")

    def test_previous_pnl_requests_only_the_latest_eligible_row(self) -> None:
        with (
            patch("services.history_service.supabase_client.is_enabled", return_value=True),
            patch("services.history_service.paths.current_user_id", return_value="user-1"),
            patch(
                "services.history_service.supabase_client.get_rows_paginated",
                return_value=[],
            ) as get_rows,
            patch("services.history_service.clear_cloud_error"),
        ):
            value = get_latest_fund_cumulative_pnl_before("000001", "2026-07-24")

        self.assertIsNone(value)
        get_rows.assert_called_once()
        params = get_rows.call_args.kwargs["params"]
        self.assertEqual(params["code"], "eq.000001")
        self.assertEqual(params["date"], "lt.2026-07-24")
        self.assertEqual(params["order"], "date.desc,code.asc")
        self.assertEqual(params["limit"], "1")
        self.assertIn("official_pnl.not.is.null", params["or"])

    def test_previous_pnl_uses_latest_available_trading_day(self) -> None:
        rows = [
            {
                "date": "2026-07-17",
                "code": "000001",
                "settle_status": constants.SETTLE_SETTLED,
                "official_pnl": 12.5,
            },
            {
                "date": "2026-07-20",
                "code": "000001",
                "settle_status": constants.SETTLE_ESTIMATED_ONLY,
                "estimated_pnl_close": 15.0,
            },
        ]
        with patch("services.history_service._load_ledger_items", return_value=rows):
            value = get_latest_fund_cumulative_pnl_before("000001", "2026-07-21")

        self.assertEqual(value, 15.0)

    def test_history_omits_zero_nav_rows_instead_of_drawing_a_zero_cliff(self) -> None:
        rows = [
            {
                "date": "2026-07-23",
                "code": "000001",
                "settle_status": constants.SETTLE_ESTIMATED_ONLY,
                "estimated_nav_close": 0.0,
            },
            {
                "date": "2026-07-24",
                "code": "000001",
                "settle_status": constants.SETTLE_SETTLED,
                "official_nav": 1.2,
            },
        ]
        with (
            patch("services.history_service._load_ledger_items", return_value=rows),
            patch("services.history_service.now_cn", return_value=datetime(2026, 7, 24, 16, 0)),
        ):
            history = get_history("000001", days=2)

        self.assertEqual(history, [{
            "date": "2026-07-24",
            "nav": 1.2,
            "source": "official",
            "settle_status": constants.SETTLE_SETTLED,
        }])


if __name__ == "__main__":
    unittest.main()
