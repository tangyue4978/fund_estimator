from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from config import constants
from domain.estimate import EstimateResult
from services.settlement_service import finalize_estimated_close, settle_pending_days


def _estimate(code: str, nav: float) -> EstimateResult:
    return EstimateResult(
        code=code,
        name=code,
        est_nav=nav,
        est_change_pct=0.0,
        method=constants.METHOD_FROZEN_NAV,
        confidence=0.0,
        warning="",
        suggested_refresh_sec=60,
        est_time="2026-07-24",
    )


class SettlementSafetyTests(unittest.TestCase):
    def test_pending_scan_fetches_each_fund_history_once_and_writes_once(self) -> None:
        rows = [
            {
                "date": date_str,
                "code": code,
                "shares_end": 10.0,
                "avg_cost_nav_end": 1.0,
                "realized_pnl_end": 0.0,
                "settle_status": constants.SETTLE_ESTIMATED_ONLY,
            }
            for date_str in ("2026-07-22", "2026-07-23", "2026-07-24")
            for code in ("000001", "000002")
        ]

        def official_history(code: str, *, days_back: int):
            self.assertEqual(days_back, 180)
            return [
                SimpleNamespace(code=code, nav_date=date_str, nav=1.1)
                for date_str in ("2026-07-22", "2026-07-23", "2026-07-24")
            ]

        with (
            patch("services.settlement_service._today_str", return_value="2026-07-24"),
            patch("services.settlement_service.supabase_client.is_enabled", return_value=True),
            patch("services.settlement_service.paths.current_user_id", return_value="user-1"),
            patch(
                "services.settlement_service.supabase_client.get_rows_paginated",
                side_effect=[rows, []],
            ) as get_rows,
            patch(
                "services.settlement_service.fetch_official_navs",
                side_effect=official_history,
            ) as fetch_navs,
            patch(
                "services.settlement_service.supabase_client.upsert_rows",
                return_value=SimpleNamespace(status_code=201),
            ) as upsert_rows,
            patch("services.settlement_service._read_cached_ledger", return_value={"items": [], "cached_at": 0.0}),
            patch("services.settlement_service._write_cached_ledger"),
            patch("services.settlement_service._clear_ledger_cache"),
            patch("services.settlement_service.get_cloud_error", return_value=""),
            patch("services.settlement_service.clear_cloud_error"),
        ):
            _, settled = settle_pending_days(7)

        self.assertEqual(settled, 6)
        self.assertEqual(fetch_navs.call_count, 2)
        self.assertEqual([call.args[0] for call in fetch_navs.call_args_list], ["000001", "000002"])
        upsert_rows.assert_called_once()
        self.assertEqual(len(upsert_rows.call_args.args[1]), 6)
        self.assertEqual(get_rows.call_count, 2)
        pending_params = get_rows.call_args_list[0].kwargs["params"]
        self.assertEqual(pending_params["settle_status"], f"eq.{constants.SETTLE_ESTIMATED_ONLY}")
        self.assertEqual(
            pending_params["and"],
            "(date.gte.2026-07-18,date.lte.2026-07-24)",
        )

    def test_pending_scan_read_failure_never_fetches_or_writes(self) -> None:
        with (
            patch("services.settlement_service._today_str", return_value="2026-07-24"),
            patch("services.settlement_service.supabase_client.is_enabled", return_value=True),
            patch("services.settlement_service.paths.current_user_id", return_value="user-1"),
            patch(
                "services.settlement_service.supabase_client.get_rows_paginated",
                side_effect=RuntimeError("read failed"),
            ),
            patch("services.settlement_service.fetch_official_navs") as fetch_navs,
            patch("services.settlement_service.supabase_client.upsert_rows") as upsert_rows,
        ):
            with self.assertRaisesRegex(RuntimeError, "settle_pending_days cloud failed"):
                settle_pending_days(7)

        fetch_navs.assert_not_called()
        upsert_rows.assert_not_called()

    def test_rejects_historical_date_before_using_current_quotes(self) -> None:
        with patch("services.settlement_service._today_str", return_value="2026-07-24"):
            with self.assertRaisesRegex(ValueError, "历史日期"):
                finalize_estimated_close("2026-07-23")

    def test_missing_nav_aborts_without_deleting_or_upserting(self) -> None:
        snapshot = SimpleNamespace(
            code="000001",
            shares_end=10.0,
            avg_cost_nav_end=1.0,
            realized_pnl_end=0.0,
        )
        with (
            patch("services.settlement_service._today_str", return_value="2026-07-24"),
            patch("services.settlement_service.supabase_client.is_enabled", return_value=True),
            patch("services.settlement_service.build_positions_as_of", return_value=[snapshot]),
            patch("services.settlement_service.get_cloud_error", return_value=""),
            patch("services.settlement_service.paths.current_user_id", return_value="user-1"),
            patch("services.settlement_service.supabase_client.get_rows", return_value=[]),
            patch("services.settlement_service.estimate_many", return_value={"000001": _estimate("000001", 0.0)}),
            patch("services.settlement_service.supabase_client.delete_rows") as delete_rows,
            patch("services.settlement_service.supabase_client.upsert_rows") as upsert_rows,
        ):
            with self.assertRaisesRegex(RuntimeError, "缺少有效实时收盘估值"):
                finalize_estimated_close("2026-07-24")

        delete_rows.assert_not_called()
        upsert_rows.assert_not_called()

    def test_frozen_official_fallback_is_not_written_as_realtime_close(self) -> None:
        snapshot = SimpleNamespace(
            code="000001",
            shares_end=10.0,
            avg_cost_nav_end=1.0,
            realized_pnl_end=0.0,
        )
        frozen = _estimate("000001", 1.2)
        frozen.method = constants.METHOD_FROZEN_NAV
        with (
            patch("services.settlement_service._today_str", return_value="2026-07-24"),
            patch("services.settlement_service.supabase_client.is_enabled", return_value=True),
            patch("services.settlement_service.build_positions_as_of", return_value=[snapshot]),
            patch("services.settlement_service.get_cloud_error", return_value=""),
            patch("services.settlement_service.paths.current_user_id", return_value="user-1"),
            patch("services.settlement_service.supabase_client.get_rows", return_value=[]),
            patch("services.settlement_service.estimate_many", return_value={"000001": frozen}),
            patch("services.settlement_service.supabase_client.delete_rows") as delete_rows,
            patch("services.settlement_service.supabase_client.upsert_rows") as upsert_rows,
        ):
            with self.assertRaisesRegex(RuntimeError, "缺少有效实时收盘估值"):
                finalize_estimated_close("2026-07-24")

        delete_rows.assert_not_called()
        upsert_rows.assert_not_called()

    def test_empty_snapshot_never_bulk_deletes_existing_ledger(self) -> None:
        with (
            patch("services.settlement_service._today_str", return_value="2026-07-24"),
            patch("services.settlement_service.supabase_client.is_enabled", return_value=True),
            patch("services.settlement_service.build_positions_as_of", return_value=[]),
            patch("services.settlement_service.get_cloud_error", return_value=""),
            patch("services.settlement_service.paths.current_user_id", return_value="user-1"),
            patch(
                "services.settlement_service.supabase_client.get_rows",
                return_value=[{"date": "2026-07-24", "code": "000001"}],
            ),
            patch(
                "services.settlement_service._load_ledger",
                return_value={"items": [{"date": "2026-07-24", "code": "000001"}]},
            ),
            patch("services.settlement_service.supabase_client.delete_rows") as delete_rows,
        ):
            ledger = finalize_estimated_close("2026-07-24")

        self.assertEqual(len(ledger["items"]), 1)
        delete_rows.assert_not_called()

    def test_failed_replacement_upsert_does_not_delete_stale_rows_first(self) -> None:
        snapshot = SimpleNamespace(
            code="000001",
            shares_end=10.0,
            avg_cost_nav_end=1.0,
            realized_pnl_end=0.0,
        )
        realtime = _estimate("000001", 1.2)
        realtime.method = constants.METHOD_OFFICIAL_GSZ
        realtime.confidence = 0.9
        existing_rows = [
            {"date": "2026-07-24", "code": "000002", "settle_status": constants.SETTLE_ESTIMATED_ONLY}
        ]
        with (
            patch("services.settlement_service._today_str", return_value="2026-07-24"),
            patch("services.settlement_service.supabase_client.is_enabled", return_value=True),
            patch("services.settlement_service.build_positions_as_of", return_value=[snapshot]),
            patch("services.settlement_service.get_cloud_error", return_value=""),
            patch("services.settlement_service.paths.current_user_id", return_value="user-1"),
            patch("services.settlement_service.supabase_client.get_rows", return_value=existing_rows),
            patch("services.settlement_service.estimate_many", return_value={"000001": realtime}),
            patch(
                "services.settlement_service.supabase_client.upsert_rows",
                return_value=SimpleNamespace(status_code=500),
            ),
            patch("services.settlement_service.supabase_client.delete_rows") as delete_rows,
        ):
            with self.assertRaisesRegex(RuntimeError, "finalize upsert failed"):
                finalize_estimated_close("2026-07-24")

        delete_rows.assert_not_called()


if __name__ == "__main__":
    unittest.main()
