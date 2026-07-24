from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from services import supabase_client
from services.adjustment_service import add_adjustment, list_adjustments
from services.snapshot_service import build_positions_as_of_safe


class SupabasePaginationTests(unittest.TestCase):
    def test_paginated_read_collects_every_page(self) -> None:
        source = [{"id": i} for i in range(2500)]

        def read_page(_table: str, params: dict | None = None) -> list[dict]:
            query = params or {}
            offset = int(query.get("offset", 0))
            limit = int(query.get("limit", 1000))
            return source[offset : offset + limit]

        with patch("services.supabase_client.get_rows", side_effect=read_page) as get_rows:
            rows = supabase_client.get_rows_paginated(
                "app_adjustments",
                params={"order": "id.asc"},
            )

        self.assertEqual(len(rows), 2500)
        self.assertEqual(get_rows.call_count, 3)

    def test_explicit_limit_does_not_probe_or_raise(self) -> None:
        with patch(
            "services.supabase_client.get_rows",
            return_value=[{"id": i} for i in range(10)],
        ) as get_rows:
            rows = supabase_client.get_rows_paginated(
                "app_adjustments",
                params={"limit": "10", "order": "id.asc"},
            )

        self.assertEqual(len(rows), 10)
        get_rows.assert_called_once()

    def test_http_error_message_does_not_include_query_url(self) -> None:
        response = SimpleNamespace(status_code=401)
        with (
            patch(
                "services.supabase_client.get_config",
                return_value=("https://private-project.supabase.co", "secret"),
            ),
            patch("services.supabase_client._SESSION.get", return_value=response),
        ):
            with self.assertRaises(supabase_client.SupabaseRequestError) as raised:
                supabase_client.get_rows(
                    "app_watchlist",
                    params={"user_id": "eq.private-user"},
                )

        message = str(raised.exception)
        self.assertNotIn("private-project", message)
        self.assertNotIn("private-user", message)
        self.assertIn("401", message)


class AdjustmentQueryTests(unittest.TestCase):
    def test_adjustment_write_rejects_invalid_financial_values_before_cloud(self) -> None:
        invalid_cases = [
            {"code": "123", "effective_date": "2026-07-24", "shares": 1, "price": 1},
            {"code": "123456", "effective_date": "not-a-date", "shares": 1, "price": 1},
            {"code": "123456", "effective_date": "2026-07-24", "shares": float("nan"), "price": 1},
            {"code": "123456", "effective_date": "2026-07-24", "shares": 1, "price": float("inf")},
        ]
        with patch("services.adjustment_service.supabase_client.insert_row") as insert_row:
            for payload in invalid_cases:
                with self.subTest(payload=payload), self.assertRaises(ValueError):
                    add_adjustment(type="BUY", **payload)

        insert_row.assert_not_called()

    def test_adjustment_write_limits_note_length(self) -> None:
        with patch("services.adjustment_service.supabase_client.insert_row") as insert_row:
            with self.assertRaises(ValueError):
                add_adjustment(
                    type="CASH_ADJ",
                    code="123456",
                    effective_date="2026-07-24",
                    cash=1,
                    note="x" * 501,
                )

        insert_row.assert_not_called()

    def test_adjustments_are_filtered_on_server_for_snapshot_date(self) -> None:
        with (
            patch("services.adjustment_service.supabase_client.is_enabled", return_value=True),
            patch(
                "services.adjustment_service.supabase_client.get_rows_paginated",
                return_value=[],
            ) as get_rows,
        ):
            rows = list_adjustments(through_date="2026-07-24")

        self.assertEqual(rows, [])
        params = get_rows.call_args.kwargs["params"]
        self.assertEqual(params["effective_date"], "lte.2026-07-24")
        self.assertEqual(params["order"], "effective_date.asc,created_at.asc,id.asc")

    def test_snapshot_requests_only_rows_through_target_date(self) -> None:
        with patch("services.snapshot_service.list_adjustments", return_value=[]) as list_rows:
            result = build_positions_as_of_safe("2026-07-24")

        self.assertEqual(result.positions, [])
        list_rows.assert_called_once_with(through_date="2026-07-24")


if __name__ == "__main__":
    unittest.main()
