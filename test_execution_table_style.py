"""Regression tests for execution-table top-product row highlighting."""
import io
import unittest

import pandas as pd
from openpyxl import load_workbook

from core.exporter import (
    EXECUTION_SHEET,
    STATUS_HIGH_BACKGROUND,
    STATUS_LOW_BACKGROUND,
    TOP_PRODUCT_BACKGROUND,
    TOP_PRODUCT_TEXT_COLOR,
    export_all_sheets,
    export_execution_table,
    style_execution_table,
    top_category_product_mask,
)


class ExecutionTableStyleTests(unittest.TestCase):
    STATUS_COLUMNS = (
        '链接投产状态',
        '推广加购率状态',
        '商品加购率状态',
        '商品支付转化率状态',
        '可售天数状态',
    )

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
                    '推广加购率状态': '高' if product_number % 2 == 0 else '低',
                    '商品加购率状态': '低' if product_number % 2 == 0 else '高',
                    '商品支付转化率状态': '高' if product_number % 2 == 0 else '低',
                    '可售天数状态': '紧急' if product_number % 2 == 0 else '需关注',
                })
        rows.append({
            '宝贝ID': 'zero',
            '淘宝类目': 'C00',
            'GMV': 0,
            '付费': 0,
            '链接投产状态': '高',
            '推广加购率状态': '高',
            '商品加购率状态': '低',
            '商品支付转化率状态': '低',
            '可售天数状态': '紧急',
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

        for column_number, column in enumerate(source.columns):
            if column in self.STATUS_COLUMNS:
                continue
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

    def test_top_product_status_cells_override_row_style_with_readable_colors(self):
        source = self._ranking_source()

        computed = style_execution_table(source)._compute()
        columns = {column: position for position, column in enumerate(source.columns)}

        expected = {
            '链接投产状态': STATUS_HIGH_BACKGROUND,
            '推广加购率状态': STATUS_HIGH_BACKGROUND,
            '商品加购率状态': STATUS_LOW_BACKGROUND,
            '商品支付转化率状态': STATUS_HIGH_BACKGROUND,
            '可售天数状态': STATUS_LOW_BACKGROUND,
        }
        for column, background in expected.items():
            cell_style = computed.ctx[(0, columns[column])]
            self.assertIn(('background-color', background), cell_style)
            self.assertIn(('color', TOP_PRODUCT_TEXT_COLOR), cell_style)

        opposite_expected = {
            '链接投产状态': STATUS_LOW_BACKGROUND,
            '推广加购率状态': STATUS_LOW_BACKGROUND,
            '商品加购率状态': STATUS_HIGH_BACKGROUND,
            '商品支付转化率状态': STATUS_LOW_BACKGROUND,
            '可售天数状态': STATUS_HIGH_BACKGROUND,
        }
        for column, background in opposite_expected.items():
            cell_style = computed.ctx[(1, columns[column])]
            self.assertIn(('background-color', background), cell_style)
            self.assertIn(('color', TOP_PRODUCT_TEXT_COLOR), cell_style)

    def test_filtered_display_keeps_full_table_ranking(self):
        ranking_source = self._ranking_source()
        filtered = ranking_source.loc[[49, 50], ['链接投产状态']]

        computed = style_execution_table(filtered, ranking_df=ranking_source)._compute()

        self.assertIn(
            ('background-color', STATUS_LOW_BACKGROUND),
            computed.ctx[(0, 0)],
        )
        self.assertIn(
            ('color', TOP_PRODUCT_TEXT_COLOR),
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

    def test_unmapped_status_values_keep_top_product_row_style(self):
        source = pd.DataFrame([{
            '宝贝ID': '1001',
            '淘宝类目': 'C00',
            'GMV': 100,
            '付费': 0,
            '链接投产状态': '样本不足',
            '推广加购率状态': '中',
            '商品加购率状态': '正常',
            '商品支付转化率状态': '正常',
            '可售天数状态': '充足',
        }])

        computed = style_execution_table(source)._compute()

        for column in self.STATUS_COLUMNS:
            column_number = source.columns.get_loc(column)
            self.assertIn(
                ('background-color', TOP_PRODUCT_BACKGROUND),
                computed.ctx[(0, column_number)],
            )
            self.assertIn(
                ('color', TOP_PRODUCT_TEXT_COLOR),
                computed.ctx[(0, column_number)],
            )

    def test_both_excel_exports_use_the_same_top_product_row_and_status_style(self):
        source = self._ranking_source()

        for export in (export_execution_table, export_all_sheets):
            with self.subTest(export=export.__name__):
                workbook = load_workbook(io.BytesIO(export(source)))
                worksheet = workbook[EXECUTION_SHEET]
                columns = {cell.value: cell.column for cell in worksheet[1]}

                for excel_row in (2, 51):
                    for column in ('宝贝ID', '淘宝类目', 'GMV'):
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

                expected_row_2_statuses = {
                    '链接投产状态': STATUS_HIGH_BACKGROUND,
                    '推广加购率状态': STATUS_HIGH_BACKGROUND,
                    '商品加购率状态': STATUS_LOW_BACKGROUND,
                    '商品支付转化率状态': STATUS_HIGH_BACKGROUND,
                    '可售天数状态': STATUS_LOW_BACKGROUND,
                }
                expected_row_3_statuses = {
                    '链接投产状态': STATUS_LOW_BACKGROUND,
                    '推广加购率状态': STATUS_LOW_BACKGROUND,
                    '商品加购率状态': STATUS_HIGH_BACKGROUND,
                    '商品支付转化率状态': STATUS_LOW_BACKGROUND,
                    '可售天数状态': STATUS_HIGH_BACKGROUND,
                }
                for excel_row, expected_statuses in (
                        (2, expected_row_2_statuses),
                        (3, expected_row_3_statuses)):
                    for column, background in expected_statuses.items():
                        cell = worksheet.cell(row=excel_row, column=columns[column])
                        self.assertEqual(cell.fill.fill_type, 'solid')
                        self.assertEqual(
                            cell.fill.fgColor.rgb[-6:],
                            background.lstrip('#'),
                        )
                        self.assertEqual(
                            cell.font.color.rgb[-6:],
                            TOP_PRODUCT_TEXT_COLOR.lstrip('#'),
                        )

                for excel_row in (52, 15 * 51 + 2, len(source) + 1):
                    for column in ('宝贝ID', *self.STATUS_COLUMNS):
                        cell = worksheet.cell(row=excel_row, column=columns[column])
                        self.assertIsNone(cell.fill.fill_type)

    def test_excel_exports_keep_full_table_ranking_for_filtered_rows(self):
        ranking_source = self._ranking_source()
        filtered = ranking_source.loc[[49, 50]]

        for export in (export_execution_table, export_all_sheets):
            with self.subTest(export=export.__name__):
                workbook = load_workbook(
                    io.BytesIO(export(filtered, ranking_df=ranking_source))
                )
                worksheet = workbook[EXECUTION_SHEET]
                columns = {cell.value: cell.column for cell in worksheet[1]}

                selected_cell = worksheet.cell(row=2, column=columns['链接投产状态'])
                self.assertEqual(selected_cell.fill.fill_type, 'solid')
                self.assertEqual(
                    selected_cell.fill.fgColor.rgb[-6:],
                    STATUS_LOW_BACKGROUND.lstrip('#'),
                )
                self.assertEqual(
                    selected_cell.font.color.rgb[-6:],
                    TOP_PRODUCT_TEXT_COLOR.lstrip('#'),
                )

                excluded_cell = worksheet.cell(row=3, column=columns['链接投产状态'])
                self.assertIsNone(excluded_cell.fill.fill_type)


if __name__ == '__main__':
    unittest.main()
