from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from config import constants
from datasources.nav_api import _parse_networth_trend
from services.estimation_service import _estimate_by_holdings, _estimate_from_gsz


class EstimateFallbackTests(unittest.TestCase):
    def test_uses_latest_official_nav_when_realtime_quote_is_missing(self) -> None:
        with patch(
            "services.estimation_service._latest_official_nav",
            return_value=(1.2345, "2026-07-23"),
        ):
            result = _estimate_from_gsz("000001", "测试基金", None, method=constants.METHOD_OFFICIAL_GSZ)

        self.assertEqual(result.method, constants.METHOD_FROZEN_NAV)
        self.assertEqual(result.est_nav, 1.2345)
        self.assertEqual(result.est_change_pct, 0.0)
        self.assertEqual(result.est_time, "2026-07-23")
        self.assertGreater(result.confidence, 0.0)
        self.assertIn("官方净值", result.warning)

    def test_reuses_quote_nav_without_an_extra_official_nav_request(self) -> None:
        quote = SimpleNamespace(gsz=0.0, gszzl=0.0, nav=1.1111, gztime="2026-07-23T15:00:00")
        with patch("services.estimation_service._latest_official_nav") as latest_nav:
            result = _estimate_from_gsz(
                "000001",
                "测试基金",
                quote,
                method=constants.METHOD_OFFICIAL_GSZ,
            )

        latest_nav.assert_not_called()
        self.assertEqual(result.est_nav, 1.1111)
        self.assertEqual(result.est_time, "2026-07-23T15:00:00")

    def test_reports_unavailable_when_no_valuation_source_exists(self) -> None:
        with patch("services.estimation_service._latest_official_nav", return_value=(0.0, None)):
            result = _estimate_from_gsz("000001", "测试基金", None, method=constants.METHOD_OFFICIAL_GSZ)

        self.assertEqual(result.est_nav, 0.0)
        self.assertEqual(result.confidence, 0.0)
        self.assertIn("均不可用", result.warning)

    def test_stale_realtime_cache_is_not_reported_as_live_valuation(self) -> None:
        quote = SimpleNamespace(
            gsz=1.5,
            gszzl=8.0,
            nav=1.2,
            gztime="2026-07-20T15:00:00",
            stale=True,
        )
        with patch(
            "services.estimation_service._latest_official_nav",
            return_value=(1.25, "2026-07-23"),
        ):
            result = _estimate_from_gsz(
                "000001",
                "测试基金",
                quote,
                method=constants.METHOD_OFFICIAL_GSZ,
            )

        self.assertEqual(result.method, constants.METHOD_FROZEN_NAV)
        self.assertEqual(result.est_nav, 1.25)
        self.assertEqual(result.est_change_pct, 0.0)

    def test_partial_holdings_are_not_scaled_up_to_the_whole_fund(self) -> None:
        holdings = {
            "as_of": "2026-06-30",
            "holdings": [
                {"code": "600001", "weight_pct": 20.0},
                {"code": "600002", "weight_pct": 20.0},
            ],
        }
        quotes = {
            "600001": SimpleNamespace(change_pct=10.0),
            "600002": SimpleNamespace(change_pct=0.0),
        }
        result = _estimate_by_holdings(
            "000001",
            "测试基金",
            holdings,
            quotes,
            SimpleNamespace(nav=1.0),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertAlmostEqual(result.est_change_pct, 2.0)
        self.assertAlmostEqual(result.est_nav, 1.02)
        self.assertAlmostEqual(result.realtime_coverage_value_pct, 40.0)

    def test_official_nav_timestamp_uses_china_trading_date(self) -> None:
        js_text = 'var Data_netWorthTrend = [{"x":1784736000000,"y":1.369}];'
        items = _parse_networth_trend("000001", js_text)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].nav_date, "2026-07-23")


if __name__ == "__main__":
    unittest.main()
