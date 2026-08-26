import ast
import unittest
from pathlib import Path

import pandas as pd

from core.analysis import category_analysis
from core.periods import aggregate_category_metrics


ROOT = Path(__file__).resolve().parent


class CategoryPeriodContextTests(unittest.TestCase):
    def test_aggregates_multiple_category_metrics_from_loaded_days(self):
        day_map = {
            "2026-07-25": (
                pd.DataFrame([
                    {
                        "淘宝类目": "羽绒服", "GMV": 100.0, "付费": 10.0,
                        "商品加购件数": 8, "支付买家数": 2,
                    },
                    {"淘宝类目": "", "GMV": 20.0, "付费": 2.0, "商品访客数": 3},
                ]),
                [], 0, 0, set(),
            ),
            "2026-07-26": (
                pd.DataFrame([
                    {
                        "淘宝类目": "羽绒服", "GMV": 50.0, "付费": 5.0,
                        "商品访客数": 7, "商品加购件数": 4, "支付买家数": 1,
                    },
                ]),
                [], 0, 0, set(),
            ),
        }

        result = aggregate_category_metrics(
            day_map,
            ["2026-07-25", "2026-07-26", "2026-07-27"],
            ["GMV", "付费", "商品访客数", "商品加购件数", "支付买家数"],
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
        self.assertEqual(row["7日付费环比"], "-100.0%")
        self.assertEqual(row["上周期关键词"], 7.0)
        self.assertEqual(row["上周期人群"], 3.0)
        self.assertEqual(row["上周期全站"], 1.0)

    def test_category_basic_adds_visitor_share_and_fixed_7d_visitor_mom(self):
        frame = pd.DataFrame([
            {
                "淘宝类目": "连衣裙",
                "宝贝ID": "current-a",
                "GMV": 300.0,
                "付费": 30.0,
                "商品访客数": 30,
                "商品加购件数": 12,
                "支付买家数": 3,
                "关键词": 0.0,
                "人群": 0.0,
                "全站": 0.0,
            },
            {
                "淘宝类目": "衬衫",
                "宝贝ID": "current-b",
                "GMV": 100.0,
                "付费": 10.0,
                "商品访客数": 70,
                "商品加购件数": 14,
                "支付买家数": 7,
                "关键词": 0.0,
                "人群": 0.0,
                "全站": 0.0,
            },
        ])

        result = category_analysis(frame, cat_ctx={
            "sel_prev_spend": {"连衣裙": 15.0, "衬衫": 5.0},
            "h7_cur_gmv": {},
            "h7_prev_gmv": {},
            "h7_cur_visitors": {"连衣裙": 70.0, "衬衫": 14.0},
            "h7_prev_visitors": {"连衣裙": 35.0, "衬衫": 7.0},
            "h7_cur_carts": {"连衣裙": 28.0, "衬衫": 7.0},
            "h7_prev_carts": {"连衣裙": 7.0, "衬衫": 7.0},
            "h7_cur_buyers": {"连衣裙": 14.0, "衬衫": 2.0},
            "h7_prev_buyers": {"连衣裙": 3.5, "衬衫": 1.0},
        })

        row = result["basic"][0]
        self.assertEqual(row["品类"], "连衣裙")
        self.assertEqual(
            list(row.keys()),
            [
                "品类", "商品数", "GMV", "GMV占比", "付费占比", "付费",
                "付费环比", "商品访客数", "访客数占比", "7日访客数环比",
                "品类加购件数", "7日加购件数环比", "品类加购率", "7日加购率环比",
                "品类支付转化率", "7日支付转化率环比",
                "退款率", "SPU价值", "ROI",
            ],
        )
        self.assertEqual(row["访客数占比"], "30.0%")
        self.assertEqual(row["7日访客数环比"], "100.0%")
        self.assertEqual(row["品类加购件数"], 12)
        self.assertEqual(row["7日加购件数环比"], "300.0%")
        self.assertEqual(row["品类加购率"], "40.0%")
        self.assertEqual(row["7日加购率环比"], "100.0%")
        self.assertEqual(row["品类支付转化率"], "10.0%")
        self.assertEqual(row["7日支付转化率环比"], "100.0%")

    def test_category_basic_visitor_metrics_use_zero_denominator_rule(self):
        frame = pd.DataFrame([{
            "淘宝类目": "连衣裙",
            "宝贝ID": "current-a",
            "GMV": 300.0,
            "付费": 30.0,
            "商品访客数": 0,
            "商品加购件数": 5,
            "支付买家数": 2,
            "关键词": 0.0,
            "人群": 0.0,
            "全站": 0.0,
        }])

        result = category_analysis(frame, cat_ctx={
            "h7_cur_visitors": {"连衣裙": 20.0},
            "h7_prev_visitors": {"连衣裙": 0.0},
            "h7_cur_carts": {"连衣裙": 5.0},
            "h7_prev_carts": {"连衣裙": 0.0},
            "h7_cur_buyers": {"连衣裙": 2.0},
            "h7_prev_buyers": {"连衣裙": 0.0},
        })

        row = result["basic"][0]
        self.assertEqual(row["访客数占比"], "0.0%")
        self.assertEqual(row["7日访客数环比"], "0.0%")
        self.assertEqual(row["品类加购率"], "0.0%")
        self.assertEqual(row["7日加购件数环比"], "0.0%")
        self.assertEqual(row["7日加购率环比"], "0.0%")
        self.assertEqual(row["品类支付转化率"], "0.0%")
        self.assertEqual(row["7日支付转化率环比"], "0.0%")


class CategoryVisitorContextUiContractTests(unittest.TestCase):
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

    def test_fixed_7d_context_includes_current_and_previous_visitors(self):
        self.assertIn(
            '["GMV", "商品访客数", "商品加购件数", "支付买家数"]',
            self.load_source,
        )
        self.assertIn(
            '["GMV", "付费", "关键词", "人群", "全站", "商品访客数", '
            '"商品加购件数", "支付买家数"]',
            self.load_source,
        )
        self.assertIn(
            "'h7_cur_visitors': h7_current['商品访客数']",
            self.load_source,
        )
        self.assertIn(
            "'h7_prev_visitors': h7_previous['商品访客数']",
            self.load_source,
        )
        self.assertIn(
            "'h7_cur_carts': h7_current['商品加购件数']",
            self.load_source,
        )
        self.assertIn(
            "'h7_prev_carts': h7_previous['商品加购件数']",
            self.load_source,
        )
        self.assertIn(
            "'h7_cur_buyers': h7_current['支付买家数']",
            self.load_source,
        )
        self.assertIn(
            "'h7_prev_buyers': h7_previous['支付买家数']",
            self.load_source,
        )

    def test_page_explains_fixed_7d_visitor_window(self):
        self.assertIn("7日GMV/访客数/加购/支付转化率环比按 7日窗口", self.source)


if __name__ == "__main__":
    unittest.main()
