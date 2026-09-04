import unittest

import pandas as pd

from core.new_product_backtest import (
    BacktestColumns as C,
    BacktestConfig,
    DECISION_DOWN,
    DECISION_OBSERVE,
    FUTURE_FAILURE,
    FUTURE_RECOVERED,
    FUTURE_UNVERIFIABLE,
    apply_backtest_rules,
    prepare_checkpoint_rows,
    run_backtest,
    summarize_backtest,
)


def day_row(style, day, visitors=0, carts=0, buyers=0, units=0, gmv=0.0, **overrides):
    row = {
        C.style_key: style,
        C.age_day: day,
        C.store: "store-a",
        C.category: "skirts",
        C.price_band: "100-200",
        C.visitors: visitors,
        C.carts: carts,
        C.buyers: buyers,
        C.units: units,
        C.gmv: gmv,
        C.profit: gmv * 0.4,
        C.valid_data_day: True,
        C.is_online: True,
        C.stock_ok: True,
        C.manual_removed: False,
        C.data_missing: False,
        C.historical_truncated: False,
        C.main_ctr: 0.03,
        C.creative_impressions: 0,
        C.creative_clicks: 0,
        C.metox_labels: "",
    }
    row.update(overrides)
    return row


def weak_days(style, days, visitors=20, carts=1):
    return [
        day_row(style, day, visitors=visitors, carts=carts, buyers=0, units=0, gmv=0.0)
        for day in days
    ]


class NewProductBacktestTests(unittest.TestCase):
    def test_prepare_checkpoint_rows_uses_15_and_30_day_checkpoints(self):
        early = prepare_checkpoint_rows(pd.DataFrame(weak_days("EARLY", range(1, 15), visitors=20)))
        self.assertTrue(early.empty)

        d15_only = prepare_checkpoint_rows(pd.DataFrame(weak_days("D15ONLY", range(1, 16), visitors=20)))
        self.assertEqual(list(d15_only[C.checkpoint]), [15])

        d30 = prepare_checkpoint_rows(pd.DataFrame(weak_days("D30", range(1, 31), visitors=20)))
        self.assertEqual(list(d30[C.checkpoint]), [15, 30])

    def test_prepare_checkpoint_rows_keeps_current_and_future_15_day_windows_separate(self):
        rows = []
        rows += weak_days("SKU1", range(1, 16), visitors=25)
        rows += [
            day_row("SKU1", day, visitors=20, carts=6, buyers=2, units=2, gmv=200.0)
            for day in range(16, 31)
        ]
        checkpoint = prepare_checkpoint_rows(pd.DataFrame(rows))
        d15 = checkpoint[checkpoint[C.checkpoint].eq(15)].iloc[0]

        self.assertEqual(d15["previous_start_day"], 1)
        self.assertEqual(d15["previous_end_day"], 0)
        self.assertEqual(d15["previous_valid_days"], 0)
        self.assertEqual(d15["current_start_day"], 1)
        self.assertEqual(d15["current_end_day"], 15)
        self.assertEqual(d15["validation_start_day"], 16)
        self.assertEqual(d15["validation_end_day"], 30)
        self.assertEqual(d15["current_visitors"], 375)
        self.assertEqual(d15["cumulative_start_day"], 1)
        self.assertEqual(d15["cumulative_end_day"], 15)
        self.assertEqual(d15["cumulative_visitors"], 375)
        self.assertEqual(d15["validation_visitors"], 300)
        self.assertGreater(d15["validation_gmv"], d15["current_gmv"])

    def test_d30_uses_second_15_day_cycle_and_following_validation_cycle(self):
        rows = []
        rows += weak_days("SKU2", range(1, 16), visitors=20, carts=2)
        rows += weak_days("SKU2", range(16, 31), visitors=10, carts=0)
        rows += weak_days("SKU2", range(31, 46), visitors=20, carts=0)
        checkpoint = prepare_checkpoint_rows(pd.DataFrame(rows))
        d30 = checkpoint[checkpoint[C.checkpoint].eq(30)].iloc[0]

        self.assertEqual(d30["previous_start_day"], 1)
        self.assertEqual(d30["previous_end_day"], 15)
        self.assertEqual(d30["current_start_day"], 16)
        self.assertEqual(d30["current_end_day"], 30)
        self.assertEqual(d30["cumulative_start_day"], 1)
        self.assertEqual(d30["cumulative_end_day"], 30)
        self.assertEqual(d30["validation_start_day"], 31)
        self.assertEqual(d30["validation_end_day"], 45)
        self.assertEqual(d30["current_visitors"], 150)
        self.assertEqual(d30["cumulative_visitors"], 450)
        self.assertEqual(d30["validation_valid_days"], 15)

    def test_rule_a_d15_down_uses_cumulative_sales_and_traffic_without_trend_gate(self):
        frame = pd.DataFrame([
            self._checkpoint_row(
                "d15-down",
                checkpoint=15,
                current_visitors=300,
                cumulative_visitors=300,
                current_valid_days=12,
                current_cart_rate=0.05,
                previous_cart_rate=float("nan"),
            )
        ])

        scored = apply_backtest_rules(frame).set_index(C.style_key)

        self.assertEqual(scored.loc["d15-down", "rule_A_decision"], DECISION_DOWN)
        self.assertEqual(scored.loc["d15-down", "rule_A_status"], "down_eligible")
        self.assertEqual(scored.loc["d15-down", "future_label"], FUTURE_FAILURE)

    def test_rule_a_d30_uses_cumulative_traffic_and_cumulative_sale_protection(self):
        frame = pd.DataFrame([
            self._checkpoint_row(
                "d30-low-current-traffic",
                checkpoint=30,
                current_visitors=50,
                cumulative_visitors=350,
                current_valid_days=12,
                current_cart_rate=0.04,
                previous_cart_rate=0.05,
            ),
            self._checkpoint_row(
                "d30-sale-protected",
                checkpoint=30,
                current_visitors=50,
                cumulative_visitors=350,
                current_valid_days=12,
                current_buyers=0,
                cumulative_buyers=1,
            ),
        ])

        scored = apply_backtest_rules(frame).set_index(C.style_key)

        self.assertEqual(scored.loc["d30-low-current-traffic", "rule_A_decision"], DECISION_DOWN)
        self.assertEqual(scored.loc["d30-low-current-traffic", "rule_A_status"], "down_eligible")
        self.assertEqual(scored.loc["d30-sale-protected", "rule_A_decision"], DECISION_OBSERVE)
        self.assertEqual(scored.loc["d30-sale-protected", "rule_A_status"], "paid_sale_protected")

    def test_unit_only_sale_survives_daily_collapse_and_protects_cumulative_cycle(self):
        rows = weak_days("SKU-UNIT", range(1, 16), visitors=20, carts=0)
        rows.append(
            day_row("SKU-UNIT", 5, visitors=0, carts=0, buyers=0, units=1, gmv=0.0)
        )

        checkpoint = prepare_checkpoint_rows(pd.DataFrame(rows))
        d15 = checkpoint[checkpoint[C.checkpoint].eq(15)].iloc[0]

        self.assertEqual(d15["current_units"], 1)
        scored = apply_backtest_rules(checkpoint).set_index(C.style_key)
        self.assertEqual(scored.loc["SKU-UNIT", "rule_A_decision"], DECISION_OBSERVE)
        self.assertEqual(scored.loc["SKU-UNIT", "rule_A_status"], "paid_sale_protected")

    def test_rule_a_requires_cumulative_traffic_current_valid_days_and_peer_count(self):
        frame = pd.DataFrame([
            self._checkpoint_row("low-cumulative-traffic", cumulative_visitors=299),
            self._checkpoint_row("low-valid-days", current_valid_days=11),
            self._checkpoint_row("low-peer-count", peer_count=19),
        ])

        scored = apply_backtest_rules(frame).set_index(C.style_key)

        self.assertEqual(scored.loc["low-cumulative-traffic", "rule_A_status"], "traffic_insufficient")
        self.assertEqual(scored.loc["low-valid-days", "rule_A_status"], "valid_days_insufficient")
        self.assertEqual(scored.loc["low-peer-count", "rule_A_status"], "baseline_insufficient")
        self.assertTrue((scored["rule_A_decision"] == DECISION_OBSERVE).all())

    def test_rule_a_ignores_online_and_stock_for_down_decision(self):
        frame = pd.DataFrame([
            self._checkpoint_row(
                "offline-or-no-stock",
                is_online_at_checkpoint=False,
                stock_ok_at_checkpoint=False,
            )
        ])

        scored = apply_backtest_rules(frame).set_index(C.style_key)

        self.assertEqual(scored.loc["offline-or-no-stock", "rule_A_decision"], DECISION_DOWN)
        self.assertEqual(scored.loc["offline-or-no-stock", "rule_A_status"], "down_eligible")

    def test_d30_cart_improvement_blocks_down_when_still_below_peer_baseline(self):
        frame = pd.DataFrame([
            self._checkpoint_row(
                "d30-improving",
                checkpoint=30,
                current_cart_rate=0.06,
                previous_cart_rate=0.05,
                baseline_cart_rate=0.10,
            )
        ])

        scored = apply_backtest_rules(frame).set_index(C.style_key)

        self.assertEqual(scored.loc["d30-improving", "rule_A_decision"], DECISION_OBSERVE)
        self.assertEqual(scored.loc["d30-improving", "rule_A_status"], "cart_rate_improving")

    def test_prepare_checkpoint_rows_computes_same_layer_peer_baseline(self):
        rows = []
        for index in range(20):
            carts = 0 if index == 0 else 2
            rows += weak_days(f"SKU{index:02d}", range(1, 16), visitors=20, carts=carts)

        checkpoint = prepare_checkpoint_rows(pd.DataFrame(rows))
        target = checkpoint[
            checkpoint[C.style_key].eq("SKU00") & checkpoint[C.checkpoint].eq(15)
        ].iloc[0]

        self.assertEqual(target[C.peer_count], 20)
        self.assertGreater(target[C.baseline_cart_rate], 0)

        scored = apply_backtest_rules(checkpoint).set_index([C.style_key, C.checkpoint])
        self.assertEqual(scored.loc[("SKU00", 15), "rule_A_decision"], DECISION_DOWN)

    def test_future_manual_removed_or_stockout_do_not_block_validation(self):
        frame = pd.DataFrame([
            self._checkpoint_row("manual-removed", validation_manual_removed=True),
            self._checkpoint_row("stockout", validation_stockout_days=5),
            self._checkpoint_row("missing", validation_data_missing=True),
            self._checkpoint_row("low-traffic", validation_visitors=99),
            self._checkpoint_row("low-days", validation_valid_days=11),
        ])

        scored = apply_backtest_rules(frame).set_index(C.style_key)

        self.assertEqual(scored.loc["manual-removed", "future_label"], FUTURE_FAILURE)
        self.assertEqual(scored.loc["stockout", "future_label"], FUTURE_FAILURE)
        self.assertEqual(scored.loc["missing", "future_label"], FUTURE_UNVERIFIABLE)
        self.assertEqual(scored.loc["low-traffic", "future_label"], FUTURE_UNVERIFIABLE)
        self.assertEqual(scored.loc["low-days", "future_label"], FUTURE_UNVERIFIABLE)

    def test_rules_b_c_d_are_parallel_protected_variants_of_rule_a(self):
        frame = pd.DataFrame([
            self._checkpoint_row("plain"),
            self._checkpoint_row(
                "ctr",
                baseline_creative_ctr=0.04,
                current_creative_ctr=0.06,
                current_creative_impressions=200,
            ),
            self._checkpoint_row("metox", metox_labels_at_checkpoint="明日之星"),
            self._checkpoint_row(
                "both",
                metox_labels_at_checkpoint="保护持有",
                baseline_main_ctr=0.03,
                current_main_ctr=0.05,
            ),
        ])

        scored = apply_backtest_rules(frame).set_index(C.style_key)

        self.assertEqual(scored.loc["plain", "rule_A_decision"], DECISION_DOWN)
        self.assertEqual(scored.loc["plain", "rule_B_decision"], DECISION_DOWN)
        self.assertEqual(scored.loc["plain", "rule_C_decision"], DECISION_DOWN)
        self.assertEqual(scored.loc["plain", "rule_D_decision"], DECISION_DOWN)

        self.assertEqual(scored.loc["ctr", "rule_A_decision"], DECISION_DOWN)
        self.assertEqual(scored.loc["ctr", "rule_B_decision"], DECISION_OBSERVE)
        self.assertEqual(scored.loc["ctr", "rule_C_decision"], DECISION_DOWN)
        self.assertEqual(scored.loc["ctr", "rule_D_decision"], DECISION_OBSERVE)

        self.assertEqual(scored.loc["metox", "rule_B_decision"], DECISION_DOWN)
        self.assertEqual(scored.loc["metox", "rule_C_decision"], DECISION_OBSERVE)
        self.assertEqual(scored.loc["metox", "rule_D_decision"], DECISION_OBSERVE)

        self.assertEqual(scored.loc["both", "rule_B_decision"], DECISION_OBSERVE)
        self.assertEqual(scored.loc["both", "rule_C_decision"], DECISION_OBSERVE)
        self.assertEqual(scored.loc["both", "rule_D_decision"], DECISION_OBSERVE)

    def test_summary_outputs_grouped_exportable_metrics_without_counting_unverifiable_hits(self):
        frame = pd.DataFrame([
            self._checkpoint_row("hit"),
            self._checkpoint_row(
                "false-kill",
                validation_cart_rate=0.20,
                validation_gmv=500.0,
                validation_profit=180.0,
            ),
            self._checkpoint_row("unverifiable", validation_data_missing=True),
            self._checkpoint_row(
                "protected",
                validation_cart_rate=0.20,
                validation_gmv=300.0,
                validation_profit=120.0,
                metox_labels_at_checkpoint="明日之星",
            ),
        ])
        scored = apply_backtest_rules(frame)
        summary = summarize_backtest(scored, group_cols=[C.store, C.category, C.price_band])

        a = summary[summary["rule"].eq("A")].iloc[0]
        c = summary[summary["rule"].eq("C")].iloc[0]

        self.assertEqual(a["total"], 4)
        self.assertEqual(a["predicted_down"], 4)
        self.assertEqual(a["verifiable"], 3)
        self.assertAlmostEqual(a["down_hit_rate"], 1 / 3)
        self.assertAlmostEqual(a["false_kill_rate"], 2 / 3)
        self.assertAlmostEqual(a["recall"], 1.0)
        self.assertEqual(a["opportunity_loss_gmv"], 800.0)
        self.assertEqual(a["opportunity_loss_profit"], 300.0)
        self.assertEqual(a["metox_protected_count"], 1)
        self.assertAlmostEqual(a["metox_protection_effectiveness"], 1.0)
        self.assertEqual(c["predicted_down"], 3)
        self.assertIn(C.store, summary.columns)
        self.assertIn(C.category, summary.columns)
        self.assertIn(C.price_band, summary.columns)

    def test_run_backtest_returns_d15_d30_scored_rows_and_exportable_summary(self):
        rows = []
        for index in range(20):
            carts = 0 if index == 0 else 2
            rows += weak_days(f"SKU{index:02d}", range(1, 31), visitors=20, carts=carts)

        result = run_backtest(pd.DataFrame(rows), group_cols=[C.checkpoint])

        self.assertEqual(set(result.checkpoint_rows[C.checkpoint]), {15, 30})
        self.assertIn("rule_A_decision", result.checkpoint_rows.columns)
        self.assertIn("rule_D_decision", result.checkpoint_rows.columns)
        self.assertIn("down_hit_rate", result.summary.columns)
        self.assertIn(C.checkpoint, result.summary.columns)

    def _checkpoint_row(self, style, **overrides):
        row = {
            C.style_key: style,
            C.checkpoint: 15,
            C.store: "store-a",
            C.category: "skirts",
            C.price_band: "100-200",
            "previous_visitors": 0,
            "previous_valid_days": 0,
            "previous_cart_rate": float("nan"),
            "current_visitors": 350,
            "current_valid_days": 15,
            "current_buyers": 0,
            "current_units": 0,
            "current_gmv": 0.0,
            "current_cart_rate": 0.05,
            "current_pay_cvr": 0.0,
            "current_uv_value": 0.0,
            "cumulative_visitors": 350,
            "cumulative_valid_days": 15,
            "cumulative_buyers": 0,
            "cumulative_units": 0,
            "cumulative_gmv": 0.0,
            "cumulative_cart_rate": 0.05,
            "cumulative_pay_cvr": 0.0,
            "cumulative_uv_value": 0.0,
            "baseline_cart_rate": 0.10,
            "baseline_pay_cvr": 0.05,
            "baseline_uv_value": 5.0,
            C.peer_count: 20,
            "baseline_creative_ctr": 0.04,
            "baseline_main_ctr": 0.03,
            "current_creative_impressions": 0,
            "current_creative_ctr": 0.0,
            "current_main_ctr": 0.0,
            "metox_labels_at_checkpoint": "",
            "validation_visitors": 140,
            "validation_valid_days": 15,
            "validation_cart_rate": 0.01,
            "validation_pay_cvr": 0.0,
            "validation_uv_value": 0.0,
            "validation_gmv": 0.0,
            "validation_profit": 0.0,
            "validation_manual_removed": False,
            "validation_stockout_days": 0,
            "validation_data_missing": False,
            "is_online_at_checkpoint": True,
            "stock_ok_at_checkpoint": True,
            "data_missing_at_checkpoint": False,
            C.historical_truncated: False,
        }
        row.update(overrides)
        return row


if __name__ == "__main__":
    unittest.main()
