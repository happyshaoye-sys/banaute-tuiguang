"""Direct-purchase metric lineage and aggregation contract tests."""
import unittest

import pandas as pd

from core.loader import _filter_source_columns, prepare_source
from core.merge import merge_sources
from core.periods import aggregate_period_frame
from core.schema import SOURCE_WUJIE_SUBJECT


class DirectPurchaseMetricTests(unittest.TestCase):
    def test_wujie_direct_cart_count_is_the_quantity_source(self):
        raw = pd.DataFrame({
            "宝贝ID": ["1001"],
            "主体类型": ["商品"],
            "转化周期": ["15天"],
            "付费": [120.0],
            "直接购物车数": [6],
            "直接成交笔数": [99],
            "总成交成本": [888.0],
        })

        filtered = _filter_source_columns(raw, SOURCE_WUJIE_SUBJECT)
        prepared = prepare_source(filtered, SOURCE_WUJIE_SUBJECT)

        self.assertIn("直购数量", prepared.columns)
        self.assertEqual(float(prepared.loc[0, "直购数量"]), 6.0)
        self.assertNotIn("直接成交笔数", filtered.columns)
        self.assertNotIn("总成交成本", filtered.columns)

    def test_daily_cost_is_total_spend_divided_by_total_quantity(self):
        raw = pd.DataFrame({
            "宝贝ID": ["1001", "1001", "1001", "1001", "1002"],
            "主体类型": ["商品", "商品", "商品", "店铺", "商品"],
            "转化周期": ["15天", "15天", "30天", "15天", "15天"],
            "付费": [100.0, 50.0, 600.0, 900.0, 40.0],
            "直接购物车数": [4, 1, 30, 30, 0],
            "总成交成本": [999.0, 999.0, 999.0, 999.0, 999.0],
        })
        prepared = prepare_source(
            _filter_source_columns(raw, SOURCE_WUJIE_SUBJECT),
            SOURCE_WUJIE_SUBJECT,
        )

        merged, _ = merge_sources({SOURCE_WUJIE_SUBJECT: [("wujie.xlsx", prepared)]})

        product = merged.loc[merged["宝贝ID"] == "1001"].iloc[0]
        self.assertEqual(float(product["付费"]), 150.0)
        self.assertEqual(float(product["直购数量"]), 5.0)
        self.assertEqual(float(product["直购成本"]), 30.0)

        no_cart = merged.loc[merged["宝贝ID"] == "1002"].iloc[0]
        self.assertEqual(float(no_cart["直购成本"]), 0.0)

    def test_period_cost_is_recalculated_from_period_totals(self):
        daily = pd.DataFrame({
            "宝贝ID": ["1001", "1001"],
            "付费": [100.0, 50.0],
            "直购数量": [4.0, 1.0],
            "直购成本": [25.0, 50.0],
        })

        for period_days in (7, 15, 30):
            with self.subTest(period_days=period_days):
                period = aggregate_period_frame(daily, period_days)
                row = period.loc[period["宝贝ID"] == "1001"].iloc[0]
                self.assertEqual(float(row["付费"]), 150.0)
                self.assertEqual(float(row["直购数量"]), 5.0)
                self.assertEqual(float(row["直购成本"]), 30.0)


if __name__ == "__main__":
    unittest.main()
