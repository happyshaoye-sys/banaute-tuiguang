"""执行表加购率口径回归测试。"""
import unittest

import pandas as pd

from core.loader import prepare_source
from core.merge import merge_sources
from core.periods import aggregate_period_frame
from core.schema import EXPORT_COLS, SOURCE_PRODUCT_ROI, SOURCE_WUJIE_SUBJECT


def _product_row(pid, visitors, carts):
    return {
        "宝贝ID": pid,
        "商品访客数": visitors,
        "商品加购件数": carts,
        "GMV": 0,
    }


def _raw_plan_row(pid, plan_id, period, subject_type, clicks, carts):
    return {
        "主体ID": pid,
        "计划ID": plan_id,
        "转化周期": period,
        "主体类型": subject_type,
        "场景名字": "关键词推广",
        "点击量": clicks,
        "总购物车数": carts,
        "付费": 0,
        "总成交金额": 0,
        "投入产出比": 0,
        "展现": 0,
        "直接购物车数": 0,
    }


class CartRateTests(unittest.TestCase):
    def test_daily_rates_use_product_source_and_15_day_product_subject_totals(self):
        product = pd.DataFrame([
            _product_row("1001", visitors=100, carts=20),
            _product_row("1002", visitors=0, carts=3),
        ])
        wujie = prepare_source(pd.DataFrame([
            _raw_plan_row("1001", "P1", "15天转化", "商品", clicks=100, carts=10),
            _raw_plan_row("1001", "P2", "15天转化", "商品", clicks=50, carts=5),
            _raw_plan_row("1001", "P3", "1天转化", "商品", clicks=1, carts=99),
            _raw_plan_row("1001", "SHOP", "15天转化", "店铺", clicks=1, carts=99),
            _raw_plan_row("1002", "P4", "15天转化", "商品", clicks=0, carts=4),
        ]), SOURCE_WUJIE_SUBJECT)

        self.assertIn("宝贝ID", wujie.columns)
        self.assertIn("点击", wujie.columns)
        self.assertIn("商品加购件数", wujie.columns)

        merged, _ = merge_sources({
            SOURCE_PRODUCT_ROI: [("product.xlsx", product)],
            SOURCE_WUJIE_SUBJECT: [("wujie.xlsx", wujie)],
        })

        row = merged.loc[merged["宝贝ID"] == "1001"].iloc[0]
        self.assertAlmostEqual(float(row["商品加购率"]), 20 / 100)
        self.assertAlmostEqual(float(row["推广加购率"]), (10 + 5) / (100 + 50))

        zero = merged.loc[merged["宝贝ID"] == "1002"].iloc[0]
        self.assertEqual(float(zero["商品加购率"]), 0.0)
        self.assertEqual(float(zero["推广加购率"]), 0.0)

    def test_period_rates_are_recalculated_from_period_totals(self):
        daily = pd.DataFrame([
            {
                "宝贝ID": "1001", "商品访客数": 100, "商品加购件数": 20,
                "点击": 150, "推广加购数": 15, "商品加购率": 0.2,
                "推广加购率": 0.1,
            },
            {
                "宝贝ID": "1001", "商品访客数": 20, "商品加购件数": 5,
                "点击": 50, "推广加购数": 10, "商品加购率": 0.25,
                "推广加购率": 0.2,
            },
        ])

        period = aggregate_period_frame(daily, period_days=2, actual_days=2)
        row = period.iloc[0]
        self.assertAlmostEqual(float(row["商品加购率"]), 25 / 120)
        self.assertAlmostEqual(float(row["推广加购率"]), 25 / 200)

    def test_execution_table_exports_both_rates_next_to_their_metrics(self):
        self.assertEqual(EXPORT_COLS.index("推广加购率"), EXPORT_COLS.index("推广投产") + 1)
        self.assertEqual(EXPORT_COLS.index("商品加购率"), EXPORT_COLS.index("商品加购件数") + 1)


if __name__ == "__main__":
    unittest.main()
