import unittest

import pandas as pd

from core.new_product_lifecycle import (
    CHECKPOINT_D14,
    CHECKPOINT_D21,
    CHECKPOINT_D30,
    LifecycleDecisionConfig,
    REASON_D14_SALE_PROTECTED,
    REASON_D14_WEAK,
    REASON_D21_SALE_PROTECTED,
    REASON_D21_WEAK,
    REASON_DATA_INCOMPLETE,
    REASON_HISTORY_TRUNCATED,
    REASON_PEER_INSUFFICIENT,
    REASON_PERFORMANCE_NORMAL,
    REASON_TRAFFIC_INSUFFICIENT,
    RESULT_DOWN,
    RESULT_OBSERVE,
    apply_lifecycle_decisions,
    compute_peer_baselines,
)


SUM_FIELDS = ("商品访客数", "商品加购件数", "支付买家数", "支付件数", "GMV")


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


def _sync_cumulative(row):
    for field in SUM_FIELDS:
        row[f"D1_D21_{field}"] = (
            float(row.get(f"D1_D14_{field}", 0.0))
            + float(row.get(f"D15_D21_{field}", 0.0))
        )
    row["D1_D21_有效数据天数"] = (
        int(row.get("D1_D14_有效数据天数", 0))
        + int(row.get("D15_D21_有效数据天数", 0))
    )
    visitors = row["D1_D21_商品访客数"]
    row["D1_D21_商品加购率"] = row["D1_D21_商品加购件数"] / visitors if visitors > 0 else 0
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


def _lifecycle_row(style_key="TARGET", age=14):
    row = {
        "style_key": style_key,
        "首次在线发现日": "2026-08-01",
        "新品年龄": age,
        "是否历史截断": False,
        "淘宝类目": "半身裙",
        "价格段": "100-200",
        "D14商品状态": "当前在线",
        "D14在仓库存": 10,
        "D21商品状态": "当前在线",
        "D21在仓库存": 10,
        "当前商品状态": "当前在线",
        "当前在仓库存": 10,
    }
    _set_period(row, "D1_D14", 300, 9, buyers=0, units=0, gmv=0, days=14)
    _set_period(row, "D15_D21", 300, 6, buyers=1, units=1, gmv=600, days=7)
    return _sync_cumulative(row)


def _peer_rows(count=20):
    rows = []
    for i in range(count):
        rows.append(_valid_peer(f"PEER-{i}"))
    return pd.DataFrame(rows)


def _valid_peer(style_key):
    row = _lifecycle_row(style_key=style_key, age=21)
    _set_period(row, "D1_D14", 200, 20, buyers=10, units=10, gmv=2000, days=14)
    _set_period(row, "D15_D21", 200, 20, buyers=10, units=10, gmv=2000, days=7)
    return _sync_cumulative(row)


def _valid_peer_with(style_key, **updates):
    row = _valid_peer(style_key)
    row.update(updates)
    return row


def _polluting_peer(style_key, **updates):
    row = _valid_peer(style_key)
    _set_period(row, "D1_D14", 1000, 900, buyers=800, units=800, gmv=90000, days=14)
    _set_period(row, "D15_D21", 1000, 900, buyers=800, units=800, gmv=90000, days=7)
    _sync_cumulative(row)
    row.update(updates)
    return row


class NewProductDecisionTests(unittest.TestCase):
    def setUp(self):
        self.config = LifecycleDecisionConfig(
            decision_visitor_min=300,
            period_min_valid_days=12,
            min_peer_count=20,
            low_ratio=0.8,
        )
        self.peers = _peer_rows()

    def test_d14_down_requires_traffic_peer_low_and_no_sale(self):
        result = apply_lifecycle_decisions(
            pd.DataFrame([_lifecycle_row(age=14)]),
            CHECKPOINT_D14,
            peer_frame=self.peers,
            config=self.config,
        )

        self.assertEqual(result.loc[0, "判定结果"], RESULT_DOWN)
        self.assertEqual(result.loc[0, "判定原因"], REASON_D14_WEAK)
        self.assertEqual(result.loc[0, "保护状态"], "无保护")
        self.assertEqual(result.loc[0, "流量验证"], "已验证")

    def test_d14_sale_protects_before_history_data_and_traffic_guards(self):
        row = _lifecycle_row(age=14)
        _set_period(row, "D1_D14", 120, 1, buyers=2, units=2, gmv=580.50, days=5)
        row["是否历史截断"] = True
        row["D14商品状态"] = "已下架"
        row["当前商品状态"] = "已下架"
        row["D14在仓库存"] = 0
        row["当前在仓库存"] = 0
        _sync_cumulative(row)

        result = apply_lifecycle_decisions(
            pd.DataFrame([row]),
            CHECKPOINT_D14,
            peer_frame=self.peers,
            config=self.config,
        )

        self.assertEqual(result.loc[0, "判定结果"], RESULT_OBSERVE)
        self.assertEqual(result.loc[0, "判定原因"], REASON_D14_SALE_PROTECTED)
        self.assertEqual(result.loc[0, "保护状态"], "动销保护")
        self.assertIn("支付买家2人", result.loc[0, "保护证据"])
        self.assertIn("支付件数2件", result.loc[0, "保护证据"])
        self.assertIn("GMV580.50", result.loc[0, "保护证据"])
        self.assertEqual(result.loc[0, "流量验证"], "待流量验证")
        self.assertEqual(result.loc[0, "动作可执行"], "当前无在线链接（已下架）")
        self.assertEqual(result.loc[0, "库存提示"], "无库存（0）")

    def test_d14_history_data_traffic_and_peer_guards_observe(self):
        censored = _lifecycle_row(age=14)
        censored["是否历史截断"] = True
        censored_result = apply_lifecycle_decisions(
            pd.DataFrame([censored]),
            CHECKPOINT_D14,
            peer_frame=self.peers,
            config=self.config,
        )
        self.assertEqual(censored_result.loc[0, "判定原因"], REASON_HISTORY_TRUNCATED)

        data_short = _lifecycle_row(age=14)
        data_short["D1_D14_有效数据天数"] = 11
        data_result = apply_lifecycle_decisions(
            pd.DataFrame([data_short]),
            CHECKPOINT_D14,
            peer_frame=self.peers,
            config=self.config,
        )
        self.assertEqual(data_result.loc[0, "判定原因"], REASON_DATA_INCOMPLETE)
        self.assertEqual(data_result.loc[0, "流量验证"], "已验证")

        low_traffic = _lifecycle_row(age=14)
        _set_period(low_traffic, "D1_D14", 299, 9, buyers=0, units=0, gmv=0, days=14)
        _sync_cumulative(low_traffic)
        low_traffic_result = apply_lifecycle_decisions(
            pd.DataFrame([low_traffic]),
            CHECKPOINT_D14,
            peer_frame=self.peers,
            config=self.config,
        )
        self.assertEqual(low_traffic_result.loc[0, "判定原因"], REASON_TRAFFIC_INSUFFICIENT)
        self.assertEqual(low_traffic_result.loc[0, "流量验证"], "待流量验证")

        no_peer_result = apply_lifecycle_decisions(
            pd.DataFrame([_lifecycle_row(age=14)]),
            CHECKPOINT_D14,
            peer_frame=_peer_rows(19),
            config=self.config,
        )
        self.assertEqual(no_peer_result.loc[0, "判定原因"], REASON_PEER_INSUFFICIENT)

    def test_d21_lifetime_sale_protection_includes_d1_d14_sale(self):
        row = _lifecycle_row(style_key="B1476D58472", age=21)
        _set_period(row, "D1_D14", 300, 9, buyers=1, units=1, gmv=200, days=14)
        _set_period(row, "D15_D21", 300, 3, buyers=0, units=0, gmv=0, days=7)
        _sync_cumulative(row)

        result = apply_lifecycle_decisions(
            pd.DataFrame([row]),
            CHECKPOINT_D21,
            peer_frame=self.peers,
            config=self.config,
        )

        self.assertEqual(result.loc[0, "style_key"], "B1476D58472")
        self.assertEqual(result.loc[0, "判定结果"], RESULT_OBSERVE)
        self.assertEqual(result.loc[0, "判定原因"], REASON_D21_SALE_PROTECTED)
        self.assertEqual(result.loc[0, "保护状态"], "动销保护")
        self.assertIn("支付买家1人", result.loc[0, "保护证据"])
        self.assertIn("支付件数1件", result.loc[0, "保护证据"])
        self.assertIn("GMV200.00", result.loc[0, "保护证据"])
        self.assertIn("支付买家1人", result.loc[0, "本轮成交"])

    def test_d21_lifetime_sale_protection_accepts_any_paid_metric(self):
        cases = [
            ("buyers", {"buyers": 1, "units": 0, "gmv": 0}, "支付买家1人"),
            ("units", {"buyers": 0, "units": 1, "gmv": 0}, "支付件数1件"),
            ("gmv", {"buyers": 0, "units": 0, "gmv": 99.9}, "GMV99.90"),
        ]
        for case_name, sale_args, evidence in cases:
            with self.subTest(case=case_name):
                row = _lifecycle_row(age=21)
                _set_period(row, "D1_D14", 150, 0, days=14, **sale_args)
                _set_period(row, "D15_D21", 149, 0, buyers=0, units=0, gmv=0, days=7)
                _sync_cumulative(row)

                result = apply_lifecycle_decisions(
                    pd.DataFrame([row]),
                    CHECKPOINT_D21,
                    peer_frame=self.peers,
                    config=self.config,
                )

                self.assertEqual(result.loc[0, "D1_D21_商品访客数"], 299.0)
                self.assertEqual(result.loc[0, "判定结果"], RESULT_OBSERVE)
                self.assertEqual(result.loc[0, "判定原因"], REASON_D21_SALE_PROTECTED)
                self.assertEqual(result.loc[0, "保护状态"], "动销保护")
                self.assertIn(evidence, result.loc[0, "保护证据"])

    def test_d21_sale_protects_before_history_data_and_traffic_guards(self):
        row = _lifecycle_row(age=21)
        _set_period(row, "D15_D21", 120, 1, buyers=3, units=3, gmv=1079.70, days=5)
        row["是否历史截断"] = True
        row["D21商品状态"] = "已下架"
        row["当前商品状态"] = "已下架"
        row["D21在仓库存"] = 0
        row["当前在仓库存"] = 0
        _sync_cumulative(row)

        result = apply_lifecycle_decisions(
            pd.DataFrame([row]),
            CHECKPOINT_D21,
            peer_frame=self.peers,
            config=self.config,
        )

        self.assertEqual(result.loc[0, "判定结果"], RESULT_OBSERVE)
        self.assertEqual(result.loc[0, "判定原因"], REASON_D21_SALE_PROTECTED)
        self.assertEqual(result.loc[0, "保护状态"], "动销保护")
        self.assertIn("支付买家3人", result.loc[0, "保护证据"])
        self.assertIn("支付件数3件", result.loc[0, "保护证据"])
        self.assertIn("GMV1079.70", result.loc[0, "保护证据"])

    def test_d21_traffic_gate_uses_cumulative_visitors(self):
        row = _lifecycle_row(age=21)
        _set_period(row, "D1_D14", 250, 5, buyers=0, units=0, gmv=0, days=14)
        _set_period(row, "D15_D21", 50, 0, buyers=0, units=0, gmv=0, days=7)
        _sync_cumulative(row)

        result = apply_lifecycle_decisions(
            pd.DataFrame([row]),
            CHECKPOINT_D21,
            peer_frame=self.peers,
            config=self.config,
        )

        self.assertEqual(result.loc[0, "D1_D21_商品访客数"], 300.0)
        self.assertEqual(result.loc[0, "判定结果"], RESULT_DOWN)
        self.assertEqual(result.loc[0, "判定原因"], REASON_D21_WEAK)
        self.assertEqual(result.loc[0, "流量验证"], "已验证")

    def test_d21_cumulative_traffic_under_300_observes(self):
        row = _lifecycle_row(age=21)
        _set_period(row, "D1_D14", 250, 5, buyers=0, units=0, gmv=0, days=14)
        _set_period(row, "D15_D21", 49, 0, buyers=0, units=0, gmv=0, days=7)
        _sync_cumulative(row)

        result = apply_lifecycle_decisions(
            pd.DataFrame([row]),
            CHECKPOINT_D21,
            peer_frame=self.peers,
            config=self.config,
        )

        self.assertEqual(result.loc[0, "判定结果"], RESULT_OBSERVE)
        self.assertEqual(result.loc[0, "判定原因"], REASON_TRAFFIC_INSUFFICIENT)
        self.assertEqual(result.loc[0, "流量验证"], "待流量验证")

    def test_d30_traffic_validation_uses_cumulative_visitors_not_effective_days(self):
        row = _lifecycle_row(style_key="B1476D56012", age=45)
        _set_period(row, "D1_D15", 324, 3, buyers=0, units=0, gmv=0, days=14)
        _set_period(row, "D16_D30", 4, 0, buyers=0, units=0, gmv=0, days=3)
        _sync_d30_cumulative(row)

        result = apply_lifecycle_decisions(
            pd.DataFrame([row]),
            CHECKPOINT_D30,
            peer_frame=self.peers,
            config=self.config,
        )

        self.assertEqual(result.loc[0, "D1_D30_商品访客数"], 328.0)
        self.assertEqual(result.loc[0, "判定结果"], RESULT_OBSERVE)
        self.assertEqual(result.loc[0, "判定原因"], REASON_DATA_INCOMPLETE)
        self.assertEqual(result.loc[0, "流量验证"], "已验证")

    def test_d30_low_cumulative_traffic_takes_priority_over_incomplete_days(self):
        row = _lifecycle_row(age=45)
        _set_period(row, "D1_D15", 295, 3, buyers=0, units=0, gmv=0, days=14)
        _set_period(row, "D16_D30", 4, 0, buyers=0, units=0, gmv=0, days=3)
        _sync_d30_cumulative(row)

        result = apply_lifecycle_decisions(
            pd.DataFrame([row]),
            CHECKPOINT_D30,
            peer_frame=self.peers,
            config=self.config,
        )

        self.assertEqual(result.loc[0, "判定结果"], RESULT_OBSERVE)
        self.assertEqual(result.loc[0, "判定原因"], REASON_TRAFFIC_INSUFFICIENT)
        self.assertEqual(result.loc[0, "流量验证"], "待流量验证")

    def test_d21_zero_cart_count_is_strong_down_evidence(self):
        row = _lifecycle_row(age=21)
        _set_period(row, "D1_D14", 300, 0, buyers=0, units=0, gmv=0, days=14)
        _set_period(row, "D15_D21", 0, 0, buyers=0, units=0, gmv=0, days=7)
        _sync_cumulative(row)

        result = apply_lifecycle_decisions(
            pd.DataFrame([row]),
            CHECKPOINT_D21,
            peer_frame=self.peers,
            config=self.config,
        )

        self.assertEqual(result.loc[0, "判定结果"], RESULT_DOWN)
        self.assertEqual(result.loc[0, "判定原因"], REASON_D21_WEAK)
        self.assertEqual(result.loc[0, "D1_D21_商品加购件数"], 0.0)
        self.assertEqual(result.loc[0, "保护状态"], "无保护")

    def test_cvr_and_uv_are_not_d14_or_d21_down_criteria(self):
        cases = [
            (CHECKPOINT_D14, "D1_D14", 14),
            (CHECKPOINT_D21, "D1_D21", 21),
        ]
        for checkpoint, prefix, age in cases:
            with self.subTest(checkpoint=checkpoint):
                row = _lifecycle_row(age=age)
                if checkpoint == CHECKPOINT_D14:
                    _set_period(row, "D1_D14", 300, 30, buyers=0, units=0, gmv=0, days=14)
                else:
                    _set_period(row, "D1_D14", 150, 15, buyers=0, units=0, gmv=0, days=14)
                    _set_period(row, "D15_D21", 150, 15, buyers=0, units=0, gmv=0, days=7)
                _sync_cumulative(row)

                result = apply_lifecycle_decisions(
                    pd.DataFrame([row]),
                    checkpoint,
                    peer_frame=self.peers,
                    config=self.config,
                )

                self.assertEqual(result.loc[0, "判定结果"], RESULT_OBSERVE)
                self.assertEqual(result.loc[0, "判定原因"], REASON_PERFORMANCE_NORMAL)
                self.assertEqual(result.loc[0, "保护状态"], "无保护")

    def test_d14_peer_baseline_filters_unqualified_styles_before_counting(self):
        config = LifecycleDecisionConfig(min_peer_count=2)
        peers = pd.DataFrame([
            _valid_peer("VALID-1"),
            _valid_peer("VALID-2"),
            _polluting_peer("YOUNG", **{"新品年龄": 13}),
            _polluting_peer("CENSORED", **{"是否历史截断": True}),
            _polluting_peer("SHORT-D1-D14", **{"D1_D14_有效数据天数": 11}),
            _polluting_peer("ZERO-D1-D14", **{"D1_D14_商品访客数": 0}),
            _valid_peer_with("OFFLINE", **{"D14商品状态": "已下架"}),
            _valid_peer_with("NO-STOCK", **{"D14在仓库存": 0}),
        ])

        result = compute_peer_baselines(peers, CHECKPOINT_D14, config=config)
        row = result.iloc[0]

        self.assertEqual(row["同层样本数"], 4)
        self.assertAlmostEqual(row["D1_D14_商品加购率_B"], 0.10)
        self.assertNotIn("D1_D14_商品支付转化率_B", row.index)
        self.assertNotIn("D1_D14_访客平均价值_B", row.index)

    def test_d21_peer_baseline_filters_unqualified_styles_before_counting(self):
        config = LifecycleDecisionConfig(min_peer_count=2)
        peers = pd.DataFrame([
            _valid_peer("VALID-1"),
            _valid_peer("VALID-2"),
            _polluting_peer("YOUNG", **{"新品年龄": 20}),
            _polluting_peer("CENSORED", **{"是否历史截断": True}),
            _polluting_peer("SHORT-D1-D21", **{"D1_D21_有效数据天数": 11}),
            _polluting_peer("ZERO-D1-D21", **{"D1_D21_商品访客数": 0}),
            _valid_peer_with("OFFLINE", **{"D21商品状态": "已下架"}),
            _valid_peer_with("NO-STOCK", **{"D21在仓库存": 0}),
        ])

        result = compute_peer_baselines(peers, CHECKPOINT_D21, config=config)
        row = result.iloc[0]

        self.assertEqual(row["同层样本数"], 4)
        self.assertAlmostEqual(row["D1_D21_商品加购率_B"], 0.10)
        self.assertNotIn("D1_D21_商品支付转化率_B", row.index)
        self.assertNotIn("D1_D21_访客平均价值_B", row.index)


if __name__ == "__main__":
    unittest.main()
