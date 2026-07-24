from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from config import constants
from domain.estimate import EstimateResult
from services import chart_service, intraday_service


def _estimate(*, nav: float = 1.2, est_time: str = "2026-07-24 10:00:00") -> EstimateResult:
    return EstimateResult(
        code="000001",
        name="测试基金",
        est_nav=nav,
        est_change_pct=1.0,
        method=constants.METHOD_OFFICIAL_GSZ,
        confidence=0.9,
        warning="",
        suggested_refresh_sec=30,
        est_time=est_time,
    )


class IntradayServiceTests(unittest.TestCase):
    def test_records_deduplicates_and_bounds_fund_points(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "intraday.json")
            with (
                patch("services.intraday_service.paths.file_intraday", return_value=target),
                patch("services.intraday_service._now_hhmmss", side_effect=["10:00:00", "10:00:01", "10:00:02", "10:00:03"]),
                patch(
                    "services.intraday_service.now_cn",
                    return_value=datetime(2026, 7, 24, 10, 0, 0),
                ),
                patch("services.intraday_service._MAX_POINTS_PER_TARGET", 2),
            ):
                intraday_service.record_intraday_point("000001", estimate=_estimate())
                intraday_service.record_intraday_point("000001", estimate=_estimate())
                intraday_service.record_intraday_point(
                    "000001",
                    estimate=_estimate(nav=1.21, est_time="2026-07-24 10:01:00"),
                )
                intraday_service.record_intraday_point(
                    "000001",
                    estimate=_estimate(nav=1.22, est_time="2026-07-24 10:02:00"),
                )
                points = intraday_service.get_intraday_series("000001", date_str="2026-07-24")

        self.assertEqual(len(points), 2)
        self.assertEqual([point["est_nav"] for point in points], [1.21, 1.22])
        self.assertEqual(points[-1]["date"], "2026-07-24 10:00:03")

    def test_realtime_chart_uses_persisted_points_when_provider_is_down(self) -> None:
        with (
            patch(
                "services.chart_service.intraday_load_fund_series",
                return_value=[
                    {"date": "2026-07-24 10:00:00", "est_nav": 1.20},
                    {"date": "2026-07-24 10:01:00", "est_nav": 1.21},
                ],
            ),
            patch("services.chart_service.get_gsz_quote", return_value=None),
            patch(
                "services.chart_service.now_cn",
                return_value=datetime(2026, 7, 24, 10, 2, 0),
            ),
        ):
            points = chart_service._load_realtime_series("000001")

        self.assertEqual(
            points,
            [
                {"date": "2026-07-24 10:00:00", "value": 1.20},
                {"date": "2026-07-24 10:01:00", "value": 1.21},
            ],
        )


if __name__ == "__main__":
    unittest.main()
