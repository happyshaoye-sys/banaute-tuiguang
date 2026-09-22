"""Regression tests for fixed-window rolling daily metrics."""
from __future__ import annotations

from datetime import date, timedelta
import unittest

import pandas as pd

from core.periods import apply_rolling_daily_metrics


class RollingDailyMetricTests(unittest.TestCase):
    def test_visitor_averages_use_fixed_windows_and_preserve_selected_period_total(self):
        end = date(2026, 9, 22)
        day_map = {}
        for offset in range(15):
            current = end - timedelta(days=14 - offset)
            day_map[current.isoformat()] = (
                pd.DataFrame(
                    [
                        {
                            "宝贝ID": "1001",
                            "商品访客数": offset + 1,
                            "支付件数": 2,
                            "付费": 3,
                        }
                    ]
                ),
            )

        source = pd.DataFrame(
            [
                {"宝贝ID": "1001", "商品访客数": 999, "在仓库存": 28},
                {"宝贝ID": "9999", "商品访客数": 555, "在仓库存": 10},
            ]
        )

        result = apply_rolling_daily_metrics(source, day_map, end.isoformat())

        self.assertEqual(result["宝贝ID"].tolist(), ["1001", "9999"])
        self.assertEqual(result["商品访客数"].tolist(), [999, 555])
        self.assertEqual(result.loc[0, "日均访客3"], 14.0)
        self.assertEqual(result.loc[0, "日均访客7"], 12.0)
        self.assertEqual(result.loc[0, "日均访客15"], 8.0)
        self.assertEqual(result.loc[0, "日销3"], 2.0)
        self.assertEqual(result.loc[0, "花费3"], 3.0)
        self.assertEqual(result.loc[0, "可售天数"], 14.0)
        self.assertEqual(result.loc[1, "日均访客3"], 0.0)
        self.assertEqual(result.loc[1, "日均访客7"], 0.0)
        self.assertEqual(result.loc[1, "日均访客15"], 0.0)

    def test_missing_calendar_days_contribute_zero_with_fixed_denominator(self):
        day_map = {
            "2026-09-21": (pd.DataFrame([{"宝贝ID": "1001", "商品访客数": 6}]),),
            "2026-09-22": (pd.DataFrame([{"宝贝ID": "1001", "商品访客数": 9}]),),
        }
        source = pd.DataFrame([{"宝贝ID": "1001"}])

        result = apply_rolling_daily_metrics(source, day_map, "2026-09-22")

        self.assertEqual(result.loc[0, "日均访客3"], 5.0)
        self.assertAlmostEqual(result.loc[0, "日均访客7"], 15.0 / 7.0)
        self.assertEqual(result.loc[0, "日均访客15"], 1.0)


if __name__ == "__main__":
    unittest.main()
