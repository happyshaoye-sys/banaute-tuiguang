"""Regression tests for current active plan counts."""
from __future__ import annotations

import json
import unittest

import pandas as pd

from core.merge import merge_sources
from core.periods import aggregate_period_frame
from core.plan_metrics import CURRENT_ACTIVE_PLAN_COUNT_COLUMN
from core.schema import EXPORT_COLS, SOURCE_WUJIE_SUBJECT, execution_export_frame


CURRENT_ACTIVE_PLAN_COUNT = CURRENT_ACTIVE_PLAN_COUNT_COLUMN


def _wujie_row(product_id, plan_id, spend, date='2026-09-04'):
    return {
        '宝贝ID': product_id,
        '计划ID': plan_id,
        '付费': spend,
        '推广投产': 2,
        'GMV': spend * 2,
        '商品加购件数': 1,
        '点击': 10,
        '场景名字': '关键词推广',
        '转化周期': '15天转化',
        '主体类型': '商品',
        '统计日期': date,
    }


def _merge_wujie(rows):
    merged, _ = merge_sources({
        SOURCE_WUJIE_SUBJECT: [('无界-商品主体-2026-09-04.xlsx', pd.DataFrame(rows))],
    })
    return merged.sort_values('宝贝ID').reset_index(drop=True)


def _plan_payload(*plan_ids):
    return json.dumps([
        {
            '计划ID': plan_id,
            '当前花费': 10,
            '归因成交': 20,
            '加购': 1,
            '点击': 5,
            '所属渠道': '关键词',
        }
        for plan_id in plan_ids
    ], ensure_ascii=False)


def _day(product_id, date, *plan_ids):
    return {
        '宝贝ID': product_id,
        '_数据日期': date,
        'GMV': 20 * len(plan_ids),
        '推广成交金额': 20 * len(plan_ids),
        '付费': 10 * len(plan_ids),
        '商品访客数': 100,
        '支付买家数': 10,
        '支付件数': 10,
        '商品加购件数': len(plan_ids),
        '推广加购数': len(plan_ids),
        '展现': 1000,
        '点击': 5 * len(plan_ids),
        '关键词': 10 * len(plan_ids),
        '人群': 0,
        '全站': 0,
        '关键词归因成交': 20 * len(plan_ids),
        '人群归因成交': 0,
        '全站归因成交': 0,
        '推广计划全量明细': _plan_payload(*plan_ids),
        CURRENT_ACTIVE_PLAN_COUNT: len(set(plan_ids)),
        '推广明细': '每日推广明细',
        '退款率': 0,
        '在仓库存': 100,
        '在途': 0,
        '成本价（含税）': 10,
        '日销3': 1,
        '日销7': 1,
        '日销15': 1,
        '花费3': 1,
        '花费7': 1,
        '花费15': 1,
    }


class CurrentActivePlanCountTests(unittest.TestCase):
    def test_daily_current_active_plan_count_counts_duplicate_plan_once(self):
        merged = _merge_wujie([
            _wujie_row('1001', 'PLAN-A', 20),
            _wujie_row('1001', 'PLAN-A', 30),
        ])

        self.assertEqual(merged.loc[0, CURRENT_ACTIVE_PLAN_COUNT], 1)

    def test_daily_current_active_plan_count_excludes_zero_spend_plan(self):
        merged = _merge_wujie([
            _wujie_row('1001', 'PLAN-A', 20),
            _wujie_row('1001', 'PLAN-ZERO', 0),
        ])

        self.assertEqual(merged.loc[0, CURRENT_ACTIVE_PLAN_COUNT], 1)

    def test_daily_current_active_plan_count_is_isolated_by_product(self):
        merged = _merge_wujie([
            _wujie_row('1001', 'PLAN-A', 20),
            _wujie_row('1001', 'PLAN-B', 30),
            _wujie_row('2002', 'PLAN-A', 40),
        ])

        counts = dict(zip(merged['宝贝ID'], merged[CURRENT_ACTIVE_PLAN_COUNT]))
        self.assertEqual(counts, {'1001': 2, '2002': 1})

    def test_daily_current_active_plan_count_uses_latest_stat_date(self):
        merged = _merge_wujie([
            _wujie_row('1001', 'PAUSED', 20, date='2026-09-03'),
            _wujie_row('1001', 'KEEP-A', 30, date='2026-09-04'),
            _wujie_row('1001', 'KEEP-B', 40, date='2026-09-04'),
        ])

        self.assertEqual(merged.loc[0, CURRENT_ACTIVE_PLAN_COUNT], 2)
        self.assertEqual(merged.loc[0, '推广计划总数'], 3)
        self.assertLess(
            merged.columns.get_loc(CURRENT_ACTIVE_PLAN_COUNT),
            merged.columns.get_loc('推广明细'),
        )

    def test_period_current_active_plan_count_uses_latest_day_not_window_union(self):
        source = pd.DataFrame([
            _day('1001', '2026-08-29', 'PAUSED', 'KEEP-A', 'KEEP-B'),
            _day('1001', '2026-09-04', 'KEEP-A', 'KEEP-B'),
        ])

        for period_days in (7, 15, 30):
            with self.subTest(period_days=period_days):
                period = aggregate_period_frame(
                    source,
                    period_days,
                    actual_days=period_days,
                    period_start='2026-08-29',
                    period_end='2026-09-04',
                ).iloc[0]
                self.assertEqual(period[CURRENT_ACTIVE_PLAN_COUNT], 2)
                self.assertEqual(period['推广计划总数'], 3)
                self.assertLess(
                    period.index.get_loc(CURRENT_ACTIVE_PLAN_COUNT),
                    period.index.get_loc('推广明细'),
                )

    def test_export_columns_place_current_active_count_before_promotion_detail(self):
        frame = pd.DataFrame([{
            '宝贝ID': '1001',
            CURRENT_ACTIVE_PLAN_COUNT: 2,
            '推广明细': '计划ID:PLAN-A',
        }])

        exported = execution_export_frame(frame)
        self.assertEqual(exported.loc[0, CURRENT_ACTIVE_PLAN_COUNT], 2)
        self.assertLess(
            EXPORT_COLS.index(CURRENT_ACTIVE_PLAN_COUNT),
            EXPORT_COLS.index('推广明细'),
        )


if __name__ == '__main__':
    unittest.main()
