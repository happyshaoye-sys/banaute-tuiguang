"""Focused tests for read-only budget decision context."""
from __future__ import annotations

import json
import unittest

import pandas as pd
from pandas.testing import assert_frame_equal

from core.budget_decision import (
    BUDGET_DECISION_REMOVED_COLUMNS,
    PRODUCT_COLUMNS,
    RANK_BAND_ORDER,
    build_budget_decision_context,
    build_plan_management,
    is_removed_budget_column,
)
from core.plan_metrics import FULL_PLAN_DETAIL_COLUMN, PLAN_ACTUAL_DAYS_COLUMN


RETIRED_BUDGET_FIELD_SAMPLES = (
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
)


def _plan_payload(*plans):
    return json.dumps([
        {
            '计划ID': plan['id'],
            '计划名称': plan.get('name', ''),
            '当前花费': plan.get('spend', 0),
            '归因成交': plan.get('attributed', 0),
        }
        for plan in plans
    ], ensure_ascii=False)


def _row(product_id, gmv, spend, attributed=None, **overrides):
    row = {
        '宝贝ID': product_id,
        '款式主图': f'https://img.example/{product_id}.jpg',
        '货号': f'SKU-{product_id}',
        '淘宝类目': '连衣裙',
        'GMV': gmv,
        '付费': spend,
        '推广成交金额': attributed if attributed is not None else gmv,
        '综合等级': 'A',
        '商品周期': '成长期',
        'MetoX算法命中': '保护持有',
        '当前有效投放计划数': 1 if spend > 0 else 0,
        '在仓库存': 20,
        PLAN_ACTUAL_DAYS_COLUMN: 7,
    }
    row.update(overrides)
    return row


def _by_id(context):
    return {item['宝贝ID']: item for item in context['products']}


def _assert_removed_budget_fields_absent(testcase, context):
    for section in ('products', 'tiers', 'plans'):
        for row in context.get(section, []):
            for column in row:
                testcase.assertFalse(is_removed_budget_column(column), f'{section}.{column}')
            for column in RETIRED_BUDGET_FIELD_SAMPLES:
                testcase.assertNotIn(column, row, f'{section}.{column}')


class BudgetDecisionContextTests(unittest.TestCase):
    def test_removed_budget_field_detector_covers_role_and_manual_markers(self):
        for column in BUDGET_DECISION_REMOVED_COLUMNS:
            self.assertTrue(is_removed_budget_column(column), column)
        for column in RETIRED_BUDGET_FIELD_SAMPLES:
            self.assertTrue(is_removed_budget_column(column), column)

    def test_context_formulas_and_input_are_read_only(self):
        source = pd.DataFrame([
            _row('1001', 600, 300, 900),
            _row('1002', 400, 700, 350),
        ]).assign(
            预算角色=['ZT', 'TD'],
            预算动作=['加投', '降投'],
            人工业务标记=['本月主推', '人工清仓'],
            manual_marks=['manual-a', 'manual-b'],
        )
        before = source.copy(deep=True)

        context = build_budget_decision_context(source)
        products = _by_id(context)

        assert_frame_equal(source, before)
        _assert_removed_budget_fields_absent(self, context)
        self.assertEqual(context['actual_days'], 7)
        self.assertEqual(context['summary']['全店GMV'], 1000)
        self.assertEqual(context['summary']['全店付费'], 1000)
        self.assertEqual(context['summary']['周期GMV'], 1000)
        self.assertEqual(context['summary']['周期花费'], 1000)
        self.assertEqual(context['summary']['待回收金额'], 150)
        self.assertEqual(context['summary']['建议增加金额'], 150)
        self.assertAlmostEqual(context['summary']['全店推广ROI'], 1.25)
        self.assertEqual(context['summary']['全店推广ROI口径说明'], '全店推广ROI=推广成交金额总和/付费总和')

        self.assertIn('商品定位', products['1001'])
        self.assertNotIn('推广商品定位', products['1001'])
        self.assertNotIn('错配金额', products['1001'])
        self.assertAlmostEqual(products['1001']['销售占比'], 0.6)
        self.assertAlmostEqual(products['1001']['花费占比'], 0.3)
        self.assertAlmostEqual(products['1001']['占比差(pp)'], -30)
        self.assertAlmostEqual(products['1001']['基准应得花费'], 600)
        self.assertAlmostEqual(products['1001']['预算错配金额'], -300)
        self.assertAlmostEqual(products['1001']['未来7日花费基线'], 300)
        self.assertEqual(products['1001']['预算匹配状态'], '花费少投')
        self.assertIn('按错配额50%增加', products['1001']['动作依据'])
        self.assertAlmostEqual(products['1001']['未来7日建议调整金额'], 150)
        self.assertAlmostEqual(products['1001']['未来7日建议花费'], 450)

        self.assertAlmostEqual(products['1002']['占比差(pp)'], 30)
        self.assertAlmostEqual(products['1002']['预算错配金额'], 300)
        self.assertEqual(products['1002']['预算匹配状态'], '花费超配')
        self.assertIn('按错配额50%回收', products['1002']['动作依据'])
        self.assertAlmostEqual(products['1002']['未来7日建议调整金额'], -150)
        self.assertAlmostEqual(products['1002']['未来7日建议花费'], 550)

        product_keys = list(context['products'][0])
        band_index = product_keys.index('排名档')
        self.assertEqual(product_keys[band_index:band_index + 3], ['排名档', '销售排名', '预算风险'])
        # 运营指定的规范列顺序：链接投产紧跟 GMV趋势率，排在推广投产之前
        self.assertEqual(list(context['products'][0]), list(PRODUCT_COLUMNS))
        self.assertEqual(
            product_keys[product_keys.index('链接投产') + 1], '推广投产',
        )

        tier = {item['排名档']: item for item in context['tiers']}
        self.assertEqual(list(tier), RANK_BAND_ORDER)
        self.assertEqual(tier['Top1-5']['商品数'], 2)
        self.assertAlmostEqual(tier['Top1-5']['预算错配金额'], 0)
        self.assertAlmostEqual(tier['Top1-5']['ROI'], 1)
        tier_keys = list(tier['Top1-5'])
        self.assertEqual(tier_keys[tier_keys.index('预算错配金额') + 1], 'ROI')

    def test_unavailable_plan_budget_and_bid_fields_do_not_affect_decisions(self):
        base = pd.DataFrame([
            _row('1001', 600, 300, 900),
            _row('1002', 400, 700, 350),
        ])
        with_unavailable_fields = base.assign(
            计划组=['任意计划组A', '任意计划组B'],
            当前预算=[1, 999999],
            目标预算=[999999, 1],
            出价=[0.01, 9999],
        )

        base_context = build_budget_decision_context(base)
        extra_context = build_budget_decision_context(with_unavailable_fields)

        self.assertEqual(extra_context['products'], base_context['products'])
        self.assertEqual(extra_context['tiers'], base_context['tiers'])
        self.assertEqual(extra_context['summary'], base_context['summary'])

    def test_duplicate_product_rows_are_aggregated_to_one_decision(self):
        context = build_budget_decision_context(pd.DataFrame([
            _row('DUP', 100, 40, 80, 当前有效投放计划数=1),
            _row('DUP', 200, 60, 120, 当前有效投放计划数=2),
            _row('OTHER', 300, 100, 300),
        ]))
        products = _by_id(context)

        self.assertEqual(len(context['products']), 2)
        self.assertEqual(products['DUP']['GMV'], 300)
        self.assertEqual(products['DUP']['付费'], 100)
        self.assertEqual(products['DUP']['当前有效投放计划数'], 2)

    def test_rank_band_boundaries_and_zero_gmv_use_final_enum(self):
        rows = [
            _row(f'P{rank:03d}', 1000 - rank, 1)
            for rank in range(1, 102)
        ]
        rows.append(_row('ZERO', 0, 25, 0))

        context = build_budget_decision_context(pd.DataFrame(rows))
        products = _by_id(context)
        tiers = {item['排名档']: item for item in context['tiers']}

        self.assertEqual(products['P005']['排名档'], 'Top1-5')
        self.assertEqual(products['P006']['排名档'], 'Top6-10')
        self.assertEqual(products['P010']['排名档'], 'Top6-10')
        self.assertEqual(products['P011']['排名档'], 'Top11-20')
        self.assertEqual(products['P020']['排名档'], 'Top11-20')
        self.assertEqual(products['P021']['排名档'], 'Top21-50')
        self.assertEqual(products['P050']['排名档'], 'Top21-50')
        self.assertEqual(products['P051']['排名档'], 'Top51-100')
        self.assertEqual(products['P100']['排名档'], 'Top51-100')
        self.assertEqual(products['P101']['排名档'], 'Top101-尾部')
        self.assertEqual(products['ZERO']['排名档'], '零GMV回收池')
        self.assertNotIn('销售梯队', products['P001'])
        self.assertEqual({item['排名档'] for item in context['products']}, set(RANK_BAND_ORDER))
        self.assertEqual([item['排名档'] for item in context['tiers']], RANK_BAND_ORDER)
        self.assertEqual(tiers['Top101-尾部']['商品数'], 1)
        self.assertEqual(tiers['Top101-尾部']['付费'], 1)
        self.assertEqual(tiers['Top101-尾部']['零GMV消耗'], 0)
        self.assertEqual(tiers['零GMV回收池']['商品数'], 1)
        self.assertEqual(tiers['零GMV回收池']['付费'], 25)
        self.assertEqual(tiers['零GMV回收池']['零GMV消耗'], 25)
        _assert_removed_budget_fields_absent(self, context)

    def test_zero_gmv_does_not_consume_positive_sales_tiers(self):
        rows = [
            _row('P06', 100, 0),
            _row('P05', 100, 0),
            _row('P04', 100, 0),
            _row('P03', 100, 0),
            _row('P02', 100, 0),
            _row('P01', 100, 0),
            _row('ZERO', 0, 20, 0),
        ]

        context = build_budget_decision_context(pd.DataFrame(rows))
        products = _by_id(context)

        ranked_ids = [item['宝贝ID'] for item in context['products'][:6]]
        self.assertEqual(ranked_ids, ['P01', 'P02', 'P03', 'P04', 'P05', 'P06'])
        self.assertEqual(products['P05']['销售排名'], 5)
        self.assertEqual(products['P05']['排名档'], 'Top1-5')
        self.assertEqual(products['P06']['销售排名'], 6)
        self.assertEqual(products['P06']['排名档'], 'Top6-10')
        # 未上榜款留空：用 None（缺失）而不是 ''，否则同一列「整数+空串」
        # 会让 pyarrow 按 int64 推断后转换失败，看板整表渲染不出来。
        self.assertIsNone(products['ZERO']['销售排名'])
        self.assertEqual(products['ZERO']['排名档'], '零GMV回收池')
        self.assertEqual(products['ZERO']['预算匹配状态'], '无GMV有花费')
        self.assertIn('未来7日按当前花费基线全部回收', products['ZERO']['动作依据'])
        self.assertEqual(products['ZERO']['未来7日建议调整金额'], -20)

        zero_tier = next(item for item in context['tiers'] if item['排名档'] == '零GMV回收池')
        self.assertEqual(zero_tier['商品数'], 1)
        self.assertEqual(zero_tier['零GMV消耗'], 20)
        top_tier = next(item for item in context['tiers'] if item['排名档'] == 'Top1-5')
        self.assertEqual(top_tier['付费'], 0)
        self.assertEqual(top_tier['ROI'], 0)
        _assert_removed_budget_fields_absent(self, context)

    def test_inventory_alert_does_not_change_budget_result(self):
        low_stock = pd.DataFrame([
            _row('CLEAR', 1000, 50, 200, 商品周期='衰退期', 在仓库存=1),
            _row('KEEP', 500, 200, 300, 在仓库存=20),
        ])
        high_stock = low_stock.copy(deep=True)
        high_stock.loc[high_stock['宝贝ID'] == 'CLEAR', '在仓库存'] = 999

        low_context = build_budget_decision_context(low_stock)
        high_context = build_budget_decision_context(high_stock)
        low_clear = _by_id(low_context)['CLEAR']
        high_clear = _by_id(high_context)['CLEAR']

        self.assertEqual(low_clear['商品定位'], high_clear['商品定位'])
        for field in ('预算匹配状态', '动作依据', '未来7日建议调整金额', '未来7日建议花费'):
            self.assertEqual(low_clear[field], high_clear[field], field)
        self.assertEqual(low_clear['库存提醒'], '库存偏低')
        self.assertEqual(high_clear['库存提醒'], '较高')
        _assert_removed_budget_fields_absent(self, low_context)
        _assert_removed_budget_fields_absent(self, high_context)

    def test_old_top_10_role_heuristic_fields_are_not_emitted(self):
        rows = [
            _row(f'TOP{i:02d}', 1000 + i, 0, 0)
            for i in range(10)
        ]
        rows.append(_row('ROI', 100, 100, 1000))

        context = build_budget_decision_context(pd.DataFrame(rows))
        product = _by_id(context)['ROI']

        self.assertEqual(product['销售排名'], 11)
        self.assertEqual(product['排名档'], 'Top11-20')
        self.assertEqual(product['预算匹配状态'], '超配但高投产')
        self.assertEqual(product['未来7日建议调整金额'], 0)
        _assert_removed_budget_fields_absent(self, context)

    def test_role_private_position_and_manual_marker_columns_are_not_emitted(self):
        rows = [
            _row(f'TOP{i:02d}', 1000 + i, 0, 0)
            for i in range(10)
        ]
        rows.append(_row(
            'TIER',
            100,
            200,
            0,
            推广商品定位='旧私有定位',
            角色候选='ZT',
            角色状态='人工冻结',
            角色原因='运营备注',
            人工业务标记='本月主推',
        ))

        context = build_budget_decision_context(pd.DataFrame(rows))
        product = _by_id(context)['TIER']

        self.assertEqual(product['销售排名'], 11)
        self.assertEqual(product['排名档'], 'Top11-20')
        self.assertNotIn('推广商品定位', product)
        _assert_removed_budget_fields_absent(self, context)

    def test_high_roi_overallocated_product_is_kept(self):
        context = build_budget_decision_context(pd.DataFrame([
            _row('HIGH', 100, 800, 4000),
            _row('LOW', 900, 200, 0),
        ]))
        high = _by_id(context)['HIGH']

        self.assertEqual(high['预算匹配状态'], '超配但高投产')
        self.assertEqual(high['未来7日建议调整金额'], 0)
        self.assertIn('>= 全店推广ROI', high['动作依据'])
        _assert_removed_budget_fields_absent(self, context)

    def test_natural_sales_do_not_add_spend(self):
        context = build_budget_decision_context(pd.DataFrame([
            _row('NATURAL', 500, 0, 0),
            _row('PAID', 500, 200, 200),
        ]))
        natural = _by_id(context)['NATURAL']

        self.assertEqual(natural['预算匹配状态'], '自然成交无花费')
        self.assertIn('可建立小额测试', natural['动作依据'])
        self.assertEqual(natural['未来7日建议调整金额'], 0)
        self.assertEqual(natural['未来7日建议花费'], 0)
        _assert_removed_budget_fields_absent(self, context)

    def test_refund_risk_passthrough_does_not_restore_retired_fields(self):
        context = build_budget_decision_context(pd.DataFrame([_row(
            'RISK',
            1000,
            1000,
            0,
            当前退款率=0.9,
            预算风险='极高退款',
            预算角色='TD',
            预算动作='保持',
            人工业务标记='人工冻结',
        )]))
        product = _by_id(context)['RISK']

        self.assertEqual(product['预算风险'], '极高退款')
        self.assertEqual(product['预算匹配状态'], '基本匹配')
        self.assertEqual(product['未来7日建议调整金额'], 0)
        _assert_removed_budget_fields_absent(self, context)

    def test_threshold_passthrough_fields_survive_without_retired_fields(self):
        context = build_budget_decision_context(pd.DataFrame([
            _row(
                'TC',
                560,
                700,
                1400,
                近7日日均GMV=80,
                上一7日日均GMV=78,
                GMV趋势率=0.025641,
                TH_MAIN=100,
                TH_MID=40,
                阈值版本='manual-test',
                角色原因='旧角色依据',
                人工标记='本月主推',
            ),
            _row('SPENDER', 70, 7000, 0),
        ]))
        product = _by_id(context)['TC']

        self.assertAlmostEqual(float(product['近7日日均GMV']), 80)
        self.assertAlmostEqual(float(product['上一7日日均GMV']), 78)
        self.assertAlmostEqual(float(product['GMV趋势率']), 0.025641)
        self.assertAlmostEqual(float(product['TH_MAIN']), 100)
        self.assertAlmostEqual(float(product['TH_MID']), 40)
        self.assertEqual(product['阈值版本'], 'manual-test')
        self.assertEqual(product['预算匹配状态'], '花费少投')
        self.assertGreater(product['未来7日建议调整金额'], 0)
        _assert_removed_budget_fields_absent(self, context)

    def test_plan_management_identifies_single_collection_and_conflict_advice(self):
        source = pd.DataFrame([
            _row(
                'UP', 600, 300, 900,
                **{FULL_PLAN_DETAIL_COLUMN: _plan_payload(
                    {'id': 'P-SINGLE', 'name': '单品计划', 'spend': 200, 'attributed': 600},
                    {'id': 'P-MIX', 'name': '共用计划', 'spend': 100, 'attributed': 300},
                )},
            ),
            _row(
                'DOWN', 400, 700, 350,
                **{FULL_PLAN_DETAIL_COLUMN: _plan_payload(
                    {'id': 'P-MIX', 'name': '共用计划', 'spend': 700, 'attributed': 350},
                )},
            ),
        ])

        plans = build_plan_management(source)
        self.assertEqual([item['计划ID'] for item in plans], ['P-MIX', 'P-SINGLE'])
        self.assertEqual([item['序号'] for item in plans], [1, 2])
        by_plan = {item['计划ID']: item for item in plans}

        self.assertEqual(by_plan['P-SINGLE']['计划名称'], '单品计划')
        self.assertEqual(by_plan['P-SINGLE']['商品数'], 1)
        self.assertEqual(by_plan['P-SINGLE']['计划商品类型'], '单商品计划')
        self.assertEqual(by_plan['P-MIX']['商品数'], 2)
        self.assertEqual(by_plan['P-MIX']['计划商品类型'], '合集推广计划')
        self.assertEqual(by_plan['P-MIX']['计划类型'], '合集推广计划')
        self.assertAlmostEqual(by_plan['P-MIX']['所选周期花费'], 800)
        self.assertAlmostEqual(by_plan['P-MIX']['周期花费'], 800)
        self.assertAlmostEqual(by_plan['P-MIX']['归因成交'], 650)
        self.assertEqual(by_plan['P-MIX']['方向证据'], '增加、回收')
        self.assertIn('先拆分/清退低效商品，勿直接整体调整', by_plan['P-MIX']['计划管理建议'])
        self.assertNotIn('预算', by_plan['P-MIX']['计划名称'])

    def test_plan_management_structure_uses_product_position_and_adjustment_direction(self):
        payload = _plan_payload({'id': 'P-TC', 'name': '承接计划', 'spend': 100, 'attributed': 200})
        source = pd.DataFrame([_row('TC', 100, 100, 200, **{FULL_PLAN_DETAIL_COLUMN: payload})])
        products = [{
            '宝贝ID': 'TC',
            '商品定位': '承接款',
            '商品周期': '成熟期',
            '未来7日建议调整金额': 120,
            '_budget_direction': '增加',
            '_plan_detail_values': [payload],
        }]

        plans = build_plan_management(source, products=products)

        self.assertEqual(plans[0]['序号'], 1)
        self.assertEqual(plans[0]['计划经营结构'], '承接型')
        self.assertEqual(plans[0]['方向证据'], '增加')
        self.assertNotIn('预算角色', plans[0]['计划管理建议'])
        self.assertNotIn('预算动作', plans[0]['计划管理建议'])

    def test_empty_missing_columns_and_missing_plan_details_are_safe(self):
        empty = build_budget_decision_context(pd.DataFrame())
        self.assertEqual(empty['products'], [])
        self.assertEqual(empty['plans'], [])
        self.assertEqual(empty['actual_days'], 1)
        self.assertEqual(len(empty['tiers']), len(RANK_BAND_ORDER))

        missing_id = build_budget_decision_context(pd.DataFrame([{'GMV': 100, '付费': 10}]))
        self.assertEqual(missing_id['products'], [])
        self.assertEqual(missing_id['plans'], [])
        self.assertEqual(missing_id['summary']['全店GMV'], 0)

        fallback_source = pd.DataFrame([{
            '宝贝ID': 'FALLBACK',
            'GMV': 500,
            '付费': 50,
            '在仓库存': '',
        }])
        fallback = build_budget_decision_context(fallback_source)
        product = _by_id(fallback)['FALLBACK']
        self.assertEqual(fallback['actual_days'], 1)
        self.assertAlmostEqual(fallback['summary']['全店推广ROI'], 10)
        self.assertIn('GMV/付费回退', fallback['summary']['全店推广ROI口径说明'])
        self.assertAlmostEqual(product['未来7日花费基线'], 350)
        self.assertEqual(product['库存提醒'], '缺失')
        _assert_removed_budget_fields_absent(self, fallback)

        no_details = build_plan_management(pd.DataFrame([_row('1001', 100, 100, 200)]))
        self.assertEqual(no_details, [])


#: 运营指定的规范列顺序（看板与导出共用；改动必须同步这里）
BUDGET_PRODUCT_COLUMN_ORDER_SPEC = [
    '宝贝ID', '款式主图', '货号', '淘宝类目', '排名档', '销售排名',
    '预算风险', '决策角色', '角色闸门', '当前有效投放计划数',
    '未来7日建议调整金额', '预算错配金额', '库存提醒',
    '近7日日均GMV', '上一7日日均GMV', 'GMV趋势率',
    '链接投产', '推广投产',
    'GMV', '付费', '销售占比', '花费占比', '占比差(pp)', '基准应得花费',
    '未来7日花费基线', '未来7日建议花费', '预算匹配状态', '动作依据',
    '综合等级', '商品周期', '商品定位', '推广动作', 'MetoX算法命中',
    '决策角色依据', '角色闸门依据', '风险命中', '角色闸门命中',
    'TH_MAIN', 'TH_MID', '阈值版本',
]


class BudgetProductColumnOrderTests(unittest.TestCase):
    def test_product_columns_follow_operator_spec(self):
        self.assertEqual(PRODUCT_COLUMNS, BUDGET_PRODUCT_COLUMN_ORDER_SPEC)
        self.assertEqual(PRODUCT_COLUMNS.index('链接投产') + 1, PRODUCT_COLUMNS.index('推广投产'))
        self.assertEqual(PRODUCT_COLUMNS[:2], ['宝贝ID', '款式主图'])
        self.assertEqual(PRODUCT_COLUMNS[-3:], ['TH_MAIN', 'TH_MID', '阈值版本'])

    def test_link_roi_is_taken_from_execution_table(self):
        source = pd.DataFrame([_row('1001', 600, 300, 900, 链接投产=2.5)])
        product = _by_id(build_budget_decision_context(source))['1001']

        self.assertAlmostEqual(product['链接投产'], 2.5)
        self.assertEqual(list(product), list(PRODUCT_COLUMNS))
        self.assertLess(
            list(product).index('链接投产'), list(product).index('推广投产'),
        )

    def test_link_roi_defaults_to_zero_when_column_missing(self):
        product = _by_id(build_budget_decision_context(pd.DataFrame([_row('1001', 600, 300, 900)])))['1001']
        self.assertEqual(product['链接投产'], 0.0)


if __name__ == '__main__':
    unittest.main()
