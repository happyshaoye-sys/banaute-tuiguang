"""品类分析环比口径测试（2026-11-14 统一为「本期 vs 紧邻的上一期」）。

口径
----
* 所有 `*环比` = (本期 − 上一期) ÷ 上一期，1 位小数，分母 0 → `0.0%`；
* 上一期由 app 按**所选时间周期**从日帧按品类汇总，通过 `cat_ctx['prev_window']` 传入；
* `上周期*` = 上一期整段合计；上一期上下文缺失时回退行内 `上周期金额/付费/人群/关键词/全站`；
* 投放表：`投放占比` = 该品类付费 ÷ 全店付费，`上周期投放占比` 同口径取上一期，
  `占比变化(pp)` = 两者之差；不再有「付费环比」（与基础表重复）。
"""
import ast
import unittest
from pathlib import Path

import pandas as pd

from core.analysis import (
    CATEGORY_BASIC_COLUMNS,
    CATEGORY_INVESTMENT_COLUMNS,
    PREV_WINDOW_METRICS,
    category_analysis,
)
from core.periods import aggregate_category_metrics


ROOT = Path(__file__).resolve().parent


def _prev_window(**kwargs):
    """构造 prev_window：{指标: {类目: 值}}，未给的指标为空字典。"""
    window = {metric: {} for metric in PREV_WINDOW_METRICS}
    for metric, values in kwargs.items():
        window[metric] = values
    return window


class AggregateCategoryMetricsTests(unittest.TestCase):
    def test_aggregates_multiple_category_metrics_from_loaded_days(self):
        day_map = {
            "2026-07-25": (
                pd.DataFrame([
                    {"淘宝类目": "羽绒服", "GMV": 100.0, "付费": 10.0,
                     "商品加购件数": 8, "支付买家数": 2},
                    {"淘宝类目": "", "GMV": 20.0, "付费": 2.0, "商品访客数": 3},
                ]),
                [], 0, 0, set(),
            ),
            "2026-07-26": (
                pd.DataFrame([
                    {"淘宝类目": "羽绒服", "GMV": 50.0, "付费": 5.0,
                     "商品访客数": 7, "商品加购件数": 4, "支付买家数": 1},
                ]),
                [], 0, 0, set(),
            ),
        }
        result = aggregate_category_metrics(
            day_map,
            ["2026-07-25", "2026-07-26", "2026-07-27"],
            list(PREV_WINDOW_METRICS),
        )
        self.assertEqual(result["GMV"]["羽绒服"], 150.0)
        self.assertEqual(result["付费"]["羽绒服"], 15.0)
        self.assertEqual(result["商品访客数"]["羽绒服"], 7.0)
        self.assertEqual(result["商品加购件数"]["羽绒服"], 12.0)
        self.assertEqual(result["支付买家数"]["羽绒服"], 3.0)
        self.assertEqual(result["GMV"][""], 20.0)
        self.assertEqual(result["商品访客数"][""], 3.0)
        self.assertEqual(result["商品加购件数"][""], 0.0)
        self.assertEqual(result["支付买家数"][""], 0.0)


class CategoryPreviousWindowTests(unittest.TestCase):
    def _frame(self, **overrides):
        row = {
            "淘宝类目": "羽绒服",
            "宝贝ID": "current-item",
            "GMV": 5067.19,
            "付费": 11.0,
            "商品访客数": 10,
            "商品加购件数": 2,
            "支付买家数": 1,
            "关键词": 0.0,
            "人群": 0.0,
            "全站": 0.0,
        }
        row.update(overrides)
        return pd.DataFrame([row])

    def test_invest_uses_previous_window(self):
        result = category_analysis(self._frame(), cat_ctx={
            "prev_window": _prev_window(
                GMV={"羽绒服": 10590.25},
                付费={"羽绒服": 8.0},
                关键词={"羽绒服": 7.0},
                人群={"羽绒服": 3.0},
                全站={"羽绒服": 1.0},
            ),
        })
        row = result["invest"][0]
        self.assertEqual(row["上周期GMV"], 10590.25)
        self.assertEqual(row["GMV环比"], "-52.2%")
        self.assertEqual(row["上周期付费"], 8.0)
        self.assertEqual(row["上周期关键词"], 7.0)
        self.assertEqual(row["上周期人群"], 3.0)
        self.assertEqual(row["上周期全站"], 1.0)
        # 投放占比 / 上周期投放占比 / 占比变化(pp)
        self.assertEqual(row["投放占比"], "100.0%")
        self.assertEqual(row["上周期投放占比"], "100.0%")
        self.assertEqual(row["占比变化(pp)"], "+0.0pp")
        # 投放表不再有付费环比（与基础表重复，已按运营要求移除）
        self.assertNotIn("付费环比", row)

    def test_basic_mom_follows_previous_window(self):
        result = category_analysis(self._frame(), cat_ctx={
            "prev_window": _prev_window(
                付费={"羽绒服": 5.5},
                商品访客数={"羽绒服": 5.0},
                商品加购件数={"羽绒服": 1.0},
                支付买家数={"羽绒服": 0.5},
            ),
        })
        row = result["basic"][0]
        self.assertEqual(row["付费环比"], "100.0%")
        self.assertEqual(row["访客数环比"], "100.0%")
        self.assertEqual(row["加购件数环比"], "100.0%")
        # 加购率 20% vs 20% → 0.0%；转化率 10% vs 10% → 0.0%
        self.assertEqual(row["加购率环比"], "0.0%")
        self.assertEqual(row["支付转化率环比"], "0.0%")

    def test_column_orders(self):
        result = category_analysis(self._frame(), cat_ctx={
            "prev_window": _prev_window(付费={"羽绒服": 5.5}),
        })
        self.assertEqual(list(result["basic"][0].keys()), list(CATEGORY_BASIC_COLUMNS))
        self.assertEqual(list(result["invest"][0].keys()), list(CATEGORY_INVESTMENT_COLUMNS))

    def test_share_and_pp_change_across_two_categories(self):
        frame = pd.DataFrame([
            {"淘宝类目": "羽绒服", "宝贝ID": "a", "GMV": 100.0, "付费": 30.0,
             "商品访客数": 10, "商品加购件数": 2, "支付买家数": 1,
             "关键词": 0.0, "人群": 0.0, "全站": 0.0},
            {"淘宝类目": "T恤", "宝贝ID": "b", "GMV": 100.0, "付费": 10.0,
             "商品访客数": 10, "商品加购件数": 2, "支付买家数": 1,
             "关键词": 0.0, "人群": 0.0, "全站": 0.0},
        ])
        result = category_analysis(frame, cat_ctx={
            "prev_window": _prev_window(付费={"羽绒服": 20.0, "T恤": 20.0}),
        })
        rows = {row["品类"]: row for row in result["invest"]}
        # 本期全店付费 40：羽绒服 75.0%、T恤 25.0%
        self.assertEqual(rows["羽绒服"]["投放占比"], "75.0%")
        self.assertEqual(rows["T恤"]["投放占比"], "25.0%")
        # 上一期全店付费 40：各 50.0%
        self.assertEqual(rows["羽绒服"]["上周期投放占比"], "50.0%")
        self.assertEqual(rows["羽绒服"]["占比变化(pp)"], "+25.0pp")
        self.assertEqual(rows["T恤"]["占比变化(pp)"], "-25.0pp")

    def test_zero_denominator_rules(self):
        frame = self._frame(商品访客数=0, 商品加购件数=5, 支付买家数=2)
        result = category_analysis(frame, cat_ctx={
            "prev_window": _prev_window(
                商品访客数={"羽绒服": 0.0},
                商品加购件数={"羽绒服": 0.0},
                支付买家数={"羽绒服": 0.0},
                付费={"羽绒服": 0.0},
                GMV={"羽绒服": 0.0},
            ),
        })
        row = result["basic"][0]
        self.assertEqual(row["访客数占比"], "0.0%")
        self.assertEqual(row["访客数环比"], "0.0%")
        self.assertEqual(row["加购件数环比"], "0.0%")
        self.assertEqual(row["加购率环比"], "0.0%")
        self.assertEqual(row["支付转化率环比"], "0.0%")
        self.assertEqual(row["付费环比"], "0.0%")
        invest = result["invest"][0]
        self.assertEqual(invest["GMV环比"], "0.0%")
        self.assertEqual(invest["上周期投放占比"], "0.0%")

    def test_missing_context_falls_back_to_row_previous_columns(self):
        """没有 prev_window 时回退行内上周期字段（导出不带上下文的老路径）。"""
        frame = self._frame(
            上周期金额=1000.0, 上周期付费=5.5,
            上周期关键词=2.0, 上周期人群=1.0, 上周期全站=0.5,
        )
        result = category_analysis(frame, cat_ctx={})
        invest = result["invest"][0]
        self.assertEqual(invest["上周期GMV"], 1000.0)
        self.assertEqual(invest["上周期付费"], 5.5)
        self.assertEqual(invest["上周期关键词"], 2.0)
        self.assertEqual(invest["上周期人群"], 1.0)
        self.assertEqual(invest["上周期全站"], 0.5)
        self.assertEqual(invest["GMV环比"], "406.7%")
        basic = result["basic"][0]
        # 访客/加购/买家没有行级上周期字段 → 环比 0.0%
        self.assertEqual(basic["访客数环比"], "0.0%")
        self.assertEqual(basic["付费环比"], "100.0%")


class CategoryContextUiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "app.py").read_text(encoding="utf-8-sig")
        tree = ast.parse(cls.source)
        load_function = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "_load_folder_cached"
        )
        cls.load_source = ast.get_source_segment(cls.source, load_function)

    def test_context_built_from_selected_period_previous_window(self):
        self.assertIn('sel_period = periods.get(sel_label) or periods["日环比"]',
                      self.load_source)
        self.assertIn('iter_dates(sel_period["prev_start"], sel_period["prev_end"])',
                      self.load_source)
        self.assertIn("list(PREV_WINDOW_METRICS)", self.load_source)
        self.assertIn("'prev_window': prev_window", self.load_source)
        self.assertIn("'prev_start': sel_period[\"prev_start\"]", self.load_source)
        # 旧的固定 7 日窗口上下文彻底移除
        self.assertNotIn("h7_cur_gmv", self.load_source)
        self.assertNotIn("h7_prev_spend", self.load_source)
        self.assertNotIn("sel_prev_spend", self.load_source)

    def test_page_explains_period_aligned_windows(self):
        self.assertIn("所有环比 = 本期 vs 紧邻的等长上一期", self.source)
        self.assertIn("上周期投放占比为上一期同口径", self.source)
        self.assertNotIn("7日GMV/访客数/加购/支付转化率环比按 7日窗口", self.source)


if __name__ == "__main__":
    unittest.main()
