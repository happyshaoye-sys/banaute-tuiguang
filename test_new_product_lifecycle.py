from datetime import date, timedelta
import unittest

import pandas as pd

from core.new_product_first_seen import (
    FIRST_SEEN_DATE,
    FIRST_SEEN_PRODUCT_ID,
    IS_HISTORY_TRUNCATED,
    STYLE_KEY,
)
from core.new_product_lifecycle import (
    CHECKPOINT_D30,
    aggregate_lifecycle_windows,
    lifecycle_window,
)


FIRST_DAY = date(2026, 8, 1)


def _day(age, pid, sku, visitors, carts, buyers=0, gmv=0, stock=10, status="当前在线"):
    return {
        "_数据日期": (FIRST_DAY + timedelta(days=age - 1)).isoformat(),
        "宝贝ID": pid,
        "货号": sku,
        "商品状态": status,
        "店铺": "女装",
        "淘宝类目": "半身裙",
        "件单价": 150,
        "商品访客数": visitors,
        "商品加购件数": carts,
        "支付买家数": buyers,
        "支付件数": buyers,
        "GMV": gmv,
        "在仓库存": stock,
    }


def _first_seen(*styles):
    return pd.DataFrame([
        {
            STYLE_KEY: sku,
            FIRST_SEEN_DATE: FIRST_DAY.isoformat(),
            FIRST_SEEN_PRODUCT_ID: pid,
            IS_HISTORY_TRUNCATED: False,
        }
        for sku, pid in styles
    ])


class NewProductLifecycleAggregationTests(unittest.TestCase):
    def test_window_assignment_uses_d15_d30_blocks(self):
        self.assertEqual(lifecycle_window(1), "D1_D15")
        self.assertEqual(lifecycle_window(15), "D1_D15")
        self.assertEqual(lifecycle_window(16), "D16_D30")
        self.assertEqual(lifecycle_window(30), "D16_D30")
        self.assertEqual(lifecycle_window(31), "")

    def test_aggregates_current_fields_without_old_d14_d21_columns(self):
        facts = pd.DataFrame([
            _day(1, "1001", "SKU-A", 10, 1),
            _day(2, "1002", "SKU-A", 20, 3, buyers=1, gmv=200),
            _day(16, "1001", "SKU-A", 40, 2, buyers=1, gmv=100),
            _day(17, "1002", "SKU-A", 60, 8, buyers=2, gmv=300),
            _day(31, "1001", "SKU-A", 500, 50, buyers=5, gmv=1000),
        ])

        result = aggregate_lifecycle_windows(
            facts,
            _first_seen(("SKU-A", "1001")),
            as_of_date="2026-08-31",
        ).set_index(STYLE_KEY)
        row = result.loc["SKU-A"]

        self.assertEqual(row["关联宝贝ID"], "1001,1002")
        self.assertEqual(row["D1_D15_商品访客数"], 30.0)
        self.assertEqual(row["D1_D15_商品加购件数"], 4.0)
        self.assertAlmostEqual(row["D1_D15_商品加购率"], 4 / 30)
        self.assertEqual(row["D16_D30_支付买家数"], 3.0)
        self.assertEqual(row["D1_D30_GMV"], 600.0)
        self.assertEqual(row["D16_D30_有效数据天数"], 2)
        forbidden = [
            "left_censored",
            "D14商品状态",
            "D14在仓库存",
            "D15商品状态",
            "D15在仓库存",
            "D21商品状态",
            "D21在仓库存",
            "D1_D14_商品访客数",
            "D15_D21_商品访客数",
            "D1_D21_商品访客数",
        ]
        for column in forbidden:
            self.assertNotIn(column, row.index)
        self.assertFalse(any(str(column).startswith("__D1_AS_OF_") for column in result.columns))

    def test_only_d30_state_is_frozen_and_current_state_stays_current(self):
        rows = []
        for age in range(1, 32):
            stock = 10
            status = "当前在线"
            if age == 15:
                stock = 8
            if age == 21:
                stock = 7
            if age == 30:
                stock = 6
            if age == 31:
                stock = 0
                status = "已下架"
            rows.append(_day(age, "1001", "SKU-A", 10, 1, stock=stock, status=status))

        row = aggregate_lifecycle_windows(
            pd.DataFrame(rows),
            _first_seen(("SKU-A", "1001")),
            as_of_date="2026-08-31",
        ).iloc[0]

        self.assertEqual(row[f"{CHECKPOINT_D30}商品状态"], "当前在线")
        self.assertEqual(row[f"{CHECKPOINT_D30}在仓库存"], 6.0)
        self.assertEqual(row["当前商品状态"], "已下架")
        self.assertEqual(row["当前在仓库存"], 0.0)
        self.assertEqual(row["新品年龄"], 31)
        for checkpoint in ("D14", "D15", "D21"):
            self.assertNotIn(f"{checkpoint}商品状态", row.index)
            self.assertNotIn(f"{checkpoint}在仓库存", row.index)

    def test_as_of_date_blocks_future_window_metrics(self):
        facts = pd.DataFrame([
            *[_day(age, "1001", "SKU-A", 10, 1) for age in range(1, 16)],
            _day(20, "1001", "SKU-A", 1000, 100, buyers=2, gmv=500),
        ])

        row = aggregate_lifecycle_windows(
            facts,
            _first_seen(("SKU-A", "1001")),
            as_of_date="2026-08-15",
        ).iloc[0]

        self.assertEqual(row["新品年龄"], 15)
        self.assertEqual(row["D1_D15_商品访客数"], 150.0)
        self.assertEqual(row["D16_D30_商品访客数"], 0.0)
        self.assertEqual(row["D1_D30_支付买家数"], 0.0)
        self.assertEqual(row["D1_D30_GMV"], 0.0)


if __name__ == "__main__":
    unittest.main()
