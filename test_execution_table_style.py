"""Regression tests for execution-table status background colors."""
import io
import unittest

import pandas as pd
from openpyxl import load_workbook

from core.exporter import (
    EXECUTION_SHEET,
    LIGHT_BLUE_BACKGROUND,
    LIGHT_PINK_BACKGROUND,
    export_all_sheets,
    export_execution_table,
    style_execution_table,
)


class ExecutionTableStyleTests(unittest.TestCase):
    def test_large_table_raises_pandas_styler_capacity_before_rendering(self):
        original_limit = pd.get_option('styler.render.max_elements')
        source = pd.DataFrame({
            f'字段{column}': range(100)
            for column in range(10)
        })

        try:
            pd.set_option('styler.render.max_elements', 100)
            styled = style_execution_table(source)

            self.assertIs(styled.data, source)
            self.assertGreaterEqual(
                pd.get_option('styler.render.max_elements'),
                source.size,
            )
            styled._compute()
        finally:
            pd.set_option('styler.render.max_elements', original_limit)

    def test_requested_statuses_receive_their_background_colors(self):
        source = pd.DataFrame({
            '链接投产状态': ['高', '低', '正常'],
            '推广加购率状态': ['低', '高', '样本不足'],
            '商品加购率状态': ['高', '低', '正常'],
            '商品支付转化率状态': ['低', '高', '样本不足'],
            '可售天数状态': ['紧急', '需关注', '充足'],
        })

        computed = style_execution_table(source)._compute()
        expected = {
            (0, 0): LIGHT_BLUE_BACKGROUND,
            (1, 0): LIGHT_PINK_BACKGROUND,
            (0, 1): LIGHT_PINK_BACKGROUND,
            (1, 1): LIGHT_BLUE_BACKGROUND,
            (0, 2): LIGHT_BLUE_BACKGROUND,
            (1, 2): LIGHT_PINK_BACKGROUND,
            (0, 3): LIGHT_PINK_BACKGROUND,
            (1, 3): LIGHT_BLUE_BACKGROUND,
            (0, 4): LIGHT_PINK_BACKGROUND,
            (1, 4): LIGHT_BLUE_BACKGROUND,
        }

        for position, background_color in expected.items():
            self.assertIn(('background-color', background_color), computed.ctx[position])

        for position in ((2, 0), (2, 1), (2, 2), (2, 3), (2, 4)):
            self.assertEqual(computed.ctx[position], [])

    def test_missing_status_columns_are_ignored(self):
        source = pd.DataFrame({'宝贝ID': ['1001'], '链接投产状态': ['高']})

        computed = style_execution_table(source)._compute()

        self.assertEqual(computed.data.equals(source), True)
        self.assertIn(('background-color', '#DDEBF7'), computed.ctx[(0, 1)])
        self.assertEqual(computed.ctx[(0, 0)], [])

    def test_both_excel_exports_use_the_same_status_background_colors(self):
        source = pd.DataFrame({
            '宝贝ID': ['1001', '1002', '1003'],
            '链接投产状态': ['高', '低', '正常'],
            '推广加购率状态': ['低', '高', '样本不足'],
            '商品加购率状态': ['高', '低', '正常'],
            '商品支付转化率状态': ['低', '高', '样本不足'],
            '可售天数状态': ['紧急', '需关注', '充足'],
        })
        expected = {
            ('链接投产状态', 2): LIGHT_BLUE_BACKGROUND,
            ('链接投产状态', 3): LIGHT_PINK_BACKGROUND,
            ('推广加购率状态', 2): LIGHT_PINK_BACKGROUND,
            ('推广加购率状态', 3): LIGHT_BLUE_BACKGROUND,
            ('商品加购率状态', 2): LIGHT_BLUE_BACKGROUND,
            ('商品加购率状态', 3): LIGHT_PINK_BACKGROUND,
            ('商品支付转化率状态', 2): LIGHT_PINK_BACKGROUND,
            ('商品支付转化率状态', 3): LIGHT_BLUE_BACKGROUND,
            ('可售天数状态', 2): LIGHT_PINK_BACKGROUND,
            ('可售天数状态', 3): LIGHT_BLUE_BACKGROUND,
        }

        for export in (export_execution_table, export_all_sheets):
            with self.subTest(export=export.__name__):
                workbook = load_workbook(io.BytesIO(export(source)))
                worksheet = workbook[EXECUTION_SHEET]
                columns = {
                    cell.value: cell.column
                    for cell in worksheet[1]
                }
                for (column, row), color in expected.items():
                    cell = worksheet.cell(row=row, column=columns[column])
                    self.assertEqual(cell.fill.fill_type, 'solid')
                    self.assertEqual(cell.fill.fgColor.rgb[-6:], color.lstrip('#'))

                for column in (
                    '链接投产状态',
                    '推广加购率状态',
                    '商品加购率状态',
                    '商品支付转化率状态',
                    '可售天数状态',
                ):
                    self.assertIsNone(
                        worksheet.cell(row=4, column=columns[column]).fill.fill_type
                    )


if __name__ == '__main__':
    unittest.main()
