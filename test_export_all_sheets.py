"""Regression tests for the multi-sheet Excel export."""
from __future__ import annotations

import io
import unittest

import pandas as pd

from core.budget_decision import (
    PRODUCT_COLUMNS,
    build_budget_decision_context,
    is_removed_budget_column,
)
from core.exporter import (
    BUDGET_DECISION_PRODUCT_COLUMNS,
    BUDGET_DECISION_PLAN_COLUMNS,
    BUDGET_DECISION_PLAN_SHEET,
    BUDGET_DECISION_PRODUCT_SHEET,
    BUDGET_DECISION_REQUIRED_PRODUCT_COLUMNS,
    BUDGET_DECISION_TIER_COLUMNS,
    BUDGET_DECISION_TIER_SHEET,
    EXECUTION_SHEET,
    budget_decision_product_columns,
    export_all_sheets,
    export_execution_table,
)
from core.plan_metrics import (
    FULL_PLAN_DETAIL_COLUMN,
    summarize_plan_records,
    serialize_full_plan_details,
)


BUDGET_DECISION_FORBIDDEN_PRODUCT_COLUMNS = [
    '预算角色',
    '预算动作',
    '角色候选',
    '角色状态',
    '角色原因',
    '角色风险',
    '预算角色候选',
    '预算角色状态',
    '预算角色判定依据',
    '人工业务标记',
    '人工标记',
    '本月主推',
    '应季状态',
    '季节状态',
    '人工清仓',
    'manual_marks',
    'manual_state',
    '推广商品定位',
    '销售梯队',
    '候选',
    '状态',
    '判定依据',
    '风险',
]


def _plan_payload(records, period_days=7, actual_days=7):
    payload = summarize_plan_records(
        records,
        period_days=period_days,
        actual_days=actual_days,
    )
    payload[FULL_PLAN_DETAIL_COLUMN] = serialize_full_plan_details(records)
    return payload


def _budget_decision_source():
    high_spend_plan = [{
        '计划ID': 'P1001',
        '计划名称': '关键词冲刺',
        '计划类型': '关键词标准计划',
        '所属渠道': '关键词',
        '当前花费': 800,
        '归因成交': 1200,
        '加购': 20,
        '点击': 100,
        '当前状态': '投放中',
    }]
    low_spend_plan = [{
        '计划ID': 'P2001',
        '计划名称': '全站补量',
        '计划类型': '全站推广',
        '所属渠道': '全站',
        '当前花费': 200,
        '归因成交': 800,
        '加购': 10,
        '点击': 80,
        '当前状态': '投放中',
    }]
    return pd.DataFrame([
        {
            '宝贝ID': '1001',
            '款式主图': 'https://img.example/style-1001.jpg',
            '货号': 'SKU-1001',
            '淘宝类目': '连衣裙',
            '商品定位': '冲刺款',
            '综合等级': 'A',
            '商品周期': '成长期',
            '推广动作': '增加预算验证',
            'GMV': 1000.0,
            '付费': 800.0,
            '商品访客数': 80,
            '关键词': 800.0,
            '人群': 0.0,
            '全站': 0.0,
            **_plan_payload(high_spend_plan),
        },
        {
            '宝贝ID': '2001',
            '款式主图': 'https://img.example/style-2001.jpg',
            '货号': 'SKU-2001',
            '淘宝类目': '半身裙',
            '商品定位': '承接款',
            '综合等级': 'B',
            '商品周期': '成熟期',
            '推广动作': '维持预算',
            'GMV': 1000.0,
            '付费': 200.0,
            '商品访客数': 120,
            '关键词': 0.0,
            '人群': 0.0,
            '全站': 200.0,
            **_plan_payload(low_spend_plan),
        },
    ])


def _context_frame(context, key):
    value = context.get(key)
    if isinstance(value, pd.DataFrame):
        return value.copy()
    return pd.DataFrame(value)


def _by_clean_id(frame, column):
    work = frame.copy()
    work[column] = (
        work[column]
        .astype(str)
        .str.replace(r'\.0$', '', regex=True)
        .str.strip()
    )
    return work.set_index(column, drop=False)


def _assert_excel_value(testcase, actual, expected):
    if pd.isna(expected):
        testcase.assertTrue(pd.isna(actual))
        return
    try:
        expected_num = float(expected)
        actual_num = float(actual)
    except (TypeError, ValueError):
        testcase.assertEqual(str(actual), str(expected))
    else:
        testcase.assertAlmostEqual(actual_num, expected_num, places=6)


def _assert_no_removed_budget_columns(testcase, frame):
    for column in frame.columns:
        testcase.assertFalse(is_removed_budget_column(column), column)
    for column in BUDGET_DECISION_FORBIDDEN_PRODUCT_COLUMNS:
        testcase.assertNotIn(column, frame.columns)


class ExportAllSheetsTests(unittest.TestCase):
    def test_budget_product_export_reuses_canonical_product_columns(self):
        expected = []
        for column in PRODUCT_COLUMNS:
            if column == '销售梯队':
                column = '排名档'
            if is_removed_budget_column(column):
                continue
            if column not in expected:
                expected.append(column)
        if '排名档' in expected:
            expected.remove('排名档')
        expected[expected.index('淘宝类目') + 1:expected.index('淘宝类目') + 1] = [
            '排名档',
        ]

        self.assertEqual(budget_decision_product_columns(), expected)
        self.assertEqual(BUDGET_DECISION_PRODUCT_COLUMNS, expected)
        for column in BUDGET_DECISION_REQUIRED_PRODUCT_COLUMNS:
            self.assertIn(column, expected)
        for column in BUDGET_DECISION_FORBIDDEN_PRODUCT_COLUMNS:
            self.assertNotIn(column, expected)
        for column in expected:
            self.assertFalse(is_removed_budget_column(column), column)

    def test_budget_decision_sheets_are_exported_from_context(self):
        source = _budget_decision_source()
        context = build_budget_decision_context(source)

        workbook = export_all_sheets(source, budget_decision_context=context)
        excel = pd.ExcelFile(io.BytesIO(workbook))

        self.assertIn(BUDGET_DECISION_PRODUCT_SHEET, excel.sheet_names)
        self.assertIn(BUDGET_DECISION_TIER_SHEET, excel.sheet_names)
        self.assertIn(BUDGET_DECISION_PLAN_SHEET, excel.sheet_names)
        self.assertNotIn('AI任务视图', excel.sheet_names)

        products = pd.read_excel(
            io.BytesIO(workbook), sheet_name=BUDGET_DECISION_PRODUCT_SHEET
        )
        expected_products = _context_frame(context, 'products')
        exported_products = _by_clean_id(products, '宝贝ID')
        context_products = _by_clean_id(expected_products, '宝贝ID')

        for column in BUDGET_DECISION_REQUIRED_PRODUCT_COLUMNS:
            self.assertIn(column, products.columns)
        _assert_no_removed_budget_columns(self, products)

        for exported_column, context_column in (
            ('销售占比', '销售占比'),
            ('花费占比', '花费占比'),
            ('占比差(pp)', '占比差(pp)'),
            ('预算错配金额', '预算错配金额'),
            ('未来7日建议调整金额', '未来7日建议调整金额'),
            ('预算匹配状态', '预算匹配状态'),
            ('动作依据', '动作依据'),
        ):
            self.assertIn(exported_column, products.columns)
            self.assertIn(context_column, expected_products.columns)
            _assert_excel_value(
                self,
                exported_products.loc['1001', exported_column],
                context_products.loc['1001', context_column],
            )

        self.assertEqual(exported_products.loc['1001', '排名档'], 'Top1-5')
        self.assertNotIn('销售梯队', products.columns)
        self.assertEqual(
            products.columns.get_loc('排名档'),
            products.columns.get_loc('淘宝类目') + 1,
        )

        tiers = pd.read_excel(io.BytesIO(workbook), sheet_name=BUDGET_DECISION_TIER_SHEET)
        _assert_no_removed_budget_columns(self, tiers)
        self.assertEqual(
            tiers.columns.tolist()[:len(BUDGET_DECISION_TIER_COLUMNS)],
            BUDGET_DECISION_TIER_COLUMNS,
        )
        self.assertEqual(
            BUDGET_DECISION_TIER_COLUMNS.index('ROI'),
            BUDGET_DECISION_TIER_COLUMNS.index('预算错配金额') + 1,
        )

        plans = pd.read_excel(io.BytesIO(workbook), sheet_name=BUDGET_DECISION_PLAN_SHEET)
        _assert_no_removed_budget_columns(self, plans)
        self.assertEqual(BUDGET_DECISION_PLAN_COLUMNS[0], '序号')
        self.assertEqual(
            plans.columns.tolist()[:len(BUDGET_DECISION_PLAN_COLUMNS)],
            BUDGET_DECISION_PLAN_COLUMNS,
        )
        expected_plans = _context_frame(context, 'plans')
        exported_plans = _by_clean_id(plans, '计划ID')
        context_plans = _by_clean_id(expected_plans, '计划ID')
        self.assertEqual(exported_plans.loc['P1001', '计划ID'], 'P1001')
        expected_plan_type_column = (
            '计划类型' if '计划类型' in expected_plans.columns else '计划商品类型'
        )
        self.assertEqual(
            exported_plans.loc['P1001', '计划类型'],
            context_plans.loc['P1001', expected_plan_type_column],
        )

    def test_budget_decision_export_preserves_passed_context_and_drops_legacy_aliases(self):
        source = pd.DataFrame([{
            '宝贝ID': 'CTX1',
            '淘宝类目': '连衣裙',
            'GMV': 0.0,
            '付费': 0.0,
            '商品访客数': 0,
        }])
        context = {
            'products': pd.DataFrame([{
                '宝贝ID': 'CTX1',
                '推广商品定位': '旧字段不应导出',
                '销售梯队': 'S1',
                '预算角色': 'ZT',
                '预算动作': '保持',
                '角色候选': 'ZT',
                '角色状态': '人工确认冻结',
                '角色原因': '运营手工标记',
                '候选': '是',
                '预算匹配状态': '冻结透传状态',
                '状态': '人工确认冻结',
                '动作依据': '冻结透传依据',
                '判定依据': '运营手工标记',
                '风险': '极高退款',
                '人工业务标记': '本月主推',
                '人工标记': '人工清仓',
                'manual_marks': 'manual-a',
                '近7日日均GMV': 123.45,
                '上一7日日均GMV': 100.0,
                'GMV趋势率': 0.2345,
                'TH_MAIN': 120.0,
                'TH_MID': 80.0,
                '阈值版本': 'store-freeze-v1',
            }]),
            'tiers': pd.DataFrame([{
                '销售梯队': 'S1',
                '商品数': 1,
                '错配金额': 999,
                '预算错配金额': 123,
                'ROI': 2.5,
                '预算角色': 'ZT',
                '预算动作': '保持',
                '角色原因': '旧角色依据',
                '人工业务标记': '本月主推',
            }]),
            'plans': pd.DataFrame([{
                '序号': 1,
                '计划ID': 'PCTX1',
                '计划名称': '关键词测试',
                '所属渠道': '关键词',
                '商品数': 1,
                '计划类型': '单商品计划',
                '周期花费': 100,
                '归因成交': 250,
                'ROI': 2.5,
                '方向证据': '测试',
                '计划管理建议': '按商品预算动作复核；不补充计划预算或出价',
                '预算角色': 'ZT',
                '预算动作': '保持',
                '角色状态': '旧状态',
                '人工标记': '人工清仓',
            }]),
        }

        workbook = export_all_sheets(source, budget_decision_context=context)

        products = pd.read_excel(
            io.BytesIO(workbook), sheet_name=BUDGET_DECISION_PRODUCT_SHEET
        )
        _assert_no_removed_budget_columns(self, products)
        for column in BUDGET_DECISION_REQUIRED_PRODUCT_COLUMNS:
            self.assertIn(column, products.columns)
        self.assertEqual(products.loc[0, '排名档'], 'Top1-5')
        self.assertEqual(
            products.columns.get_loc('排名档'),
            products.columns.get_loc('淘宝类目') + 1,
        )
        self.assertEqual(products.loc[0, '预算匹配状态'], '冻结透传状态')
        self.assertEqual(products.loc[0, '动作依据'], '冻结透传依据')
        self.assertEqual(products.loc[0, '预算风险'], '极高退款')
        self.assertEqual(products.loc[0, '近7日日均GMV'], 123.45)
        self.assertEqual(products.loc[0, '上一7日日均GMV'], 100.0)
        self.assertEqual(products.loc[0, 'GMV趋势率'], 0.2345)
        self.assertEqual(products.loc[0, 'TH_MAIN'], 120)
        self.assertEqual(products.loc[0, 'TH_MID'], 80)
        self.assertEqual(products.loc[0, '阈值版本'], 'store-freeze-v1')

        tiers = pd.read_excel(io.BytesIO(workbook), sheet_name=BUDGET_DECISION_TIER_SHEET)
        _assert_no_removed_budget_columns(self, tiers)
        self.assertEqual(
            tiers.columns.tolist()[:len(BUDGET_DECISION_TIER_COLUMNS)],
            BUDGET_DECISION_TIER_COLUMNS,
        )
        self.assertEqual(
            tiers.columns.get_loc('ROI'),
            tiers.columns.get_loc('预算错配金额') + 1,
        )
        self.assertIn('预算错配金额', tiers.columns)
        self.assertNotIn('错配金额', tiers.columns)
        self.assertNotIn('销售梯队', tiers.columns)
        self.assertEqual(tiers.loc[0, '排名档'], 'Top1-5')
        self.assertEqual(tiers.loc[0, '预算错配金额'], 123)
        self.assertEqual(tiers.loc[0, 'ROI'], 2.5)

        plans = pd.read_excel(io.BytesIO(workbook), sheet_name=BUDGET_DECISION_PLAN_SHEET)
        _assert_no_removed_budget_columns(self, plans)
        self.assertEqual(
            plans.columns.tolist()[:len(BUDGET_DECISION_PLAN_COLUMNS)],
            BUDGET_DECISION_PLAN_COLUMNS,
        )
        self.assertEqual(plans.columns[0], '序号')
        self.assertEqual(plans.loc[0, '序号'], 1)
        self.assertEqual(plans.loc[0, '计划ID'], 'PCTX1')
        self.assertEqual(plans.loc[0, 'ROI'], 2.5)
        self.assertNotIn('预算动作', plans.loc[0, '计划管理建议'])
        self.assertIn('商品调整方向', plans.loc[0, '计划管理建议'])

    def test_no_paid_no_plan_exports_budget_tabs_without_ai_task_view(self):
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
        excel = pd.ExcelFile(io.BytesIO(workbook))

        self.assertEqual(excel.sheet_names[0], EXECUTION_SHEET)
        self.assertIn(BUDGET_DECISION_PRODUCT_SHEET, excel.sheet_names)
        self.assertIn(BUDGET_DECISION_TIER_SHEET, excel.sheet_names)
        self.assertIn(BUDGET_DECISION_PLAN_SHEET, excel.sheet_names)
        self.assertNotIn('AI任务视图', excel.sheet_names)

    def test_category_investment_and_roi_sheet_is_in_both_downloads(self):
        plan = _plan_payload([{
            '计划ID': 'P1003', '当前花费': 100, '归因成交': 200,
            '加购': 10, '点击': 50, '所属渠道': '关键词',
            '计划类型': '关键词标准计划',
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
            '付费', '付费占比', '上周期付费', '7日付费环比',
            '关键词', '人群', '全站',
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
                self.assertEqual(category.loc[0, '7日付费环比'], '11.1%')
                self.assertEqual(category.loc[0, '上周期关键词'], 50.0)
                self.assertEqual(category.loc[0, '上周期人群'], 30.0)
                self.assertEqual(category.loc[0, '上周期全站'], 10.0)
                self.assertEqual(category.loc[0, 'ROI'], 10.0)

    def test_category_sheets_match_page_source_instead_of_execution_filters(self):
        full_source = pd.DataFrame([
            {
                '宝贝ID': 'C1001', '淘宝类目': '连衣裙', 'GMV': 1000.0,
                '付费': 0.0, '商品访客数': 100, '商品加购件数': 20,
                '支付买家数': 10, '关键词': 0.0, '人群': 0.0, '全站': 0.0,
                '退款率': 0.5,
            },
            {
                '宝贝ID': 'C1002', '淘宝类目': 'T恤', 'GMV': 500.0,
                '付费': 0.0, '商品访客数': 50, '商品加购件数': 5,
                '支付买家数': 2, '关键词': 0.0, '人群': 0.0, '全站': 0.0,
                '退款率': 0.1,
            },
        ])
        execution_filter = full_source.iloc[[0]].copy()
        category_context = {
            'h7_cur_visitors': {'连衣裙': 100.0, 'T恤': 50.0},
            'h7_prev_visitors': {'连衣裙': 80.0, 'T恤': 40.0},
            'h7_cur_carts': {'连衣裙': 20.0, 'T恤': 5.0},
            'h7_prev_carts': {'连衣裙': 10.0, 'T恤': 4.0},
            'h7_cur_buyers': {'连衣裙': 10.0, 'T恤': 2.0},
            'h7_prev_buyers': {'连衣裙': 8.0, 'T恤': 1.0},
        }

        workbook = export_all_sheets(
            execution_filter,
            category_df=full_source,
            cat_ctx=category_context,
        )
        execution = pd.read_excel(io.BytesIO(workbook), sheet_name=EXECUTION_SHEET)
        category = pd.read_excel(io.BytesIO(workbook), sheet_name='品类分析')
        investment = pd.read_excel(
            io.BytesIO(workbook), sheet_name='品类投放占比与ROI'
        )

        self.assertEqual(len(execution), 1)
        self.assertEqual(category['商品数'].sum(), 2)
        self.assertEqual(set(category['品类']), {'连衣裙', 'T恤'})
        self.assertEqual(set(investment['品类']), {'连衣裙', 'T恤'})
        self.assertEqual(
            category.columns.tolist(),
            [
                '品类', '商品数', 'GMV', 'GMV占比', '付费占比', '付费',
                '付费环比', '商品访客数', '访客数占比', '7日访客数环比',
                '品类加购件数', '7日加购件数环比', '品类加购率',
                '7日加购率环比', '品类支付转化率', '7日支付转化率环比',
                '退款率', 'SPU价值', 'ROI',
            ],
        )
        dress = category.set_index('品类').loc['连衣裙']
        self.assertEqual(dress['商品访客数'], 100)
        self.assertEqual(dress['7日访客数环比'], '25.0%')
        self.assertEqual(dress['品类加购率'], '20.0%')
        self.assertEqual(dress['品类支付转化率'], '10.0%')
        self.assertEqual(dress['退款率'], '50.0%')

        execution_workbook = export_execution_table(
            execution_filter,
            category_df=full_source,
            cat_ctx=category_context,
        )
        execution_investment = pd.read_excel(
            io.BytesIO(execution_workbook), sheet_name='品类投放占比与ROI'
        )
        self.assertEqual(set(execution_investment['品类']), {'连衣裙', 'T恤'})

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
                self.assertIn('退款后GSV=GMV×(1-退款率)', rules.loc[0, '指标口径'])
                self.assertIn('退款后销售成本=退款后GSV÷件单价×成本价（含税）', rules.loc[0, '指标口径'])
                self.assertIn('盈亏平衡线=GMV÷可投放毛利额', rules.loc[0, '基准或参考线'])
                self.assertIn('同组无数据时不跨类目或价格段回退', rules.loc[0, '基准或参考线'])
                self.assertIn('max(盈亏平衡线×1.2，同组P75)', rules.loc[0, '判定标准'])
                self.assertIn('不使用链接实际投产过滤', rules.loc[0, '特殊情况'])
                self.assertIn('支付买家数÷商品访客数', rules.loc[1, '指标口径'])
                self.assertIn('商品加购件数÷商品访客数', rules.loc[2, '指标口径'])
                self.assertIn('14≤可售天数<28：需关注', rules.loc[3, '判定标准'])
                self.assertIn('28≤可售天数≤40：充足', rules.loc[3, '判定标准'])
                self.assertIn('可售天数>40：积压', rules.loc[3, '判定标准'])
                self.assertNotIn('60', rules.loc[3, '判定标准'])
                self.assertIn('推广加购件数÷推广访客数', rules.loc[4, '指标口径'])
                self.assertIn('当前访客数<100', rules.loc[4, '特殊情况'])

    def test_new_product_sheets_are_exported_with_empty_template_without_context(self):
        source = pd.DataFrame([{
            '宝贝ID': '1005',
            '淘宝类目': '半身裙',
            'GMV': 1000.0,
            '付费': 0.0,
            '商品访客数': 200,
            '关键词': 60.0,
            '人群': 25.0,
            '全站': 15.0,
        }])

        workbook = export_all_sheets(source)
        excel = pd.ExcelFile(io.BytesIO(workbook))

        for sheet in ('新品判定', '素材表现', '回测验证', '新品口径说明'):
            self.assertIn(sheet, excel.sheet_names)

        lifecycle = pd.read_excel(io.BytesIO(workbook), sheet_name='新品判定')
        methodology = pd.read_excel(io.BytesIO(workbook), sheet_name='新品口径说明')

        self.assertIn('首次在线发现日', lifecycle.columns)
        self.assertIn('D15结果', lifecycle.columns)
        self.assertIn('D30结果', lifecycle.columns)
        self.assertNotIn('D14结果', lifecycle.columns)
        self.assertNotIn('D21结果', lifecycle.columns)
        self.assertEqual(methodology.columns.tolist(), ['主题', '口径', '说明'])
        self.assertIn('首次在线发现日', ''.join(methodology['口径'].astype(str).tolist()))
        self.assertIn('D15', ''.join(methodology.astype(str).agg(''.join, axis=1).tolist()))
        self.assertIn('D30', ''.join(methodology.astype(str).agg(''.join, axis=1).tolist()))
        self.assertNotIn('D14', ''.join(methodology.astype(str).agg(''.join, axis=1).tolist()))
        self.assertNotIn('D21', ''.join(methodology.astype(str).agg(''.join, axis=1).tolist()))

    def test_new_product_context_is_written_to_all_new_sheets(self):
        source = pd.DataFrame([{
            '宝贝ID': '1006',
            '淘宝类目': '连衣裙',
            'GMV': 1200.0,
            '付费': 0.0,
            '商品访客数': 240,
            '关键词': 60.0,
            '人群': 40.0,
            '全站': 20.0,
        }])
        new_product_ctx = {
            'lifecycle': pd.DataFrame([{
                '货号': 'SKU-1006',
                '关联宝贝ID': '1006',
                '商品名称': '不应导出的商品名称',
                '首次在线发现日': '2026-08-01',
                '新品年龄': 30,
                'D15结果': '继续观察',
                'D30结果': '下架',
                'D14结果': '继续观察',
                'D21结果': '继续观察',
                'D28结果': '继续观察',
                'left_censored': True,
                'D14商品状态': '当前在线',
                'D14在仓库存': 10,
                'D15商品状态': '当前在线',
                'D15在仓库存': 8,
                'D21商品状态': '当前在线',
                'D21在仓库存': 6,
                'D30商品状态': '当前在线',
                'D30在仓库存': 4,
                'D15_D21_商品访客数': 300,
                'D15_D21_商品加购件数': 3,
                'D15_D21_支付买家数': 0,
                'D15_D21_支付件数': 0,
                'D15_D21_GMV': 0.0,
                'D15_D21_有效数据天数': 7,
                'D15_D21_商品加购率': 0.01,
                'D1_D21_商品访客数': 600,
                'D1_D21_商品加购件数': 6,
                'D1_D21_支付买家数': 0,
                'D1_D21_支付件数': 0,
                'D1_D21_GMV': 0.0,
                'D1_D21_有效数据天数': 21,
                'D1_D21_商品加购率': 0.01,
                '当前结果': '下架',
                '判定原因': 'D30加购弱且证据充分',
            }]),
            'materials': pd.DataFrame([{
                '宝贝ID': '1006',
                '商品名称': '素材表也不应导出商品名称',
                '创意ID': 'C1006',
                '创意展现量': 1000,
                '创意点击量': 50,
                '无界创意CTR': 0.05,
                '是否主投创意': True,
            }]),
            'backtest': pd.DataFrame([{
                '判定节点': 'D15',
                '规则版本': 'A-商品经营核心规则',
                '样本数': 1,
                '下架建议数': 1,
                '不可验证样本数': 0,
            }]),
            'methodology': pd.DataFrame([{
                '主题': '测试口径',
                '口径': '累计分子÷累计分母',
                '说明': '用于导出测试',
            }]),
        }

        workbook = export_all_sheets(source, new_product_ctx=new_product_ctx)

        lifecycle = pd.read_excel(io.BytesIO(workbook), sheet_name='新品判定')
        materials = pd.read_excel(io.BytesIO(workbook), sheet_name='素材表现')
        backtest = pd.read_excel(io.BytesIO(workbook), sheet_name='回测验证')
        methodology = pd.read_excel(io.BytesIO(workbook), sheet_name='新品口径说明')

        self.assertEqual(lifecycle.loc[0, '货号'], 'SKU-1006')
        self.assertEqual(lifecycle.loc[0, '当前结果'], '下架')
        self.assertEqual(lifecycle.loc[0, 'D15结果'], '继续观察')
        self.assertEqual(lifecycle.loc[0, 'D30结果'], '下架')
        self.assertEqual(lifecycle.loc[0, 'D30商品状态'], '当前在线')
        self.assertEqual(lifecycle.loc[0, 'D30在仓库存'], 4)
        for stale_column in (
            'D14结果',
            'D21结果',
            'D28结果',
            'left_censored',
            'D14商品状态',
            'D14在仓库存',
            'D15商品状态',
            'D15在仓库存',
            'D21商品状态',
            'D21在仓库存',
            'D15_D21_商品访客数',
            'D15_D21_商品加购件数',
            'D15_D21_支付买家数',
            'D15_D21_支付件数',
            'D15_D21_GMV',
            'D15_D21_有效数据天数',
            'D15_D21_商品加购率',
            'D1_D21_商品访客数',
            'D1_D21_商品加购件数',
            'D1_D21_支付买家数',
            'D1_D21_支付件数',
            'D1_D21_GMV',
            'D1_D21_有效数据天数',
            'D1_D21_商品加购率',
        ):
            self.assertNotIn(stale_column, lifecycle.columns)
        self.assertNotIn('商品名称', lifecycle.columns)
        self.assertEqual(materials.loc[0, '创意ID'], 'C1006')
        self.assertNotIn('商品名称', materials.columns)
        self.assertEqual(backtest.loc[0, '判定节点'], 'D15')
        self.assertEqual(methodology.loc[0, '主题'], '测试口径')


if __name__ == '__main__':
    unittest.main()
