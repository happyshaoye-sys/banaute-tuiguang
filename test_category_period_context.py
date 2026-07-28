import unittest

import pandas as pd

from core.analysis import category_analysis
from core.periods import aggregate_category_metrics


class CategoryPeriodContextTests(unittest.TestCase):
    def test_aggregates_multiple_category_metrics_from_loaded_days(self):
        day_map = {
            "2026-07-25": (
                pd.DataFrame([
                    {"淘宝类目": "羽绒服", "GMV": 100.0, "付费": 10.0},
                    {"淘宝类目": "", "GMV": 20.0, "付费": 2.0},
                ]),
                [], 0, 0, set(),
            ),
            "2026-07-26": (
                pd.DataFrame([
                    {"淘宝类目": "羽绒服", "GMV": 50.0, "付费": 5.0},
                ]),
                [], 0, 0, set(),
            ),
        }

        result = aggregate_category_metrics(
            day_map,
            ["2026-07-25", "2026-07-26", "2026-07-27"],
            ["GMV", "付费"],
        )

        self.assertEqual(result["GMV"]["羽绒服"], 150.0)
        self.assertEqual(result["付费"]["羽绒服"], 15.0)
        self.assertEqual(result["GMV"][""], 20.0)

    def test_category_analysis_uses_context_when_selected_period_has_no_7d_columns(self):
        frame = pd.DataFrame([{
            "淘宝类目": "羽绒服",
            "宝贝ID": "current-item",
            "GMV": 5067.19,
            "付费": 0.0,
            "商品访客数": 10,
            "关键词": 0.0,
            "人群": 0.0,
            "全站": 0.0,
        }])

        result = category_analysis(frame, cat_ctx={
            "sel_prev_spend": {"羽绒服": 0.0},
            "h7_cur_gmv": {"羽绒服": 5067.19},
            "h7_prev_gmv": {"羽绒服": 10590.25},
            "h7_prev_spend": {"羽绒服": 11.0},
            "h7_prev_keyword": {"羽绒服": 7.0},
            "h7_prev_crowd": {"羽绒服": 3.0},
            "h7_prev_omni": {"羽绒服": 1.0},
        })

        row = result["invest"][0]
        self.assertEqual(row["7天GMV"], 10590.25)
        self.assertEqual(row["7日GMV环比"], "-52.2%")
        self.assertEqual(row["上周期付费"], 11.0)
        self.assertEqual(row["上周期关键词"], 7.0)
        self.assertEqual(row["上周期人群"], 3.0)
        self.assertEqual(row["上周期全站"], 1.0)


if __name__ == "__main__":
    unittest.main()
