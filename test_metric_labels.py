"""执行表核心指标状态标签回归测试。"""
import unittest

import pandas as pd

from core.merge import merge_sources
from core.metric_labels import apply_metric_labels
from core.schema import EXPORT_COLS, SOURCE_PRODUCT_ROI


class MetricLabelsTests(unittest.TestCase):
    def _frames(self):
        history_rows = []
        current_rows = []
        for i in range(40):
            pid = str(1000 + i)
            history_rows.append({
                "宝贝ID": pid,
                "淘宝类目": "连衣裙",
                "GMV": 750.0,
                "付费": 75.0,
                "商品访客数": 100.0,
                "商品加购件数": 10.0,
                "支付买家数": 5.0,
                "支付件数": 5.0,
                "MetoX推广访客数": 100.0,
                "MetoX推广加购件数": 10.0,
            })
            current_rows.append({
                "宝贝ID": pid,
                "淘宝类目": "连衣裙",
                "GMV": 1000.0,
                "付费": 100.0,
                "链接投产": 10.0,
                "链接实际投产": 0.0,
                "退款率": 0.0,
                "件单价": 150.0,
                "成本价（含税）": 50.0,
                "商品访客数": 100.0,
                "商品加购件数": 10.0,
                "支付买家数": 5.0,
                "MetoX推广访客数": 100.0,
                "MetoX推广加购件数": 10.0,
                "日销7": 10.0,
                "在仓库存": 400.0,
                "可售天数": 40.0,
            })

        current_rows[0].update({
            "链接投产": 12.0,
            "商品加购件数": 12.0,
            "支付买家数": 6.0,
            "MetoX推广加购件数": 12.0,
            "在仓库存": 100.0,
            "可售天数": 10.0,
        })
        current_rows[1].update({
            "链接投产": 5.0,
            "在仓库存": 200.0,
            "可售天数": 20.0,
        })
        current_rows[2].update({
            "链接投产": 1.0,
            "商品加购件数": 7.0,
            "支付买家数": 3.0,
            "MetoX推广加购件数": 7.0,
        })
        current_rows[3].update({
            "商品访客数": 99.0,
            "MetoX推广访客数": 99.0,
            "在仓库存": 700.0,
            "可售天数": 70.0,
        })
        return pd.DataFrame(current_rows), pd.DataFrame(history_rows)

    def test_applies_agreed_status_boundaries_without_actual_roi_filter(self):
        current, history = self._frames()
        result = apply_metric_labels(
            current,
            day_map={"2026-08-07": (history,)},
            date_str="2026-08-07",
        ).set_index("宝贝ID")

        self.assertEqual(result.loc["1000", "链接投产状态"], "高")
        self.assertEqual(result.loc["1000", "链接实际投产"], 0.0)
        self.assertEqual(result.loc["1001", "链接投产状态"], "正常")
        self.assertEqual(result.loc["1002", "链接投产状态"], "低")

        self.assertEqual(result.loc["1000", "商品加购率状态"], "高")
        self.assertEqual(result.loc["1001", "商品加购率状态"], "正常")
        self.assertEqual(result.loc["1002", "商品加购率状态"], "低")
        self.assertEqual(result.loc["1003", "商品加购率状态"], "样本不足")
        self.assertEqual(result.loc["1003", "推广加购率状态"], "样本不足")

        self.assertEqual(result.loc["1000", "可售天数状态"], "紧急")
        self.assertEqual(result.loc["1001", "可售天数状态"], "需关注")
        self.assertEqual(result.loc["1002", "可售天数状态"], "充足")
        self.assertEqual(result.loc["1003", "可售天数状态"], "积压")

    def test_sellable_days_status_uses_40_day_overstock_boundary(self):
        current = pd.DataFrame({
            "宝贝ID": ["under-14", "at-14", "under-28", "at-28", "at-40", "over-40", "missing"],
            "可售天数": [13.9, 14.0, 27.9, 28.0, 40.0, 40.1, float("nan")],
            "在仓库存": [100.0] * 7,
            "日销7": [10.0] * 7,
        })

        result = apply_metric_labels(current).set_index("宝贝ID")

        self.assertEqual(result.loc["under-14", "可售天数状态"], "紧急")
        self.assertEqual(result.loc["at-14", "可售天数状态"], "需关注")
        self.assertEqual(result.loc["under-28", "可售天数状态"], "需关注")
        self.assertEqual(result.loc["at-28", "可售天数状态"], "充足")
        self.assertEqual(result.loc["at-40", "可售天数状态"], "充足")
        self.assertEqual(result.loc["over-40", "可售天数状态"], "积压")
        self.assertEqual(result.loc["missing", "可售天数状态"], "样本不足")

    def test_link_roi_breakeven_uses_refund_after_gsv_and_sales_cost(self):
        current = pd.DataFrame([{
            "宝贝ID": "target",
            "淘宝类目": "连衣裙",
            "GMV": 1000.0,
            "付费": 250.0,
            "链接投产": 4.0,
            "退款率": 0.60,
            "件单价": 100.0,
            "成本价（含税）": 40.0,
        }])
        history = pd.DataFrame([{
            "宝贝ID": "peer",
            "淘宝类目": "连衣裙",
            "GMV": 300.0,
            "付费": 100.0,
            "支付件数": 3.0,
        }])

        result = apply_metric_labels(
            current,
            day_map={"2026-08-07": (history,)},
            date_str="2026-08-07",
        )

        # 退款后GSV=400，销售成本=400/100*40=160，可投放毛利额=240，
        # 盈亏平衡链接投产=1000/240=4.1667，因此链接投产4.0为低。
        self.assertEqual(result.loc[0, "链接投产状态"], "低")

    def test_link_roi_requires_refund_rate_instead_of_assuming_zero(self):
        current = pd.DataFrame([{
            "宝贝ID": "target",
            "淘宝类目": "连衣裙",
            "GMV": 1000.0,
            "付费": 100.0,
            "链接投产": 10.0,
            "件单价": 100.0,
            "成本价（含税）": 40.0,
        }])

        result = apply_metric_labels(current)

        self.assertEqual(result.loc[0, "链接投产状态"], "样本不足")

    def test_same_group_p75_does_not_fallback_across_categories(self):
        current = pd.DataFrame([{
            "宝贝ID": "target",
            "淘宝类目": "连衣裙",
            "GMV": 300.0,
            "付费": 100.0,
            "链接投产": 3.0,
            "退款率": 0.0,
            "件单价": 150.0,
            "成本价（含税）": 50.0,
            "商品访客数": 100.0,
            "商品加购件数": 10.0,
        }])
        non_matching_history = pd.DataFrame([
            {
                "宝贝ID": "other-category",
                "淘宝类目": "上衣",
                "GMV": 1500.0,
                "付费": 150.0,
                "支付件数": 10.0,
                "商品访客数": 100.0,
                "商品加购件数": 5.0,
            },
            {
                "宝贝ID": "other-price-band",
                "淘宝类目": "连衣裙",
                "GMV": 3000.0,
                "付费": 300.0,
                "支付件数": 10.0,
                "商品访客数": 100.0,
                "商品加购件数": 5.0,
            },
        ])

        result = apply_metric_labels(
            current,
            day_map={"2026-08-07": (non_matching_history,)},
            date_str="2026-08-07",
        )

        # 同价格段但不同类目、同类目但不同价格段都不能贡献P75或加购率基准。
        self.assertEqual(result.loc[0, "链接投产状态"], "高")
        self.assertEqual(result.loc[0, "商品加购率状态"], "样本不足")

    def test_keeps_metox_people_metric_and_adds_count_metric(self):
        product = pd.DataFrame([{
            "宝贝ID": "1",
            "GMV": 1000.0,
            "商品访客数": 200.0,
            "支付买家数": 10.0,
            "支付件数": 10.0,
            "关键词推广访客数": 100.0,
            "关键词推广加购人数": 3.0,
            "关键词推广加购数": 9.0,
        }])
        merged, _ = merge_sources({SOURCE_PRODUCT_ROI: [("test.xlsx", product)]})
        row = merged.iloc[0]
        self.assertEqual(row["MetoX推广加购数"], 3.0)
        self.assertEqual(row["MetoX推广加购件数"], 9.0)
        self.assertEqual(row["推广加购率"], 0.09)

    def test_baseline_includes_historical_products_absent_from_current_day(self):
        current = pd.DataFrame([{
            "宝贝ID": "current",
            "淘宝类目": "连衣裙",
            "件单价": 150.0,
            "商品访客数": 100.0,
            "商品加购件数": 10.0,
        }])
        history = pd.DataFrame([
            {
                "宝贝ID": f"history-{i}",
                "淘宝类目": "连衣裙",
                "件单价": 150.0,
                "商品访客数": 100.0,
                "商品加购件数": 1.0,
            }
            for i in range(10)
        ])

        result = apply_metric_labels(
            current,
            day_map={"2026-08-07": (history,)},
            date_str="2026-08-07",
        )

        self.assertEqual(result.loc[0, "商品加购率状态"], "高")

    def test_exports_five_status_fields(self):
        for column in (
            "链接投产状态",
            "可售天数状态",
            "推广加购率状态",
            "商品加购率状态",
            "商品支付转化率状态",
        ):
            self.assertIn(column, EXPORT_COLS)


if __name__ == "__main__":
    unittest.main()
