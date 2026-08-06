"""Regression tests for the multi-sheet Excel export."""
from __future__ import annotations

import io
import unittest

import pandas as pd

from core.exporter import export_all_sheets
from core.plan_metrics import summarize_plan_records


class ExportAllSheetsTests(unittest.TestCase):
    def test_ai_task_view_sheet_is_exported_with_image_url(self):
        image_url = "https://img.example/style-1001.jpg"
        plan = summarize_plan_records([{
            '计划ID': 'P1001', '当前花费': 100, '归因成交': 200,
            '加购': 10, '点击': 50, '所属渠道': '关键词',
        }], period_days=1, actual_days=1)
        source = pd.DataFrame([{
            '宝贝ID': '1001',
            '款式主图': image_url,
            '货号': 'SKU-1001',
            '商品名称': '测试商品',
            '淘宝类目': '连衣裙',
            '综合等级': 'A',
            '商品定位': '冲刺款',
            '商品周期': '成长期',
            'MetoX算法命中': '保护持有',
            'MetoX算法分': 15,
            'GMV': 1000,
            '付费': 100,
            **plan,
        }])

        workbook = export_all_sheets(source)
        excel = pd.ExcelFile(io.BytesIO(workbook))

        self.assertIn('AI任务视图', excel.sheet_names)
        tasks = pd.read_excel(io.BytesIO(workbook), sheet_name='AI任务视图')
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks.loc[0, '款式主图'], image_url)
        self.assertIn('AI任务类型', tasks.columns)
        self.assertEqual(tasks.loc[0, '优先级'], 'P3')
        self.assertEqual(
            tasks.columns.get_loc('优先级'),
            tasks.columns.get_loc('MetoX算法命中') + 1,
        )
        self.assertNotIn('商品名称', tasks.columns)
        self.assertTrue(str(tasks.loc[0, 'AI任务类型']).strip())

    def test_no_paid_no_plan_completeness_is_exported_as_not_applicable(self):
        source = pd.DataFrame([{
            '宝贝ID': '1002',
            '款式主图': 'https://img.example/style-1002.jpg',
            '货号': 'SKU-1002',
            '淘宝类目': '连衣裙',
            '综合等级': 'A',
            '商品定位': '冲刺款',
            '商品周期': '成长期',
            'MetoX算法命中': '保护持有',
            'MetoX算法分': 15,
            'GMV': 1000,
            '付费': 0,
            '推广计划总数': 0,
            '推广计划展示数': 0,
            '计划覆盖率': 1.0,
            '计划数据完整度': 1.0,
            '推广明细': '',
            '推广计划全量明细': '',
        }])

        workbook = export_all_sheets(source)
        tasks = pd.read_excel(io.BytesIO(workbook), sheet_name='AI任务视图')

        self.assertEqual(tasks.loc[0, '计划覆盖率'], '无计划')
        self.assertEqual(tasks.loc[0, '计划数据完整度'], '无计划')


if __name__ == '__main__':
    unittest.main()
