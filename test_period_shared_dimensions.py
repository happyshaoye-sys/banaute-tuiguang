import unittest

import pandas as pd

from core.merge import build_shared_dimensions, merge_sources
from core.schema import SOURCE_PRODUCT_INFO, SOURCE_PRODUCT_ROI


class SharedPeriodDimensionTests(unittest.TestCase):
    def test_product_missing_on_period_end_keeps_sku_from_shared_mapping(self):
        target = "1000046504413"
        product_info = pd.DataFrame({
            "宝贝ID": [target],
            "货号": ["B0625B5QXL2"],
            "款式主图": ["https://example.invalid/item.jpg"],
            "创建时间": ["2025-12-01 16:11:30"],
            "成本价（含税）": [277.0],
        })
        dimensions = build_shared_dimensions({
            SOURCE_PRODUCT_INFO: [("mapping.csv", product_info)],
        })

        earlier_day = pd.DataFrame({
            "宝贝ID": [target],
            "GMV": [100.0],
            "商品访客数": [10],
        })
        merged, _ = merge_sources({
            SOURCE_PRODUCT_ROI: [("2026-07-18.xlsx", earlier_day)],
        }, dimensions=dimensions)

        row = merged.loc[merged["宝贝ID"] == target].iloc[0]
        self.assertEqual(row["货号"], "B0625B5QXL2")
        self.assertEqual(row["成本价（含税）"], 277.0)
        self.assertEqual(row["款式主图"], "https://example.invalid/item.jpg")


if __name__ == "__main__":
    unittest.main()
