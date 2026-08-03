"""计划层周期累计与 Top3 展示边界测试。"""
from __future__ import annotations

import json
import unittest

import pandas as pd

from core.periods import aggregate_period_frame, build_period_ranges
from core.merge import _promotion_detail_agg, merge_sources
from core.plan_metrics import parse_full_plan_details
from core.schema import SOURCE_WUJIE_SUBJECT
from core.task_engine import build_ai_task_view, parse_plan_execution_details


def _payload(*plans):
    return json.dumps([
        {
            '计划ID': plan['id'],
            '当前花费': plan.get('spend', 0),
            '归因成交': plan.get('attributed', 0),
            '加购': plan.get('cart', 0),
            '点击': plan.get('clicks', 0),
            '所属渠道': plan.get('channel', '关键词'),
        }
        for plan in plans
    ], ensure_ascii=False)


def _day(date, *plans, **overrides):
    spend = sum(plan.get('spend', 0) for plan in plans)
    attributed = sum(plan.get('attributed', 0) for plan in plans)
    carts = sum(plan.get('cart', 0) for plan in plans)
    clicks = sum(plan.get('clicks', 0) for plan in plans)
    row = {
        '宝贝ID': '1001', '_数据日期': date,
        'GMV': attributed, '付费': spend, '推广成交金额': attributed,
        '商品访客数': 100, '支付买家数': 10, '支付件数': 10,
        '商品加购件数': carts, '推广加购数': carts,
        '展现': 1000, '点击': clicks,
        '关键词': spend, '人群': 0, '全站': 0,
        '关键词归因成交': attributed, '人群归因成交': 0, '全站归因成交': 0,
        '推广计划全量明细': _payload(*plans),
        '推广明细': '旧的单日Top3不得直接进入周期结果',
        '综合等级': 'A', '商品定位': '冲刺款', '商品周期': '成长期',
        '流量定位': '冲刺款', '价值定位': '潜力款', '组合': '冲刺款潜力款',
        'MetoX算法命中': '保护持有', 'MetoX算法分': 1, 'MetoX算法原因': '',
        'v9.3信号': '', '风险标签': '',
        '上周期金额': attributed, '上周期付费': spend,
        '上周期关键词': spend, '上周期人群': 0, '上周期全站': 0,
        '关键词环比': 0, '人群环比': 0, '全站环比': 0,
        '日销3': 1, '日销7': 1, '日销15': 1,
        '花费3': spend, '花费7': spend, '花费15': spend,
        '退款率': 0, '在仓库存': 100, '在途': 0, '成本价（含税）': 10,
    }
    row.update(overrides)
    return row


def _plans_from_period(period_row):
    final_row = period_row.to_dict() if hasattr(period_row, 'to_dict') else dict(period_row)
    final_row.update({
        '综合等级': 'A', '商品定位': '冲刺款', '商品周期': '成长期',
        '流量定位': '冲刺款', '价值定位': '潜力款', '组合': '冲刺款潜力款',
        'MetoX算法命中': '保护持有', 'MetoX算法分': 1, 'MetoX算法原因': '',
        'v9.3信号': '', '风险标签': '',
        '上周期金额': period_row.get('GMV', 0),
        '上周期付费': period_row.get('付费', 0),
    })
    tasks = build_ai_task_view(pd.DataFrame([final_row]))
    if tasks.empty:
        raise AssertionError('周期数据应生成AI任务')
    return tasks.iloc[0], parse_plan_execution_details(tasks.iloc[0]['计划执行明细'])


class PlanPeriodAggregationTests(unittest.TestCase):
    def test_daily_merge_keeps_all_plans_before_period_aggregation(self):
        raw = pd.DataFrame([
            {
                '_pid': '1001', '计划ID': f'P{index}', '付费': spend,
                '推广投产': roi, 'GMV': 0, '商品加购件数': index,
                '点击': index * 10, '场景名字': '关键词推广',
            }
            for index, (spend, roi) in enumerate(
                ((50, 2), (40, 3), (30, 4), (20, 5), (10, 6)), start=1
            )
        ])
        merged = _promotion_detail_agg(raw).iloc[0]
        all_plans = parse_full_plan_details(merged['推广计划全量明细'])
        self.assertEqual(len(all_plans), 5)
        self.assertEqual(merged['推广计划总数'], 5)
        self.assertEqual(merged['推广计划展示数'], 3)
        self.assertIn('计划ID:P1', merged['推广明细'])
        self.assertNotIn('计划ID:P4', merged['推广明细'])

    def test_merge_sources_preserves_plan_metadata_through_period_and_task_pipeline(self):
        raw = pd.DataFrame([
            {
                '宝贝ID': '1001', '计划ID': 'P1', '付费': 100,
                '推广投产': 3, 'GMV': 300, '商品加购件数': 10,
                '点击': 50, '场景名字': '关键词推广',
                '转化周期': '15天转化', '主体类型': '商品',
            },
            {
                '宝贝ID': '1001', '计划ID': 'P2', '付费': 40,
                '推广投产': 2, 'GMV': 80, '商品加购件数': 4,
                '点击': 20, '场景名字': '人群推广',
                '转化周期': '15天转化', '主体类型': '商品',
            },
        ])

        daily, plan_ids = merge_sources({
            SOURCE_WUJIE_SUBJECT: [('无界-商品主体-2026-08-01.xlsx', raw)],
        })
        daily_row = daily.iloc[0]
        self.assertEqual(plan_ids, {'P1', 'P2'})
        self.assertEqual(daily_row['推广计划总数'], 2)
        self.assertEqual(daily_row['推广计划展示数'], 2)
        self.assertEqual(daily_row['计划覆盖率'], 1.0)
        self.assertEqual(daily_row['计划数据完整度'], 1.0)
        self.assertEqual(daily_row['计划统计天数'], 1)
        self.assertEqual(daily_row['计划实际数据天数'], 1)
        self.assertEqual(daily_row['计划数据口径'], '当日累计')
        self.assertEqual(daily_row['计划数据范围'], '')
        self.assertEqual(len(parse_full_plan_details(daily_row['推广计划全量明细'])), 2)

        daily['_数据日期'] = '2026-08-01'
        period = aggregate_period_frame(
            daily, 1, actual_days=1,
            period_start='2026-08-01', period_end='2026-08-01',
        ).iloc[0]
        task, plans = _plans_from_period(period)
        self.assertEqual(period['推广计划总数'], 2)
        self.assertEqual(task['推广计划数'], 2)
        self.assertEqual(list(plans['计划ID']), ['P1', 'P2'])

    def test_7_day_aggregates_all_plans_before_top3(self):
        rows = [
            _day('2026-07-26', {'id': 'A', 'spend': 100, 'attributed': 200, 'cart': 10, 'clicks': 50}),
            _day('2026-07-27', {'id': 'B', 'spend': 80, 'attributed': 400, 'cart': 8, 'clicks': 40}),
            _day('2026-07-28', {'id': 'C', 'spend': 70, 'attributed': 70, 'cart': 7, 'clicks': 35}),
            _day('2026-07-29', {'id': 'D', 'spend': 60, 'attributed': 600, 'cart': 6, 'clicks': 30}),
            _day('2026-07-30', {'id': 'E', 'spend': 50, 'attributed': 10, 'cart': 5, 'clicks': 25}),
            _day('2026-07-31', {'id': 'A', 'spend': 20, 'attributed': 80, 'cart': 2, 'clicks': 10}),
            _day('2026-08-01'),
        ]
        period = aggregate_period_frame(
            pd.DataFrame(rows), 7, actual_days=7,
            period_start='2026-07-26', period_end='2026-08-01',
        ).iloc[0]

        task, plans = _plans_from_period(period)
        self.assertEqual(period['推广计划总数'], 5)
        self.assertEqual(period['推广计划展示数'], 3)
        self.assertAlmostEqual(period['计划覆盖率'], 270 / 380)
        self.assertEqual(list(plans['计划ID']), ['A', 'B', 'C'])
        plan_a = plans.set_index('计划ID').loc['A']
        self.assertEqual(plan_a['当前花费'], 120)
        self.assertEqual(plan_a['归因成交'], 280)
        self.assertAlmostEqual(plan_a['ROI'], 2.33, places=2)
        self.assertEqual(plan_a['CPA'], 10)
        self.assertEqual(plan_a['点击'], 60)
        self.assertEqual(task['计划总数'], 5)
        self.assertEqual(task['计划展示数'], 3)
        self.assertIn('覆盖3/5个计划', task['计划数据说明'])
        self.assertEqual(task['商品策略数据源'], '近7天商品汇总数据')
        self.assertEqual(task['渠道动作数据源'], '近7天商品×渠道数据')
        self.assertEqual(task['计划动作数据源'], '近7天商品×PlanID数据（Top3展示）')

    def test_15_day_keeps_new_and_paused_plans_in_same_window(self):
        rows = []
        for day in range(1, 16):
            plans = []
            if day <= 3:
                plans.append({'id': 'PAUSED', 'spend': 10, 'attributed': 20, 'cart': 1, 'clicks': 5})
            if day >= 10:
                plans.append({'id': 'NEW', 'spend': 20, 'attributed': 80, 'cart': 2, 'clicks': 8})
            rows.append(_day(f'2026-07-{day:02d}', *plans))

        period = aggregate_period_frame(
            pd.DataFrame(rows), 15, actual_days=15,
            period_start='2026-07-01', period_end='2026-07-15',
        ).iloc[0]
        task, plans = _plans_from_period(period)
        by_id = plans.set_index('计划ID')
        self.assertEqual(set(by_id.index), {'PAUSED', 'NEW'})
        self.assertEqual(by_id.loc['PAUSED', '当前花费'], 30)
        self.assertEqual(by_id.loc['NEW', '当前花费'], 120)
        self.assertEqual(period['计划数据口径'], '近15天累计')
        self.assertEqual(task['商品策略数据源'], '近15天商品汇总数据')
        self.assertEqual(task['渠道动作数据源'], '近15天商品×渠道数据')
        self.assertEqual(task['计划动作数据源'], '近15天商品×PlanID数据（Top3展示）')

    def test_30_day_plan_metrics_use_full_window(self):
        rows = [
            _day(
                f'2026-07-{day:02d}',
                {'id': 'P30', 'spend': 10, 'attributed': 30, 'cart': 2, 'clicks': 4},
            )
            for day in range(1, 31)
        ]
        period = aggregate_period_frame(
            pd.DataFrame(rows), 30, actual_days=30,
            period_start='2026-07-01', period_end='2026-07-30',
        ).iloc[0]
        task, plans = _plans_from_period(period)
        plan = plans.iloc[0]
        self.assertEqual(plan['当前花费'], 300)
        self.assertEqual(plan['归因成交'], 900)
        self.assertEqual(plan['CPA'], 5)
        self.assertEqual(plan['点击'], 120)
        self.assertEqual(task['商品策略数据源'], '近30天商品汇总数据')
        self.assertEqual(task['渠道动作数据源'], '近30天商品×渠道数据')
        self.assertEqual(task['计划动作数据源'], '近30天商品×PlanID数据（Top3展示）')

    def test_30_day_period_is_available_to_frontend(self):
        periods = build_period_ranges('2026-08-01')
        self.assertEqual(periods['30日环比']['current_start'], '2026-07-03')
        self.assertEqual(periods['30日环比']['current_end'], '2026-08-01')

    def test_incomplete_period_lowers_confidence_without_changing_strategy(self):
        complete = aggregate_period_frame(
            pd.DataFrame([_day('2026-08-01', {'id': 'P1', 'spend': 100, 'attributed': 500, 'cart': 10, 'clicks': 20})]),
            7, actual_days=7, period_start='2026-07-26', period_end='2026-08-01',
        ).iloc[0]
        incomplete = aggregate_period_frame(
            pd.DataFrame([_day('2026-08-01', {'id': 'P1', 'spend': 100, 'attributed': 500, 'cart': 10, 'clicks': 20})]),
            7, actual_days=4, period_start='2026-07-26', period_end='2026-08-01',
        ).iloc[0]
        complete_task, _ = _plans_from_period(complete)
        incomplete_task, _ = _plans_from_period(incomplete)
        invariant_fields = [
            'AI任务类型', '商品级总策略', '关键词状态', '关键词动作',
            '人群状态', '人群动作', '全站状态', '全站动作', '计划执行摘要',
        ]
        for field in invariant_fields:
            self.assertEqual(complete_task[field], incomplete_task[field], field)
        self.assertGreater(complete_task['AI可信度分'], incomplete_task['AI可信度分'])


if __name__ == '__main__':
    unittest.main()
