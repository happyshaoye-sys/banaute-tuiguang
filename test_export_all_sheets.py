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
        self.assertNotIn('商品名称', tasks.columns)
        self.assertTrue(str(tasks.loc[0, 'AI任务类型']).strip())


if __name__ == '__main__':
    unittest.main()
