"""Regression tests for the multi-sheet Excel export."""
from __future__ import annotations

import io
import unittest

import pandas as pd

from core.exporter import export_all_sheets, export_execution_table
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
            '商品访客数': 40,
            '关键词': 60,
            '人群': 25,
            '全站': 15,
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
            '商品访客数': 40,
            '关键词': 0,
            '人群': 0,
            '全站': 0,
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

    def test_category_investment_and_roi_sheet_is_in_both_downloads(self):
        plan = summarize_plan_records([{
            '计划ID': 'P1003', '当前花费': 100, '归因成交': 200,
            '加购': 10, '点击': 50, '所属渠道': '关键词',
        }], period_days=7, actual_days=7)
        source = pd.DataFrame([{
            '宝贝ID': '1003',
            '淘宝类目': '连衣裙',
            'GMV': 1000.0,
            '付费': 100.0,
            '商品访客数': 40,
            '关键词': 60.0,
            '人群': 25.0,
            '全站': 15.0,
            **plan,
        }])
        category_context = {
            'h7_cur_gmv': {'连衣裙': 1000.0},
            'h7_prev_gmv': {'连衣裙': 800.0},
            'h7_prev_spend': {'连衣裙': 90.0},
            'h7_prev_keyword': {'连衣裙': 50.0},
            'h7_prev_crowd': {'连衣裙': 30.0},
            'h7_prev_omni': {'连衣裙': 10.0},
        }
        expected_columns = [
            '品类', 'GMV', 'GMV占比', '7天GMV', '7日GMV环比',
            '付费', '付费占比', '上周期付费', '关键词', '人群', '全站',
            '上周期关键词', '上周期人群', '上周期全站', 'ROI',
        ]

        for workbook in (
            export_execution_table(source, cat_ctx=category_context),
            export_all_sheets(source, cat_ctx=category_context),
        ):
            with self.subTest(sheet_names=pd.ExcelFile(io.BytesIO(workbook)).sheet_names):
                excel = pd.ExcelFile(io.BytesIO(workbook))
                self.assertIn('品类投放占比与ROI', excel.sheet_names)
                category = pd.read_excel(
                    io.BytesIO(workbook), sheet_name='品类投放占比与ROI'
                )
                self.assertEqual(category.columns.tolist(), expected_columns)
                self.assertEqual(category.loc[0, '品类'], '连衣裙')
                self.assertEqual(category.loc[0, '7天GMV'], 800.0)
                self.assertEqual(category.loc[0, '7日GMV环比'], '25.0%')
                self.assertEqual(category.loc[0, '上周期付费'], 90.0)
                self.assertEqual(category.loc[0, '上周期关键词'], 50.0)
                self.assertEqual(category.loc[0, '上周期人群'], 30.0)
                self.assertEqual(category.loc[0, '上周期全站'], 10.0)
                self.assertEqual(category.loc[0, 'ROI'], 10.0)

    def test_metric_status_rule_document_is_in_both_downloads(self):
        source = pd.DataFrame([{
            '宝贝ID': '1004',
            '淘宝类目': '连衣裙',
            'GMV': 1000.0,
            '付费': 0.0,
            '商品访客数': 200,
            '商品加购件数': 20,
            '支付买家数': 10,
            '关键词': 60.0,
            '人群': 25.0,
            '全站': 15.0,
        }])
        expected_fields = [
            '链接投产状态',
            '商品支付转化率状态',
            '商品加购率状态',
            '可售天数状态',
            '推广加购率状态',
        ]
        expected_columns = [
            '状态字段', '指标口径', '基准或参考线', '判定标准', '特殊情况', '运营解释',
        ]

        for workbook in (export_execution_table(source), export_all_sheets(source)):
            with self.subTest(sheet_names=pd.ExcelFile(io.BytesIO(workbook)).sheet_names):
                excel = pd.ExcelFile(io.BytesIO(workbook))
                self.assertIn('指标状态规则', excel.sheet_names)
                rules = pd.read_excel(io.BytesIO(workbook), sheet_name='指标状态规则')
                self.assertEqual(rules.columns.tolist(), expected_columns)
                self.assertEqual(rules['状态字段'].tolist(), expected_fields)
                self.assertIn('GMV÷付费', rules.loc[0, '指标口径'])
                self.assertIn('max(盈亏平衡线×1.2，同层P75)', rules.loc[0, '判定标准'])
                self.assertIn('不使用链接实际投产或退款率过滤', rules.loc[0, '特殊情况'])
                self.assertIn('支付买家数÷商品访客数', rules.loc[1, '指标口径'])
                self.assertIn('商品加购件数÷商品访客数', rules.loc[2, '指标口径'])
                self.assertIn('14≤可售天数<28：需关注', rules.loc[3, '判定标准'])
                self.assertIn('推广加购件数÷推广访客数', rules.loc[4, '指标口径'])
                self.assertIn('当前访客数<100', rules.loc[4, '特殊情况'])


if __name__ == '__main__':
    unittest.main()
