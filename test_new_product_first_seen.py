import unittest

import pandas as pd

from core.new_product_first_seen import (
    FIRST_SEEN_DATE,
    FIRST_SEEN_PRODUCT_ID,
    HISTORY_START_DATE,
    IS_HISTORY_TRUNCATED,
    MATCH_CONFIDENCE,
    MATCH_CONFIDENCE_HIGH,
    MATCH_CONFIDENCE_LOW,
    STYLE_KEY,
    STYLE_KEY_SOURCE,
    build_first_seen_index,
    build_style_key_series,
)


class NewProductFirstSeenTests(unittest.TestCase):
    def test_style_key_prefers_normalized_sku_and_falls_back_to_product_id(self):
        frame = pd.DataFrame({
            "货号": [" a001 ", "", None, "123.0"],
            "宝贝ID": ["9001", "9002.0", "9003", "9004"],
        })

        result = build_style_key_series(frame)

        self.assertEqual(result.loc[0, STYLE_KEY], "A001")
        self.assertEqual(result.loc[0, STYLE_KEY_SOURCE], "货号")
        self.assertEqual(result.loc[0, MATCH_CONFIDENCE], MATCH_CONFIDENCE_HIGH)
        self.assertEqual(result.loc[1, STYLE_KEY], "9002")
        self.assertEqual(result.loc[1, STYLE_KEY_SOURCE], "宝贝ID")
        self.assertEqual(result.loc[1, MATCH_CONFIDENCE], MATCH_CONFIDENCE_LOW)
        self.assertEqual(result.loc[3, STYLE_KEY], "123")

    def test_build_index_uses_first_online_day_and_marks_history_truncated(self):
        day1 = pd.DataFrame([
            {
                "宝贝ID": "1001",
                "货号": "sku-a",
                "商品状态": "当前在线",
                "创建时间": "2026-07-20 10:00:00",
            },
            {
                "宝贝ID": "1002",
                "货号": "sku-b",
                "商品状态": "已下架",
            },
        ])
        day2 = pd.DataFrame([
            {
                "宝贝ID": "1002",
                "货号": "sku-b",
                "商品状态": "当前在线",
            },
        ])

        result = build_first_seen_index(
            [("2026-08-01", day1), ("2026-08-02", day2)],
            history_start_date="2026-08-01",
        ).set_index(STYLE_KEY)

        self.assertEqual(result.loc["SKU-A", FIRST_SEEN_DATE], "2026-08-01")
        self.assertEqual(result.loc["SKU-A", FIRST_SEEN_PRODUCT_ID], "1001")
        self.assertTrue(bool(result.loc["SKU-A", IS_HISTORY_TRUNCATED]))
        self.assertEqual(result.loc["SKU-A", HISTORY_START_DATE], "2026-08-01")
        self.assertEqual(result.loc["SKU-B", FIRST_SEEN_DATE], "2026-08-02")
        self.assertFalse(bool(result.loc["SKU-B", IS_HISTORY_TRUNCATED]))
        self.assertNotIn("left_censored", result.columns)

    def test_legacy_left_censored_input_is_folded_into_history_truncated(self):
        existing = pd.DataFrame([{
            STYLE_KEY: "sku-a",
            FIRST_SEEN_DATE: "2026-08-03",
            FIRST_SEEN_PRODUCT_ID: "1001",
            "ERP建档时间": "",
            HISTORY_START_DATE: "",
            IS_HISTORY_TRUNCATED: False,
            "left_censored": True,
            MATCH_CONFIDENCE: MATCH_CONFIDENCE_HIGH,
            STYLE_KEY_SOURCE: "货号",
        }])

        result = build_first_seen_index([], existing_index=existing).set_index(STYLE_KEY)

        self.assertTrue(bool(result.loc["SKU-A", IS_HISTORY_TRUNCATED]))
        self.assertNotIn("left_censored", result.columns)

        later = pd.DataFrame([{
            "宝贝ID": "1001",
            "货号": "sku-a",
            "商品状态": "当前在线",
        }])
        updated = build_first_seen_index(
            [("2026-08-05", later)],
            existing_index=existing,
        ).set_index(STYLE_KEY)
        self.assertTrue(bool(updated.loc["SKU-A", IS_HISTORY_TRUNCATED]))
        self.assertNotIn("left_censored", updated.columns)

    def test_existing_index_only_moves_first_seen_earlier(self):
        existing = pd.DataFrame([{
            STYLE_KEY: "SKU-A",
            FIRST_SEEN_DATE: "2026-08-10",
            FIRST_SEEN_PRODUCT_ID: "1001",
            "ERP建档时间": "",
            HISTORY_START_DATE: "2026-08-01",
            IS_HISTORY_TRUNCATED: False,
            MATCH_CONFIDENCE: MATCH_CONFIDENCE_HIGH,
            STYLE_KEY_SOURCE: "货号",
        }])
        later = pd.DataFrame([{
            "宝贝ID": "1001",
            "货号": "sku-a",
            "商品状态": "当前在线",
        }])
        earlier = pd.DataFrame([{
            "宝贝ID": "1001",
            "货号": "sku-a",
            "商品状态": "当前在线",
        }])

        after_later = build_first_seen_index(
            [("2026-08-12", later)],
            existing_index=existing,
            history_start_date="2026-08-01",
        )
        self.assertEqual(after_later.loc[0, FIRST_SEEN_DATE], "2026-08-10")

        after_earlier = build_first_seen_index(
            [("2026-08-05", earlier)],
            existing_index=after_later,
            history_start_date="2026-08-01",
        )
        self.assertEqual(after_earlier.loc[0, FIRST_SEEN_DATE], "2026-08-05")

    def test_later_sku_promotes_early_product_id_fallback_date(self):
        missing_sku = pd.DataFrame([{
            "宝贝ID": "1001",
            "货号": "",
            "商品状态": "当前在线",
        }])
        with_sku = pd.DataFrame([{
            "宝贝ID": "1001",
            "货号": "sku-a",
            "商品状态": "当前在线",
        }])

        result = build_first_seen_index(
            [("2026-08-02", missing_sku), ("2026-08-05", with_sku)],
            history_start_date="2026-08-01",
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(result.loc[0, STYLE_KEY], "SKU-A")
        self.assertEqual(result.loc[0, FIRST_SEEN_DATE], "2026-08-02")
        self.assertEqual(result.loc[0, MATCH_CONFIDENCE], MATCH_CONFIDENCE_HIGH)


if __name__ == "__main__":
    unittest.main()
