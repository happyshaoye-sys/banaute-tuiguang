import unittest

import pandas as pd

from core.analysis import category_analysis


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


class CategoryCompletePreviousGmvTests(unittest.TestCase):
    def test_uses_complete_previous_category_gmv_for_display_and_comparison(self):
        result = category_analysis(
            _category_frame(mapped_previous_gmv=8207.15),
            cat_ctx={
                "h7_cur_gmv": {"羽绒服": 5067.19},
                "h7_prev_gmv": {"羽绒服": 10590.25},
            },
        )

        row = result["invest"][0]
        self.assertEqual(row["7天GMV"], 10590.25)
        self.assertEqual(row["7日GMV环比"], "-52.2%")

    def test_without_period_context_keeps_legacy_mapped_value(self):
        result = category_analysis(_category_frame(mapped_previous_gmv=8207.15))

        self.assertEqual(result["invest"][0]["7天GMV"], 8207.15)


if __name__ == "__main__":
    unittest.main()
