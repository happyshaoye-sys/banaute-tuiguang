import unittest

import pandas as pd

from core.new_product_first_seen import build_first_seen_index
from core.new_product_lifecycle import (
    CHECKPOINT_D14,
    CHECKPOINT_D21,
    aggregate_lifecycle_windows,
    lifecycle_window,
)


def _day(date, pid, sku, visitors, carts, buyers, gmv, stock=10, status="当前在线"):
    return {
        "_数据日期": date,
        "宝贝ID": pid,
        "货号": sku,
        "商品状态": status,
        "淘宝类目": "半身裙",
        "件单价": 150,
        "商品访客数": visitors,
        "商品加购件数": carts,
        "支付买家数": buyers,
        "支付件数": buyers,
        "GMV": gmv,
        "在仓库存": stock,
    }


class NewProductLifecycleAggregationTests(unittest.TestCase):
    def test_window_assignment_uses_d14_d21_blocks(self):
        self.assertEqual(lifecycle_window(1), "D1_D14")
        self.assertEqual(lifecycle_window(14), "D1_D14")
        self.assertEqual(lifecycle_window(15), "D15_D21")
        self.assertEqual(lifecycle_window(21), "D15_D21")
        self.assertEqual(lifecycle_window(22), "")

    def test_aggregates_style_periods_and_recomputes_rates_from_totals(self):
        facts = pd.DataFrame([
            _day("2026-08-01", "1001", "SKU-A", 10, 1, 0, 0),
            _day("2026-08-02", "1002", "SKU-A", 20, 3, 1, 200),
            _day("2026-08-16", "1001", "SKU-A", 40, 2, 1, 100),
            _day("2026-08-17", "1002", "SKU-A", 60, 8, 2, 300),
        ])
        first_seen = build_first_seen_index(
            [("2026-08-01", facts[facts["_数据日期"] == "2026-08-01"])],
            history_start_date="2026-07-01",
        )

        result = aggregate_lifecycle_windows(facts, first_seen).set_index("style_key")
        row = result.loc["SKU-A"]

        self.assertEqual(row["关联宝贝ID"], "1001,1002")
        self.assertEqual(row["D1_D14_商品访客数"], 30.0)
        self.assertEqual(row["D1_D14_商品加购件数"], 4.0)
        self.assertAlmostEqual(row["D1_D14_商品加购率"], 4 / 30)
        self.assertEqual(row["D15_D21_支付买家数"], 3.0)
        self.assertEqual(row["D1_D21_GMV"], 600.0)
        self.assertEqual(row["D15_D21_有效数据天数"], 2)
        self.assertNotIn("D15_D21_商品支付转化率", row.index)
        self.assertNotIn("D1_D21_访客平均价值", row.index)

    def test_checkpoint_state_is_frozen_to_decision_window_but_current_state_stays_current(self):
        rows = []
        for day in range(1, 23):
            date = f"2026-08-{day:02d}"
            stock = 10
            status = "当前在线"
            if day == 14:
                stock = 8
            if day == 21:
                stock = 7
            if day == 22:
                stock = 0
                status = "已下架"
            rows.append(_day(date, "1001", "SKU-A", 10, 1, 0, 0, stock=stock, status=status))
        facts = pd.DataFrame(rows)
        first_seen = build_first_seen_index(
            [("2026-08-01", facts[facts["_数据日期"] == "2026-08-01"])],
            history_start_date="2026-07-01",
        )

        row = aggregate_lifecycle_windows(facts, first_seen, as_of_date="2026-08-22").iloc[0]

        self.assertEqual(row[f"{CHECKPOINT_D14}商品状态"], "当前在线")
        self.assertEqual(row[f"{CHECKPOINT_D14}在仓库存"], 8.0)
        self.assertEqual(row[f"{CHECKPOINT_D21}商品状态"], "当前在线")
        self.assertEqual(row[f"{CHECKPOINT_D21}在仓库存"], 7.0)
        self.assertEqual(row["当前商品状态"], "已下架")
        self.assertEqual(row["当前在仓库存"], 0.0)
        self.assertEqual(row["新品年龄"], 22)


if __name__ == "__main__":
    unittest.main()
