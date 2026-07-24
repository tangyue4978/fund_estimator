from __future__ import annotations

import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch

from config import constants
from domain.estimate import EstimateResult
from services.portfolio_service import portfolio_realtime_view_as_of


def _snapshot(code: str, *, shares: float = 10.0, cost_nav: float = 1.0) -> SimpleNamespace:
    return SimpleNamespace(
        code=code,
        shares_end=shares,
        avg_cost_nav_end=cost_nav,
        realized_pnl_end=0.0,
    )


def _estimate(code: str, nav: float) -> EstimateResult:
    return EstimateResult(
        code=code,
        name=code,
        est_nav=nav,
        est_change_pct=0.0,
        method=constants.METHOD_OFFICIAL_GSZ if nav > 0 else constants.METHOD_FROZEN_NAV,
        confidence=0.0 if nav <= 0 else 0.5,
        warning="估值缺失" if nav <= 0 else "",
        suggested_refresh_sec=60,
        est_time="2026-07-24T15:00:00",
    )


class PortfolioFallbackTests(unittest.TestCase):
    def test_missing_nav_uses_cost_nav_instead_of_showing_total_loss(self) -> None:
        snapshots = [_snapshot("000001", shares=10.0, cost_nav=2.0)]
        with (
            patch("services.portfolio_service.now_cn", return_value=datetime(2026, 7, 24, 15, 30)),
            patch("services.portfolio_service.build_positions_as_of", return_value=snapshots),
            patch("services.portfolio_service.estimate_many", return_value={"000001": _estimate("000001", 0.0)}),
        ):
            view = portfolio_realtime_view_as_of("2026-07-24")

        self.assertEqual(view["total_cost"], 20.0)
        self.assertEqual(view["total_est_value"], 20.0)
        self.assertEqual(view["total_est_pnl"], 0.0)
        self.assertEqual(view["realtime_coverage_value_pct"], 0.0)
        self.assertIn("按成本净值显示", view["positions"][0]["warning"])

    def test_coverage_includes_unvalued_positions_in_denominator(self) -> None:
        snapshots = [_snapshot("A"), _snapshot("B")]
        estimates = {"A": _estimate("A", 2.0), "B": _estimate("B", 0.0)}
        with (
            patch("services.portfolio_service.now_cn", return_value=datetime(2026, 7, 24, 15, 30)),
            patch("services.portfolio_service.build_positions_as_of", return_value=snapshots),
            patch("services.portfolio_service.estimate_many", return_value=estimates),
        ):
            view = portfolio_realtime_view_as_of("2026-07-24")

        self.assertAlmostEqual(view["total_est_value"], 30.0)
        self.assertAlmostEqual(view["realtime_coverage_value_pct"], 100.0 * 20.0 / 30.0)

    def test_historical_missing_ledger_uses_cost_without_claiming_coverage(self) -> None:
        snapshots = [_snapshot("000001", shares=10.0, cost_nav=2.0)]
        with (
            patch("services.portfolio_service.now_cn", return_value=datetime(2026, 7, 24, 15, 30)),
            patch("services.portfolio_service.build_positions_as_of", return_value=snapshots),
            patch("services.portfolio_service._load_daily_ledger_map", return_value={}),
        ):
            view = portfolio_realtime_view_as_of("2026-07-23")

        self.assertEqual(view["total_est_value"], 20.0)
        self.assertEqual(view["total_est_pnl"], 0.0)
        self.assertEqual(view["realtime_coverage_value_pct"], 0.0)
        self.assertIn("按成本净值显示", view["positions"][0]["warning"])


if __name__ == "__main__":
    unittest.main()
