"""Regression tests for execution-table top-product row highlighting."""
import io
import unittest

import pandas as pd
from openpyxl import load_workbook

from core.exporter import (
    EXECUTION_SHEET,
    TOP_PRODUCT_BACKGROUND,
    TOP_PRODUCT_TEXT_COLOR,
    export_all_sheets,
    export_execution_table,
    style_execution_table,
    top_category_product_mask,
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

    @staticmethod
    def _ranking_source():
        rows = []
        for category_number in range(16):
            category = f'C{category_number:02d}'
            category_base = (16 - category_number) * 10000
            for product_number in range(51):
                rows.append({
                    '宝贝ID': f'{category}-{product_number:02d}',
                    '淘宝类目': category,
                    'GMV': category_base + 51 - product_number,
                    '付费': 0,
                    '链接投产状态': '高' if product_number % 2 == 0 else '低',
                })
        rows.append({
            '宝贝ID': 'zero',
            '淘宝类目': 'C00',
            'GMV': 0,
            '付费': 0,
            '链接投产状态': '高',
        })
        return pd.DataFrame(rows)

    def test_only_top_15_categories_top_50_positive_products_are_selected(self):
        source = self._ranking_source()

        mask = top_category_product_mask(source)

        self.assertEqual(int(mask.sum()), 15 * 50)
        self.assertTrue(mask.iloc[0])
        self.assertTrue(mask.iloc[49])
        self.assertFalse(mask.iloc[50])
        self.assertFalse(mask.iloc[15 * 51])
        self.assertFalse(mask.iloc[-1])

    def test_selected_products_receive_readable_full_row_style(self):
        source = self._ranking_source()

        computed = style_execution_table(source)._compute()

        for column_number in range(len(source.columns)):
            self.assertIn(
                ('background-color', TOP_PRODUCT_BACKGROUND),
                computed.ctx[(0, column_number)],
            )
            self.assertIn(
                ('color', TOP_PRODUCT_TEXT_COLOR),
                computed.ctx[(0, column_number)],
            )
        for row_number in (50, 15 * 51, len(source) - 1):
            for column_number in range(len(source.columns)):
                self.assertEqual(computed.ctx[(row_number, column_number)], [])

    def test_filtered_display_keeps_full_table_ranking(self):
        ranking_source = self._ranking_source()
        filtered = ranking_source.loc[[49, 50], ['链接投产状态']]

        computed = style_execution_table(filtered, ranking_df=ranking_source)._compute()

        self.assertIn(
            ('background-color', TOP_PRODUCT_BACKGROUND),
            computed.ctx[(0, 0)],
        )
        self.assertEqual(computed.ctx[(1, 0)], [])

    def test_status_values_are_not_colored_without_top_product_rank(self):
        source = pd.DataFrame({
            '宝贝ID': ['1001', '1002'],
            '链接投产状态': ['高', '低'],
            '可售天数状态': ['紧急', '需关注'],
        })

        computed = style_execution_table(source)._compute()

        self.assertEqual(dict(computed.ctx), {})

    def test_both_excel_exports_use_the_same_top_product_row_style(self):
        source = self._ranking_source()

        for export in (export_execution_table, export_all_sheets):
            with self.subTest(export=export.__name__):
                workbook = load_workbook(io.BytesIO(export(source)))
                worksheet = workbook[EXECUTION_SHEET]
                columns = {cell.value: cell.column for cell in worksheet[1]}

                for excel_row in (2, 51):
                    for column in ('宝贝ID', '淘宝类目', 'GMV', '链接投产状态'):
                        cell = worksheet.cell(row=excel_row, column=columns[column])
                        self.assertEqual(cell.fill.fill_type, 'solid')
                        self.assertEqual(
                            cell.fill.fgColor.rgb[-6:],
                            TOP_PRODUCT_BACKGROUND.lstrip('#'),
                        )
                        self.assertEqual(
                            cell.font.color.rgb[-6:],
                            TOP_PRODUCT_TEXT_COLOR.lstrip('#'),
                        )

                for excel_row in (52, 15 * 51 + 2, len(source) + 1):
                    for column in ('宝贝ID', '链接投产状态'):
                        cell = worksheet.cell(row=excel_row, column=columns[column])
                        self.assertIsNone(cell.fill.fill_type)


if __name__ == '__main__':
    unittest.main()
