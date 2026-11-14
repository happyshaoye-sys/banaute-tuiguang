"""「上周期GMV」取完整上一期整段（含已下架商品），不只看本期仍存在的商品。

2026-11-14 口径：`上周期GMV` = 上一期整段按品类汇总的 GMV，`GMV环比` = 本期 vs 上一期；
上一期上下文缺失时才回退行内 `上周期金额`（只含本期仍存在商品，会略偏低）。
"""
import unittest

import pandas as pd

from core.analysis import PREV_WINDOW_METRICS, category_analysis


def _category_frame(mapped_previous_gmv):
    return pd.DataFrame([{
        "淘宝类目": "羽绒服",
        "宝贝ID": "current-item",
        "GMV": 5067.19,
        "付费": 0.0,
        "商品访客数": 10,
        "关键词": 0.0,
        "人群": 0.0,
        "全站": 0.0,
        "上周期金额": mapped_previous_gmv,
        "上周期付费": 0.0,
        "上周期关键词": 0.0,
        "上周期人群": 0.0,
        "上周期全站": 0.0,
    }])


def _prev_window(**kwargs):
    window = {metric: {} for metric in PREV_WINDOW_METRICS}
    for metric, values in kwargs.items():
        window[metric] = values
    return window


class CategoryCompletePreviousGmvTests(unittest.TestCase):
    def test_previous_window_gmv_wins_over_row_level_value(self):
        result = category_analysis(
            _category_frame(mapped_previous_gmv=8207.15),
            cat_ctx={"prev_window": _prev_window(GMV={"羽绒服": 10590.25})},
        )
        row = result["invest"][0]
        self.assertEqual(row["上周期GMV"], 10590.25)
        self.assertEqual(row["GMV环比"], "-52.2%")

    def test_without_period_context_falls_back_to_row_level_value(self):
        result = category_analysis(_category_frame(mapped_previous_gmv=8207.15))
        row = result["invest"][0]
        self.assertEqual(row["上周期GMV"], 8207.15)
        self.assertEqual(row["GMV环比"], "-38.3%")
        # 上一期付费为 0 → 占比与变化按 0 处理，不报错
        self.assertEqual(row["上周期投放占比"], "0.0%")
        self.assertEqual(row["占比变化(pp)"], "+0.0pp")


if __name__ == "__main__":
    unittest.main()
