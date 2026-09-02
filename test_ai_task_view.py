"""AI任务策略引擎18个反向场景与页面契约测试。"""
from __future__ import annotations

from pathlib import Path
import json
import unittest

import pandas as pd
from pandas.testing import assert_frame_equal

import core.analysis as analysis_module
import core.task_engine as task_engine_module
from core.analysis import AI_TASK_VIEW_COLUMNS, build_ai_task_view
from core.task_engine import (
    AI_TASK_MAIN_COLUMNS,
    PLAN_DETAIL_COLUMNS,
    _consistency_conflicts,
    parse_plan_execution_details,
)


ROOT = Path(__file__).resolve().parent


def _detail(*plans):
    lines = []
    for index, plan in enumerate(plans, start=1):
        lines.append(
            f"{index}. 计划ID:{plan.get('id', index)} "
            f"花费:{plan.get('spend', 0):.2f} "
            f"投产:{plan.get('roi', 0):.2f} "
            f"加购:{plan.get('cart', 0)}"
        )
    return '\n'.join(lines)


def _row(**overrides):
    row = {
        '宝贝ID': '1001', '商品名称': '测试商品', '淘宝类目': '连衣裙',
        '货号': 'SKU-001', '款式主图': 'https://img.example/1.jpg', '链接数': 2,
        '综合等级': 'A', '商品定位': '冲刺款', '商品周期': '成长期',
        '流量定位': '冲刺款', '价值定位': '潜力款', '组合': '冲刺款潜力款',
        'MetoX算法命中': '', 'MetoX算法分': 0, 'MetoX算法原因': '', 'v9.3信号': '',
        'GMV': 3000, '付费': 300, '商品访客数': 300, '商品加购件数': 30,
        '访客平均价值': 10, '推广成交金额': 2400, '推广加购数': 24,
        '推广访客数': 150, '推广投产': 8, '链接投产': 10,
        '关键词': 120, '人群': 100, '全站': 80,
        '关键词归因成交': 1200, '人群归因成交': 800, '全站归因成交': 400,
        '上周期关键词': 100, '上周期人群': 100, '上周期全站': 100,
        '关键词环比': 0.2, '人群环比': 0, '全站环比': -0.2,
        '上周期金额': 2500, '上周期付费': 300,
        '日销3': 8, '日销7': 7, '日销15': 6,
        '花费3': 45, '花费7': 42, '花费15': 40,
        '计划数据口径': '近7天累计', '计划统计天数': 7, '计划实际数据天数': 7,
        '推广计划总数': 3, '推广计划展示数': 3,
        '计划覆盖率': 1.0, '计划数据完整度': 1.0,
        '退款率': 0.1, '在仓库存': 100, '在途': 0, '风险标签': '',
        '推广明细': _detail(
            {'id': 'P-HIGH', 'spend': 120, 'roi': 12, 'cart': 15},
            {'id': 'P-NORMAL', 'spend': 100, 'roi': 8, 'cart': 8},
            {'id': 'P-LOW', 'spend': 80, 'roi': 2, 'cart': 1},
        ),
    }
    row.update(overrides)
    return row


def _one(**overrides):
    result = build_ai_task_view(pd.DataFrame([_row(**overrides)]))
    if result.empty:
        raise AssertionError('预期生成任务，实际被过滤')
    return result.iloc[0]


def _plans(task):
    return parse_plan_execution_details(task['计划执行明细'])


class AITaskViewEighteenScenarioTests(unittest.TestCase):
    def test_01_a_sprint_growth_reduce_spillover_keyword_high_crowd_low(self):
        task = _one(
            综合等级='A', 商品定位='冲刺款', 商品周期='成长期',
            MetoX算法命中='削减|溢出', 推广成交金额=1800,
            关键词归因成交=1200, 人群归因成交=200, 全站归因成交=400,
        )
        self.assertEqual(task['AI任务类型'], '渠道调整任务')
        self.assertEqual(task['商品级总策略'], '预算迁移')
        self.assertEqual(task['关键词状态'], '高效渠道')
        self.assertEqual(task['人群状态'], '低效渠道')
        self.assertIn('增加', task['关键词动作'])
        self.assertIn('削减', task['人群动作'])

    def test_02_a_plus_cash_cow_needs_optimization_without_aggressive_increase(self):
        task = _one(
            综合等级='A+', 商品定位='金牛款', 商品周期='待优化',
            MetoX算法命中='削减|溢出', 推广成交金额=1800,
            关键词归因成交=1200, 人群归因成交=200, 全站归因成交=400,
        )
        self.assertEqual(task['商品级总策略'], '结构优化')
        self.assertTrue(task['关键词动作'].startswith('保持高效投放'))
        self.assertIn('削减30%～50%', task['人群动作'])

    def test_03_b_potential_decline_warning_keeps_test_not_large_migration(self):
        task = _one(
            综合等级='B', 商品定位='潜力款', 商品周期='衰退预警',
            MetoX算法命中='削减|溢出', 推广成交金额=1800,
            关键词归因成交=1200, 人群归因成交=200, 全站归因成交=400,
        )
        self.assertEqual(task['商品级总策略'], '结构优化')
        self.assertNotEqual(task['商品级总策略'], '预算迁移')
        self.assertIn('保持高效', task['关键词动作'])

    def test_04_c_wait_improve_only_reduce_is_stop_loss(self):
        task = _one(
            综合等级='C', 商品定位='待改善', 商品周期='待优化',
            MetoX算法命中='削减', 推广成交金额=0,
            关键词归因成交=0, 人群归因成交=0, 全站归因成交=0,
        )
        self.assertEqual(task['AI任务类型'], '止损任务')
        self.assertEqual(task['商品级总策略'], '暂停推广')
        self.assertIn('复核主图', task['非投放动作'])

    def test_05_all_paid_channels_low_pauses_or_reduces(self):
        task = _one(
            MetoX算法命中='削减', 推广成交金额=0,
            关键词归因成交=0, 人群归因成交=0, 全站归因成交=0,
        )
        self.assertEqual(task['商品级总策略'], '暂停推广')
        self.assertTrue(all(_plans(task)['建议动作'] == '暂停'))

    def test_06_only_keyword_is_efficient(self):
        task = _one(
            MetoX算法命中='削减|溢出', 推广成交金额=1200,
            关键词=120, 关键词归因成交=1200,
            人群=0, 人群归因成交=0, 全站=0, 全站归因成交=0,
        )
        self.assertEqual(task['关键词状态'], '高效渠道')
        self.assertEqual(task['人群状态'], '暂无投放')
        self.assertNotEqual(task['商品级总策略'], '预算迁移')

    def test_07_only_omni_is_efficient(self):
        task = _one(
            MetoX算法命中='削减|溢出', 推广成交金额=800,
            关键词=0, 关键词归因成交=0, 人群=0, 人群归因成交=0,
            全站=80, 全站归因成交=800,
        )
        self.assertEqual(task['全站状态'], '高效渠道')
        self.assertEqual(task['关键词状态'], '暂无投放')
        self.assertNotEqual(task['商品级总策略'], '预算迁移')

    def test_08_all_channels_zero_never_generates_budget_migration(self):
        task = _one(
            MetoX算法命中='加购爆发', 付费=0, 推广成交金额=0,
            关键词=0, 人群=0, 全站=0,
            关键词归因成交=0, 人群归因成交=0, 全站归因成交=0,
            推广明细='',
        )
        self.assertNotEqual(task['商品级总策略'], '预算迁移')
        self.assertEqual(task['关键词状态'], '暂无投放')
        self.assertIn('小额测试', task['关键词动作'])

    def test_09_paid_channels_missing_attribution_require_manual_review(self):
        frame = pd.DataFrame([_row(MetoX算法命中='强主推', 推广明细='')]).drop(
            columns=['关键词归因成交', '人群归因成交', '全站归因成交']
        )
        task = build_ai_task_view(frame).iloc[0]
        self.assertEqual(task['AI任务类型'], '人工复核任务')
        self.assertEqual(task['执行优先级'], 'P4')
        self.assertEqual(task['关键词状态'], '数据不足')

    def test_10_multiple_plans_mix_high_and_low_actions(self):
        task = _one(
            MetoX算法命中='削减|溢出', 推广成交金额=1800,
            关键词归因成交=1200, 人群归因成交=200, 全站归因成交=400,
        )
        actions = set(_plans(task)['建议动作'])
        self.assertIn('增加', actions)
        self.assertTrue(actions & {'削减', '暂停'})
        self.assertEqual(task['推广计划数'], 3)

    def test_11_all_plans_low_follow_product_stop_loss(self):
        task = _one(
            MetoX算法命中='削减', 推广成交金额=0,
            关键词归因成交=0, 人群归因成交=0, 全站归因成交=0,
            推广明细=_detail(
                {'id': 'L1', 'spend': 120, 'roi': 0},
                {'id': 'L2', 'spend': 100, 'roi': 0},
            ),
        )
        self.assertEqual(task['商品级总策略'], '暂停推广')
        self.assertEqual(set(_plans(task)['建议动作']), {'暂停'})

    def test_12_single_valid_plan_is_parsed_and_acted_on(self):
        task = _one(
            MetoX算法命中='加购爆发',
            推广明细=_detail({'id': 'ONLY-1', 'spend': 300, 'roi': 8, 'cart': 20}),
        )
        details = _plans(task)
        self.assertEqual(len(details), 1)
        self.assertEqual(details.iloc[0]['计划ID'], 'ONLY-1')
        self.assertIn(details.iloc[0]['建议动作'], ('增加', '保持'))

    def test_13_plan_parse_failure_does_not_break_product_or_channel_advice(self):
        task = _one(MetoX算法命中='保护持有', 推广明细='这不是可解析的计划明细')
        self.assertEqual(task['商品级总策略'], '保持投放')
        self.assertEqual(task['推广计划数'], 0)
        self.assertEqual(task['计划执行摘要'], '计划明细不足，请人工复核')
        self.assertTrue(_plans(task).empty)

    def test_14_product_plan_conflict_is_detected(self):
        ctx = {'low_channels': ['人群'], 'high_channels': [], 'normal_channels': []}
        channels = {
            '关键词': {'spend': 100, 'action': '削减'},
            '人群': {'spend': 100, 'action': '暂停'},
            '全站': {'spend': 0, 'action': '无动作'},
        }
        plans = [{'建议动作': '保持'}]
        conflicts = _consistency_conflicts(ctx, '预算迁移', channels, plans)
        self.assertTrue(conflicts)
        self.assertIn('预算迁移', '/'.join(conflicts))

    def test_15_low_stock_does_not_cancel_scale(self):
        normal = _one(MetoX算法命中='强主推|加购爆发', 综合等级='A+', 在仓库存=100)
        low = _one(MetoX算法命中='强主推|加购爆发', 综合等级='A+', 在仓库存=2)
        self.assertEqual(low['商品级总策略'], normal['商品级总策略'])
        self.assertEqual(low['任务优先分'], normal['任务优先分'])
        self.assertEqual(low['关键词动作'], normal['关键词动作'])
        self.assertEqual(low['库存提醒'], '库存偏低，请关注')

    def test_16_high_stock_does_not_cancel_stop_loss(self):
        normal = _one(
            MetoX算法命中='削减', 推广成交金额=0,
            关键词归因成交=0, 人群归因成交=0, 全站归因成交=0,
            在仓库存=30, 商品访客数=100,
        )
        high = _one(
            MetoX算法命中='削减', 推广成交金额=0,
            关键词归因成交=0, 人群归因成交=0, 全站归因成交=0,
            在仓库存=500, 商品访客数=5,
        )
        self.assertEqual(high['商品级总策略'], normal['商品级总策略'])
        self.assertEqual(high['任务优先分'], normal['任务优先分'])
        self.assertEqual(high['库存提醒'], '库存较高，请关注动销')

    def test_17_no_promotion_no_signal_product_is_excluded(self):
        result = build_ai_task_view(pd.DataFrame([_row(
            付费=0, 推广成交金额=0, 关键词=0, 人群=0, 全站=0,
            关键词归因成交=0, 人群归因成交=0, 全站归因成交=0,
            推广明细='',
        )]))
        self.assertTrue(result.empty)
        self.assertEqual(list(result.columns), AI_TASK_VIEW_COLUMNS)

    def test_18_sku_image_and_link_count_are_passed_through(self):
        task = _one(
            MetoX算法命中='保护持有', 货号='RAW-SKU',
            款式主图='https://img.example/raw.png', 链接数=7,
        )
        self.assertEqual(task['货号'], 'RAW-SKU')
        self.assertEqual(task['款式主图'], 'https://img.example/raw.png')
        self.assertEqual(task['链接数'], 7)


class AITaskViewAdditionalContractTests(unittest.TestCase):
    def test_analysis_reexports_the_single_task_engine_implementation(self):
        self.assertIs(analysis_module.build_ai_task_view, task_engine_module.build_ai_task_view)
        self.assertIs(analysis_module.AI_TASK_VIEW_COLUMNS, task_engine_module.AI_TASK_VIEW_COLUMNS)

    def test_operating_metrics_are_passed_through_from_execution_table_without_recalculation(self):
        task = _one(
            MetoX算法命中='保护持有',
            GMV=1234.56789,
            商品访客数=321,
            链接投产=4.32109,
            付费=999,
            推广成交金额=1,
        )
        self.assertEqual(task['GMV'], 1234.56789)
        self.assertEqual(task['商品访客数'], 321)
        self.assertEqual(task['链接投产'], 4.32109)

    def test_main_table_has_exact_operational_projection_and_only_one_visible_confidence(self):
        self.assertEqual(AI_TASK_MAIN_COLUMNS, [
            '执行优先级', '任务优先分', '款式主图', '货号', '链接数', '宝贝ID',
            'GMV', '商品访客数', '链接投产', '淘宝类目', '综合等级', '商品定位',
            '商品周期', 'MetoX算法命中', '优先级', 'AI任务类型', '商品级总策略', '主执行动作',
            '动作对象', '关键词动作', '人群动作', '全站动作', '计划执行摘要',
            '观察周期', '成功标准', '停止条件', '任务触发原因', 'AI可信度', '库存提醒',
        ])
        self.assertNotIn('商品名称', AI_TASK_MAIN_COLUMNS)
        self.assertNotIn('AI可信度分', AI_TASK_MAIN_COLUMNS)
        self.assertNotIn('AI可信度说明', AI_TASK_MAIN_COLUMNS)
        self.assertNotIn('商品可信度', AI_TASK_MAIN_COLUMNS)
        self.assertNotIn('渠道可信度', AI_TASK_MAIN_COLUMNS)
        self.assertNotIn('计划可信度', AI_TASK_MAIN_COLUMNS)
        self.assertNotIn('商品名称', AI_TASK_VIEW_COLUMNS)

    def test_metox_priority_is_immediately_after_label_and_uses_highest_matching_level(self):
        self.assertEqual(
            AI_TASK_MAIN_COLUMNS.index('优先级'),
            AI_TASK_MAIN_COLUMNS.index('MetoX算法命中') + 1,
        )
        self.assertEqual(
            AI_TASK_VIEW_COLUMNS.index('优先级'),
            AI_TASK_VIEW_COLUMNS.index('MetoX算法命中') + 1,
        )
        expected = {
            '亏损风险': 'P0',
            '削减': 'P0',
            '加预算': 'P1',
            '强主推': 'P1',
            '饱和观察': 'P1',
            '加购爆发': 'P2',
            '明日之星': 'P2',
            '近3日潜力': 'P2',
            '保护持有': 'P3',
            '溢出': 'P3',
            '加购爆发|削减': 'P0',
        }
        for labels, priority in expected.items():
            with self.subTest(labels=labels):
                self.assertEqual(_one(MetoX算法命中=labels)['优先级'], priority)

    def test_external_v93_burst_remains_compatible_without_filling_metox_priority(self):
        task = _one(MetoX算法命中='', **{'v9.3信号': '爆发'})
        self.assertTrue(task['AI任务类型'])
        self.assertEqual(task['优先级'], '')

    def test_product_name_is_absent_from_task_output_not_only_hidden_by_page_projection(self):
        task = _one(MetoX算法命中='保护持有')
        self.assertNotIn('商品名称', task.index)

    def test_confidence_is_one_composite_value_with_operational_checklist(self):
        task = _one(MetoX算法命中='保护持有')
        self.assertRegex(task['AI可信度'], r'^[★☆]{5}（\d+%）$')
        explanation = task['AI可信度说明']
        self.assertIn('✓ 商品数据完整', explanation)
        self.assertIn('✓ 渠道数据完整', explanation)
        self.assertIn('✓ 商品-计划数据完整', explanation)
        self.assertIn('✓ 周期统计一致', explanation)
        self.assertIn('✓ MetoX数据完整', explanation)
        self.assertNotIn('商品可信度', explanation)
        self.assertNotIn('渠道可信度', explanation)
        self.assertNotIn('计划可信度', explanation)

    def test_partial_plan_evidence_lowers_confidence_without_changing_any_actions(self):
        complete = _one(MetoX算法命中='削减|溢出', 推广成交金额=1800)
        partial = _one(
            MetoX算法命中='削减|溢出', 推广成交金额=1800,
            推广计划总数=5, 推广计划展示数=3,
            计划覆盖率=0.6, 计划数据完整度=0.6,
            计划实际数据天数=4,
        )
        for field in (
            'AI任务类型', '商品级总策略', '关键词状态', '关键词动作',
            '人群状态', '人群动作', '全站状态', '全站动作', '计划执行摘要',
            '计划执行明细', '执行优先级', '任务优先分',
        ):
            self.assertEqual(complete[field], partial[field], field)
        self.assertGreater(complete['AI可信度分'], partial['AI可信度分'])
        self.assertIn('△ 当前仅展示Top3关键计划', partial['AI可信度说明'])
        self.assertIn('△ 部分计划数据缺失', partial['AI可信度说明'])

    def test_no_plan_coverage_is_not_reported_as_one_hundred_percent(self):
        task = _one(
            MetoX算法命中='保护持有', 付费=0, 推广成交金额=0,
            关键词=0, 人群=0, 全站=0,
            关键词归因成交=0, 人群归因成交=0, 全站归因成交=0,
            推广计划总数=0, 推广计划展示数=0,
            计划覆盖率=1.0, 计划数据完整度=1.0, 推广明细='',
        )
        self.assertEqual(task['计划总数'], 0)
        self.assertEqual(task['计划覆盖率'], '无计划')
        self.assertEqual(task['计划数据完整度'], '无计划')
        self.assertIn('无计划数据', task['计划数据说明'])
        self.assertNotIn('100.0%', task['计划数据说明'])

    def test_paid_without_plan_is_not_mislabeled_as_no_plan(self):
        task = _one(
            MetoX算法命中='保护持有', 付费=100, 推广成交金额=0,
            推广计划总数=0, 推广计划展示数=0,
            计划覆盖率=0.0, 计划数据完整度=0.0, 推广明细='',
        )
        self.assertEqual(task['计划数据完整度'], '0.0%')

    def test_no_plan_confidence_uses_product_and_channel_evidence_without_full_score(self):
        task = _one(
            MetoX算法命中='保护持有', 付费=0, 推广成交金额=0,
            关键词=0, 人群=0, 全站=0,
            关键词归因成交=0, 人群归因成交=0, 全站归因成交=0,
            推广计划总数=0, 推广计划展示数=0,
            计划覆盖率=1.0, 计划数据完整度=1.0, 推广明细='',
        )
        self.assertLess(task['AI可信度分'], 90)
        self.assertNotEqual(task['AI可信度'], '★★★★★（100%）')
        self.assertIn('当前无计划数据，计划可信度未参与计算', task['AI可信度说明'])
        self.assertNotIn('✓ 商品-计划数据完整', task['AI可信度说明'])

    def test_no_plan_explainability_metadata_does_not_change_business_actions(self):
        left = _one(
            MetoX算法命中='保护持有', 推广计划总数=0,
            推广计划展示数=0, 计划覆盖率=1.0, 计划数据完整度=1.0,
            推广明细='',
        )
        right = _one(
            MetoX算法命中='保护持有', 推广计划总数=0,
            推广计划展示数=0, 计划覆盖率=0.0, 计划数据完整度=0.0,
            推广明细='',
        )
        for field in (
            'AI任务类型', '商品级总策略', '关键词状态', '关键词动作',
            '人群状态', '人群动作', '全站状态', '全站动作',
            '计划执行摘要', '计划执行明细', '执行优先级', '任务优先分',
        ):
            self.assertEqual(left[field], right[field], field)

    def test_declining_product_budget_receiver_is_kept_not_increased(self):
        task = _one(
            综合等级='A', 商品定位='待改善', 商品周期='衰退期',
            MetoX算法命中='削减|溢出', 推广成交金额=1800,
            关键词归因成交=1200, 人群归因成交=200, 全站归因成交=400,
        )
        self.assertEqual(task['商品级总策略'], '预算迁移')
        self.assertTrue(task['关键词动作'].startswith('保持高效投放'))
        high_plan = _plans(task).set_index('计划ID').loc['P-HIGH']
        self.assertEqual(high_plan['建议动作'], '保持')
        self.assertEqual(high_plan['建议调整比例'], '0%')

    def test_json_plan_details_are_supported_without_inventing_missing_fields(self):
        raw = json.dumps([
            {'计划ID': 'JSON-1', '花费': 100, 'ROI': 6, '渠道': '关键词'},
        ], ensure_ascii=False)
        task = _one(MetoX算法命中='保护持有', 推广明细=raw)
        detail = _plans(task).iloc[0]
        self.assertEqual(detail['计划ID'], 'JSON-1')
        self.assertEqual(detail['所属渠道'], '关键词')
        self.assertEqual(detail['计划名称'], '执行表未提供')

    def test_plan_detail_columns_are_stable(self):
        task = _one(MetoX算法命中='保护持有')
        self.assertEqual(list(_plans(task).columns), PLAN_DETAIL_COLUMNS)

    def test_empty_passthrough_fields_stay_empty(self):
        task = _one(MetoX算法命中='保护持有', 货号=None, 款式主图=float('nan'), 链接数=None)
        self.assertEqual(task['货号'], '')
        self.assertEqual(task['款式主图'], '')
        self.assertEqual(task['链接数'], '')

    def test_input_dataframe_is_not_modified(self):
        source = pd.DataFrame([_row(MetoX算法命中='保护持有')])
        before = source.copy(deep=True)
        build_ai_task_view(source)
        assert_frame_equal(source, before)

    def test_explainability_fields_do_not_change_existing_business_actions(self):
        base = _one(MetoX算法命中='削减|溢出', 推广成交金额=1800)
        explained = _one(
            MetoX算法命中='削减|溢出', 推广成交金额=1800,
            计划数据口径='近7天累计', 计划统计天数=7, 计划实际数据天数=7,
            推广计划总数=5, 推广计划展示数=3, 计划覆盖率=0.85,
            计划数据完整度=1.0,
        )
        for field in (
            'AI任务类型', '商品级总策略', '关键词状态', '关键词动作',
            '人群状态', '人群动作', '全站状态', '全站动作', '计划执行摘要',
        ):
            self.assertEqual(base[field], explained[field], field)
        self.assertEqual(explained['动作对象'], '商品-计划级')
        self.assertEqual(explained['计划总数'], 5)
        self.assertEqual(explained['计划展示数'], 3)
        details = _plans(explained)
        self.assertTrue((details['动作对象'] == '商品-计划级').all())
        self.assertTrue(details['执行对象'].str.contains('宝贝ID 1001').all())
        self.assertEqual(explained['商品策略数据源'], '近7天商品汇总数据')
        self.assertEqual(explained['渠道动作数据源'], '近7天商品×渠道数据')
        self.assertEqual(explained['计划动作数据源'], '近7天商品×PlanID数据（Top3展示）')


class AITaskViewUIContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / 'app.py').read_text(encoding='utf-8-sig')

    def test_tab_order_places_ai_task_view_between_acceptance_and_execution(self):
        # The new-product view contains its own nested tabs, so anchor this
        # contract to the top-level navigation assignment instead of the first
        # st.tabs call in the module.
        main_tabs = 'tab1, tab2, tab3, tab4, tab5, tab6, tab7, tab8, tab9, tab10 = st.tabs(['
        start = self.source.index(main_tabs)
        tabs = self.source[start:self.source.index('])', start)]
        self.assertLess(tabs.index('数据验收'), tabs.index('AI任务视图'))
        self.assertLess(tabs.index('AI任务视图'), tabs.index('执行表'))

    def test_view_is_built_from_final_session_dataframe(self):
        main = self.source[self.source.index('# 主内容区'):]
        normalize = main.index('st.session_state.df = _normalize_display_columns')
        build = main.index('build_ai_task_view(st.session_state.df)')
        tabs = main.index('st.tabs([')
        self.assertLess(normalize, build)
        self.assertLess(build, tabs)

    def test_image_url_is_shown_as_link_without_image_column(self):
        view = self.source[self.source.index('with tab3:'):self.source.index('# ── Tab 4:')]
        self.assertIn("'款式主图': st.column_config.LinkColumn(", view)
        self.assertIn("'款式主图链接'", view)
        self.assertNotIn('ImageColumn', view)

    def test_main_table_hides_full_plan_json_and_detail_selector_expands_it(self):
        view = self.source[self.source.index('with tab3:'):self.source.index('# ── Tab 4:')]
        self.assertIn('task_main = task_filtered[AI_TASK_MAIN_COLUMNS].copy()', view)
        self.assertIn('parse_plan_execution_details', view)
        self.assertIn('选择商品查看计划动作', view)
        self.assertNotIn("task_choices.at[idx, '商品名称']", view)

    def test_main_table_renames_strategy_and_exposes_one_confidence_detail(self):
        view = self.source[self.source.index('with tab3:'):self.source.index('# ── Tab 4:')]
        self.assertIn("'商品级总策略': st.column_config.TextColumn('商品级策略'", view)
        self.assertIn("'AI可信度': st.column_config.TextColumn(", view)
        self.assertIn('with st.expander("AI可信度说明"', view)
        self.assertIn("selected_task['AI可信度说明'].splitlines()", view)

    def test_metox_filter_uses_the_renamed_cart_burst_label(self):
        view = self.source[self.source.index('with tab3:'):self.source.index('# ── Tab 4:')]
        self.assertIn("'加购爆发'", view)
        self.assertNotIn("'爆发'", view)

    def test_plan_explanation_states_period_top3_and_coverage(self):
        view = self.source[self.source.index('with tab3:'):self.source.index('# ── Tab 4:')]
        self.assertIn('Top3关键计划（按周期花费排序）', view)
        self.assertIn('计划覆盖率', view)
        self.assertIn('AI可信度', view)
        self.assertIn('商品-计划级', view)

    def test_sidebar_supports_30_day_period(self):
        self.assertIn('["当日", "3天", "7天", "15天", "30天"]', self.source)


if __name__ == '__main__':
    unittest.main()
