"""Regression contract for the operator-specified execution-table columns."""
from __future__ import annotations

import io
import unittest

import pandas as pd

from core.exporter import EXECUTION_SHEET, export_all_sheets, export_execution_table
from core.schema import EXPORT_COLS, ensure_export_columns, execution_export_frame


EXPECTED_EXPORT_COLS = [
    '统计日期','宝贝ID','款式主图','淘宝类目','货号','链接数','创建时间',
    '销售排名','流量排名','消耗排名','GMV','链接投产',
    '推广动作','商品动作','运营备注',
    '链接投产状态','可售天数状态','推广加购率状态','商品加购率状态','商品支付转化率状态',
    '流量定位','价值定位','组合','商品定位','综合等级','商品周期','v9.3信号',
    'MetoX算法命中','MetoX算法分','MetoX算法原因',
    '预算优先级','风险标签','当前有效投放计划数','推广明细',
    '日销3','日销7','日销15','日均访客3','日均访客7','日均访客15','花费3','花费7','花费15','可售天数','在仓库存','在途','成本价（含税）',
    '预估利润','付费','推广投产','推广加购率','点击率','日均消耗','利润率',
    '人群','人群环比','关键词','关键词环比','全站','全站环比',
    '上周期人群','上周期关键词','上周期全站','上周期付费','直购数量','直购成本',
    '推广实际投产','链接实际投产','购物车价值','件单价','倍率',
    '商品访客数','商品加购件数','商品加购率','支付买家数','支付件数','上周期金额',
    '商品支付转化率','访客平均价值','成功退款金额','退款率',
]

BUDGET_RETIRED_OR_DASHBOARD_ONLY_COLUMNS = [
    '销售占比',
    '花费占比',
    '占比差(pp)',
    '预算错配金额',
    '未来7日建议调整金额',
    '预算角色',
    '预算动作',
    '角色候选',
    '角色状态',
    '角色原因',
    '角色风险',
    '预算角色候选',
    '预算角色状态',
    '预算角色判定依据',
    '预算风险',
    '人工业务标记',
    '人工标记',
    '本月主推',
    '应季状态',
    '季节状态',
    '人工清仓',
    'NOCAP',
    '生效月份',
    '标记备注',
    'manual_marks',
    'manual_state',
    'effective_month',
    '候选',
    '状态',
    '判定依据',
    '风险',
    '推广商品定位',
]


class ExecutionColumnOrderTests(unittest.TestCase):
    def test_export_columns_match_operator_order_without_duplicate_headers(self):
        self.assertEqual(EXPORT_COLS, EXPECTED_EXPORT_COLS)
        self.assertEqual(len(EXPORT_COLS), len(set(EXPORT_COLS)))
        self.assertEqual(EXPORT_COLS.count('推广动作'), 1)
        self.assertNotIn('操作', EXPORT_COLS)
        for column in BUDGET_RETIRED_OR_DASHBOARD_ONLY_COLUMNS:
            self.assertNotIn(column, EXPORT_COLS)

    def test_missing_columns_are_added_without_changing_existing_values(self):
        source = pd.DataFrame([{
            '宝贝ID': '1001',
            'GMV': 123.45,
            '推广动作': '增加预算验证',
            '内部计算列': 999,
        }])

        prepared = ensure_export_columns(source)
        ordered = execution_export_frame(source)

        self.assertEqual(source.columns.tolist(), ['宝贝ID', 'GMV', '推广动作', '内部计算列'])
        self.assertEqual(prepared.loc[0, 'GMV'], 123.45)
        self.assertEqual(prepared.loc[0, '推广动作'], '增加预算验证')
        self.assertEqual(prepared.loc[0, '商品动作'], '')
        self.assertEqual(prepared.loc[0, '内部计算列'], 999)
        self.assertEqual(ordered.columns.tolist(), EXPECTED_EXPORT_COLS)
        self.assertNotIn('内部计算列', ordered.columns)

    def test_both_execution_excel_entries_use_exact_order_and_keep_values(self):
        source = pd.DataFrame([{
            '宝贝ID': '1001',
            'GMV': 123.45,
            '推广动作': '增加预算验证',
        }])

        for exporter in (export_execution_table, export_all_sheets):
            with self.subTest(exporter=exporter.__name__):
                payload = exporter(source)
                excel = pd.ExcelFile(io.BytesIO(payload))
                self.assertEqual(excel.sheet_names[0], EXECUTION_SHEET)
                exported = pd.read_excel(io.BytesIO(payload), sheet_name=EXECUTION_SHEET)

                self.assertEqual(exported.columns.tolist(), EXPECTED_EXPORT_COLS)
                self.assertEqual(str(exported.loc[0, '宝贝ID']), '1001')
                self.assertEqual(exported.loc[0, 'GMV'], 123.45)
                self.assertEqual(exported.loc[0, '推广动作'], '增加预算验证')
                self.assertTrue(pd.isna(exported.loc[0, '商品动作']))


if __name__ == '__main__':
    unittest.main()
