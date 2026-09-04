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
    CHECKPOINT_D15,
    CHECKPOINT_D30,
    LifecycleDecisionConfig,
    REASON_CURRENT_SALE_PROTECTED,
    REASON_CURRENT_TRAFFIC_INSUFFICIENT,
    REASON_D15_SALE_PROTECTED,
    REASON_D15_WEAK,
    REASON_D30_SALE_PROTECTED,
    REASON_D30_WEAK,
    REASON_D45_TRAFFIC_EXPIRED,
    REASON_HISTORY_TRUNCATED,
    REASON_PEER_INSUFFICIENT,
    REASON_TRAFFIC_INSUFFICIENT,
    RESULT_DOWN,
    RESULT_OBSERVE,
    aggregate_lifecycle_windows,
    apply_current_lifecycle_decisions,
    apply_lifecycle_decisions,
    compute_peer_baselines,
)


SUM_FIELDS = ("商品访客数", "商品加购件数", "支付买家数", "支付件数", "GMV")
FIRST_DAY = date(2026, 8, 1)


def _set_period(row, prefix, visitors, carts, buyers=0, units=None, gmv=0, days=15):
    if units is None:
        units = buyers
    row.update({
        f"{prefix}_商品访客数": visitors,
        f"{prefix}_商品加购件数": carts,
        f"{prefix}_支付买家数": buyers,
        f"{prefix}_支付件数": units,
        f"{prefix}_GMV": gmv,
        f"{prefix}_有效数据天数": days,
        f"{prefix}_商品加购率": carts / visitors if visitors > 0 else 0,
    })
    return row


def _sync_d30_cumulative(row):
    for field in SUM_FIELDS:
        row[f"D1_D30_{field}"] = (
            float(row.get(f"D1_D15_{field}", 0.0))
            + float(row.get(f"D16_D30_{field}", 0.0))
        )
    row["D1_D30_有效数据天数"] = (
        int(row.get("D1_D15_有效数据天数", 0))
        + int(row.get("D16_D30_有效数据天数", 0))
    )
    visitors = row["D1_D30_商品访客数"]
    row["D1_D30_商品加购率"] = row["D1_D30_商品加购件数"] / visitors if visitors > 0 else 0
    return row


def _lifecycle_row(style_key="TARGET", age=15, truncated=False):
    row = {
        STYLE_KEY: style_key,
        FIRST_SEEN_DATE: FIRST_DAY.isoformat(),
        "新品年龄": age,
        IS_HISTORY_TRUNCATED: truncated,
        "店铺": "女装",
        "淘宝类目": "半身裙",
        "价格段": "100-200",
        "D30商品状态": "当前在线",
        "D30在仓库存": 10,
        "当前商品状态": "当前在线",
        "当前在仓库存": 10,
    }
    _set_period(row, "D1_D15", 300, 3, buyers=0, units=0, gmv=0, days=15)
    _set_period(row, "D16_D30", 300, 3, buyers=0, units=0, gmv=0, days=15)
    return _sync_d30_cumulative(row)


def _valid_peer(style_key):
    row = _lifecycle_row(style_key=style_key, age=45)
    _set_period(row, "D1_D15", 200, 20, buyers=10, units=10, gmv=2000, days=15)
    _set_period(row, "D16_D30", 200, 20, buyers=10, units=10, gmv=2000, days=15)
    return _sync_d30_cumulative(row)


def _peer_rows(count=20):
    return pd.DataFrame([_valid_peer(f"PEER-{i:02d}") for i in range(count)])


def _daily_fact(style, age, visitors, carts, buyers=0, units=None, gmv=0.0):
    if units is None:
        units = buyers
    return {
        "_数据日期": (FIRST_DAY + timedelta(days=age - 1)).isoformat(),
        "宝贝ID": f"PID-{style}",
        "货号": style,
        "店铺": "女装",
        "淘宝类目": "半身裙",
        "价格段": "100-200",
        "商品状态": "当前在线",
        "在仓库存": 10,
        "商品访客数": visitors,
        "商品加购件数": carts,
        "支付买家数": buyers,
        "支付件数": units,
        "GMV": gmv,
    }


def _first_seen_rows(styles, truncated=()):
    truncated = set(truncated)
    return pd.DataFrame([
        {
            STYLE_KEY: style,
            FIRST_SEEN_DATE: FIRST_DAY.isoformat(),
            FIRST_SEEN_PRODUCT_ID: f"PID-{style}",
            IS_HISTORY_TRUNCATED: style in truncated,
        }
        for style in styles
    ])


def _aggregate_current_case(target_rows, *, as_of_age, peer_count=20, truncated=()):
    peer_styles = [f"PEER-{i:02d}" for i in range(peer_count)]
    peer_rows = []
    for style in peer_styles:
        for age in range(1, 31):
            peer_rows.append(_daily_fact(style, age, visitors=10, carts=1, buyers=1, units=1, gmv=100))
    facts = pd.DataFrame([*target_rows, *peer_rows])
    first_seen = _first_seen_rows(["TARGET", *peer_styles], truncated=truncated)
    return aggregate_lifecycle_windows(
        facts,
        first_seen,
        as_of_date=(FIRST_DAY + timedelta(days=as_of_age - 1)).isoformat(),
    )


class NewProductDecisionTests(unittest.TestCase):
    def setUp(self):
        self.config = LifecycleDecisionConfig(
            decision_visitor_min=300,
            period_min_valid_days=12,
            min_peer_count=20,
            low_ratio=0.8,
        )
        self.peers = _peer_rows()

    def test_fixed_d15_equal_300_traffic_can_down(self):
        result = apply_lifecycle_decisions(
            pd.DataFrame([_lifecycle_row(age=15)]),
            CHECKPOINT_D15,
            peer_frame=self.peers,
            config=self.config,
        )

        self.assertEqual(result.loc[0, "判定结果"], RESULT_DOWN)
        self.assertEqual(result.loc[0, "判定原因"], REASON_D15_WEAK)
        self.assertEqual(result.loc[0, "流量验证"], "已验证")
        self.assertEqual(result.loc[0, "保护状态"], "无保护")

    def test_fixed_d15_sale_protects_before_history_data_and_traffic_guards(self):
        row = _lifecycle_row(age=15, truncated=True)
        _set_period(row, "D1_D15", 120, 1, buyers=2, units=0, gmv=0, days=5)
        _sync_d30_cumulative(row)

        result = apply_lifecycle_decisions(
            pd.DataFrame([row]),
            CHECKPOINT_D15,
            peer_frame=self.peers,
            config=self.config,
        )

        self.assertEqual(result.loc[0, "判定结果"], RESULT_OBSERVE)
        self.assertEqual(result.loc[0, "判定原因"], REASON_D15_SALE_PROTECTED)
        self.assertEqual(result.loc[0, "保护状态"], "动销保护")
        self.assertIn("支付买家2人", result.loc[0, "保护证据"])
        self.assertEqual(result.loc[0, "流量验证"], "待流量验证")

    def test_fixed_d30_uses_d1_d30_sale_protection_and_d16_d30_performance(self):
        row = _lifecycle_row(age=30)
        _set_period(row, "D1_D15", 300, 3, buyers=1, units=0, gmv=0, days=15)
        _set_period(row, "D16_D30", 300, 0, buyers=0, units=0, gmv=0, days=15)
        _sync_d30_cumulative(row)

        result = apply_lifecycle_decisions(
            pd.DataFrame([row]),
            CHECKPOINT_D30,
            peer_frame=self.peers,
            config=self.config,
        )

        self.assertEqual(result.loc[0, "判定结果"], RESULT_OBSERVE)
        self.assertEqual(result.loc[0, "判定原因"], REASON_D30_SALE_PROTECTED)
        self.assertIn("支付买家1人", result.loc[0, "保护证据"])
        self.assertEqual(result.loc[0, "本轮成交"], "支付买家0人，支付件数0件，GMV0.00")

    def test_current_d20_uses_rolling_d1_to_analysis_day_traffic(self):
        rows = [
            *[_daily_fact("TARGET", age, visitors=10, carts=0) for age in range(1, 16)],
            *[_daily_fact("TARGET", age, visitors=30, carts=0) for age in range(16, 21)],
        ]
        lifecycle = _aggregate_current_case(rows, as_of_age=20)

        fixed = apply_lifecycle_decisions(
            lifecycle[lifecycle[STYLE_KEY].eq("TARGET")],
            CHECKPOINT_D15,
            peer_frame=lifecycle,
            config=self.config,
        )
        current = apply_current_lifecycle_decisions(
            lifecycle[lifecycle[STYLE_KEY].eq("TARGET")],
            peer_frame=lifecycle,
            config=self.config,
        )

        self.assertEqual(fixed.loc[0, "判定原因"], REASON_TRAFFIC_INSUFFICIENT)
        self.assertEqual(fixed.loc[0, "流量验证"], "待流量验证")
        self.assertEqual(current.loc[0, "判定轮次"], CHECKPOINT_D15)
        self.assertEqual(current.loc[0, "判定结果"], RESULT_DOWN)
        self.assertEqual(current.loc[0, "判定原因"], REASON_D15_WEAK)
        self.assertEqual(current.loc[0, "流量验证"], "已验证")
        self.assertFalse(any(str(column).startswith("__D1_AS_OF_") for column in current.columns))

    def test_current_d44_low_traffic_still_observes(self):
        row = _lifecycle_row(age=44)
        _set_period(row, "D1_D15", 100, 0, buyers=0, units=0, gmv=0, days=15)
        _set_period(row, "D16_D30", 100, 0, buyers=0, units=0, gmv=0, days=15)
        _sync_d30_cumulative(row)

        result = apply_current_lifecycle_decisions(
            pd.DataFrame([row]),
            peer_frame=self.peers,
            config=self.config,
        )

        self.assertEqual(result.loc[0, "判定轮次"], CHECKPOINT_D30)
        self.assertEqual(result.loc[0, "判定结果"], RESULT_OBSERVE)
        self.assertEqual(result.loc[0, "判定原因"], REASON_CURRENT_TRAFFIC_INSUFFICIENT)
        self.assertEqual(result.loc[0, "流量验证"], "待流量验证")

    def test_current_d45_low_traffic_forces_down_after_history_guard(self):
        low_traffic = _lifecycle_row(age=45)
        _set_period(low_traffic, "D1_D15", 100, 0, buyers=0, units=0, gmv=0, days=15)
        _set_period(low_traffic, "D16_D30", 100, 0, buyers=0, units=0, gmv=0, days=15)
        _sync_d30_cumulative(low_traffic)
        truncated = dict(low_traffic)
        truncated[STYLE_KEY] = "TRUNCATED"
        truncated[IS_HISTORY_TRUNCATED] = True

        result = apply_current_lifecycle_decisions(
            pd.DataFrame([low_traffic, truncated]),
            peer_frame=self.peers,
            config=self.config,
        ).set_index(STYLE_KEY)

        self.assertEqual(result.loc["TARGET", "判定结果"], RESULT_DOWN)
        self.assertEqual(result.loc["TARGET", "判定原因"], REASON_D45_TRAFFIC_EXPIRED)
        self.assertEqual(result.loc["TARGET", "流量验证"], "待流量验证")
        self.assertEqual(result.loc["TRUNCATED", "判定结果"], RESULT_OBSERVE)
        self.assertEqual(result.loc["TRUNCATED", "判定原因"], REASON_HISTORY_TRUNCATED)

    def test_current_d31_to_d45_sale_protection_overrides_hard_deadline(self):
        rows = [
            *[_daily_fact("TARGET", age, visitors=7, carts=0) for age in range(1, 16)],
            *[_daily_fact("TARGET", age, visitors=6, carts=0) for age in range(16, 31)],
            _daily_fact("TARGET", 31, visitors=0, carts=0, buyers=0, units=1, gmv=0),
        ]
        lifecycle = _aggregate_current_case(rows, as_of_age=45)

        result = apply_current_lifecycle_decisions(
            lifecycle[lifecycle[STYLE_KEY].eq("TARGET")],
            peer_frame=lifecycle,
            config=self.config,
        )

        self.assertEqual(result.loc[0, "判定结果"], RESULT_OBSERVE)
        self.assertEqual(result.loc[0, "判定原因"], REASON_CURRENT_SALE_PROTECTED)
        self.assertEqual(result.loc[0, "保护状态"], "动销保护")
        self.assertIn("支付件数1件", result.loc[0, "保护证据"])
        self.assertIn("支付件数1件", result.loc[0, "本轮成交"])

    def test_current_as_of_date_does_not_leak_future_sale(self):
        rows = [
            *[_daily_fact("TARGET", age, visitors=10, carts=0) for age in range(1, 31)],
            _daily_fact("TARGET", 31, visitors=0, carts=0, buyers=1, units=1, gmv=100),
        ]
        lifecycle = _aggregate_current_case(rows, as_of_age=30)

        result = apply_current_lifecycle_decisions(
            lifecycle[lifecycle[STYLE_KEY].eq("TARGET")],
            peer_frame=lifecycle,
            config=self.config,
        )

        self.assertEqual(result.loc[0, "D1_D30_支付买家数"], 0.0)
        self.assertEqual(result.loc[0, "判定轮次"], CHECKPOINT_D30)
        self.assertEqual(result.loc[0, "判定结果"], RESULT_DOWN)
        self.assertEqual(result.loc[0, "判定原因"], REASON_D30_WEAK)
        self.assertEqual(result.loc[0, "保护状态"], "无保护")
        self.assertEqual(result.loc[0, "本轮成交"], "支付买家0人，支付件数0件，GMV0.00")

    def test_current_traffic_equal_300_is_verified_not_d45_fallback(self):
        row = _lifecycle_row(age=45)
        _set_period(row, "D1_D15", 150, 0, buyers=0, units=0, gmv=0, days=15)
        _set_period(row, "D16_D30", 150, 0, buyers=0, units=0, gmv=0, days=15)
        _sync_d30_cumulative(row)

        result = apply_current_lifecycle_decisions(
            pd.DataFrame([row]),
            peer_frame=self.peers,
            config=self.config,
        )

        self.assertEqual(result.loc[0, "流量验证"], "已验证")
        self.assertEqual(result.loc[0, "判定结果"], RESULT_DOWN)
        self.assertEqual(result.loc[0, "判定原因"], REASON_D30_WEAK)

    def test_peer_baselines_use_d15_and_d30_only(self):
        config = LifecycleDecisionConfig(min_peer_count=2)
        peers = pd.DataFrame([
            _valid_peer("VALID-1"),
            _valid_peer("VALID-2"),
            {**_valid_peer("YOUNG-D15"), "新品年龄": 14},
            {**_valid_peer("YOUNG-D30"), "新品年龄": 29},
            {**_valid_peer("CENSORED"), IS_HISTORY_TRUNCATED: True},
            {**_valid_peer("SHORT-D15"), "D1_D15_有效数据天数": 11},
            {**_valid_peer("SHORT-D30"), "D16_D30_有效数据天数": 11},
            {**_valid_peer("ZERO-D15"), "D1_D15_商品访客数": 0},
            {**_valid_peer("ZERO-D30"), "D16_D30_商品访客数": 0},
        ])

        d15 = compute_peer_baselines(peers, CHECKPOINT_D15, config=config).iloc[0]
        d30 = compute_peer_baselines(peers, CHECKPOINT_D30, config=config).iloc[0]

        self.assertEqual(d15["同层样本数"], 5)
        self.assertAlmostEqual(d15["D1_D15_商品加购率_B"], 0.10)
        self.assertEqual(d30["同层样本数"], 4)
        self.assertAlmostEqual(d30["D16_D30_商品加购率_B"], 0.10)
        self.assertNotIn("D1_D14_商品加购率_B", d15.index)
        self.assertNotIn("D1_D21_商品加购率_B", d30.index)

    def test_old_d14_d21_checkpoints_are_no_longer_supported(self):
        with self.assertRaises(ValueError):
            apply_lifecycle_decisions(pd.DataFrame([_lifecycle_row(age=21)]), "D14")
        with self.assertRaises(ValueError):
            compute_peer_baselines(self.peers, "D21", config=self.config)

    def test_low_peer_count_still_observes_after_current_traffic_verified(self):
        result = apply_current_lifecycle_decisions(
            pd.DataFrame([_lifecycle_row(age=20)]),
            peer_frame=_peer_rows(19),
            config=self.config,
        )

        self.assertEqual(result.loc[0, "判定结果"], RESULT_OBSERVE)
        self.assertEqual(result.loc[0, "判定原因"], REASON_PEER_INSUFFICIENT)


if __name__ == "__main__":
    unittest.main()
