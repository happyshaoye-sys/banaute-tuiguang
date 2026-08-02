"""Inventory action identity-column regression tests."""
from __future__ import annotations

import io
import unittest

import pandas as pd
from openpyxl import load_workbook

from core.analysis import inventory_actions
from core.exporter import export_all_sheets


class InventoryActionColumnTests(unittest.TestCase):
    def setUp(self):
        self.frame = pd.DataFrame([
            {
                "宝贝ID": "10001",
                "货号": "B0626A10001",
                "商品名称": "高库存商品",
                "在仓库存": 100,
                "在途": 0,
                "商品访客数": 5,
                "GMV": 0,
                "付费": 0,
                "综合等级": "C",
                "风险标签": "",
            },
            {
                "宝贝ID": "10002",
                "货号": "B0626A10002",
                "商品名称": "缺货在投商品",
                "在仓库存": 0,
                "在途": 0,
                "商品访客数": 30,
                "GMV": 200,
                "付费": 20,
                "综合等级": "B",
                "风险标签": "",
            },
        ])

    def test_page_tables_place_style_code_after_product_id(self):
        result = inventory_actions(self.frame)

        for key in ("hi_stock", "no_stock"):
            with self.subTest(table=key):
                self.assertTrue(result[key])
                self.assertEqual(list(result[key][0])[:3], ["宝贝ID", "货号", "商品名称"])
                self.assertTrue(result[key][0]["货号"].startswith("B0626"))

    def test_excel_inventory_sheet_uses_same_identity_order(self):
        workbook = load_workbook(io.BytesIO(export_all_sheets(self.frame)), read_only=True)
        headers = [cell.value for cell in next(workbook["库存动作"].iter_rows(max_row=1))]

        self.assertEqual(headers[:3], ["宝贝ID", "货号", "商品名称"])


if __name__ == "__main__":
    unittest.main()
