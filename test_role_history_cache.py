# -*- coding: utf-8 -*-
"""角色层历史轻量缓存测试（v2.54）。

验收原则：
* 轻量 GMV 缓存入口与 ``day_map`` 入口**逐位一致**（同口径单点抽取）；
* 缓存文件缺失/损坏不得阻断主流程；
* KPI 缓存只补齐缺失日期，不覆盖本次已算出的天。
"""
from __future__ import annotations

import pickle
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from core.budget_role import (
    compute_coverage_thresholds_from_day_map,
    compute_coverage_thresholds_from_gmv_daily,
    gmv_by_product,
    gmv_daily_from_day_map,
)
from core.role_history_cache import (
    ROLE_HISTORY_KEEP_DAYS,
    gmv_daily_coverage,
    gmv_daily_path,
    kpi_daily_path,
    load_gmv_daily,
    load_kpi_daily,
    merge_kpi_trend,
    update_gmv_daily,
    update_kpi_daily,
)

END = '2026-09-09'


def _day_map(days: int = 56, values=(600.0, 200.0, 100.0, 40.0), end: str = END):
    """构造 56 天 day_map：每个宝贝在每天贡献 GMV/7（使窗口日均恒等于 values）。"""
    end_date = date.fromisoformat(end)
    day_map = {}
    for offset in range(days):
        day = (end_date - timedelta(days=offset)).isoformat()
        rows = [
            {'宝贝ID': f'P{index}', 'GMV': value / 7.0 * 7.0, '转化周期': '15天转化'}
            for index, value in enumerate(values)
        ]
        rows.append({'宝贝ID': 'P0', 'GMV': 999.0, '转化周期': '1天转化'})
        day_map[day] = (pd.DataFrame(rows),)
    return day_map


class GmvDailyRouteTests(unittest.TestCase):
    def test_light_route_matches_day_map_route_bit_for_bit(self):
        day_map = _day_map()
        from_map = compute_coverage_thresholds_from_day_map(day_map, end_date=END)
        from_light = compute_coverage_thresholds_from_gmv_daily(
            gmv_daily_from_day_map(day_map), end_date=END,
        )
        self.assertEqual(from_light.th_main, from_map.th_main)
        self.assertEqual(from_light.th_mid, from_map.th_mid)
        self.assertEqual(repr(from_light.th_main), repr(from_map.th_main))
        self.assertEqual(repr(from_light.th_mid), repr(from_map.th_mid))
        self.assertEqual(from_light.version, from_map.version)
        self.assertEqual(from_light.weeks, 8)

    def test_gmv_by_product_filters_trading_period_and_normalizes_ids(self):
        frame = pd.DataFrame([
            {'宝贝ID': '123.0', 'GMV': 10.0, '转化周期': '15天转化'},
            {'宝贝ID': '123', 'GMV': 5.0, '转化周期': '15天转化'},
            {'宝贝ID': '123', 'GMV': 100.0, '转化周期': '1天转化'},
            {'宝贝ID': '456', 'GMV': 0.0, '转化周期': '15天转化'},
        ])
        self.assertEqual(gmv_by_product(frame), {'123': 15.0, '456': 0.0})
        self.assertEqual(gmv_by_product(pd.DataFrame()), {})
        self.assertEqual(gmv_by_product(None), {})

    def test_gmv_daily_from_day_map_accepts_subset_dates(self):
        day_map = _day_map(days=5)
        subset = gmv_daily_from_day_map(day_map, dates=[END])
        self.assertEqual(list(subset), [END])


class GmvDailyStoreTests(unittest.TestCase):
    def test_update_load_roundtrip_and_prune(self):
        with tempfile.TemporaryDirectory() as tmp:
            day_map = _day_map(days=80)
            stats = update_gmv_daily(tmp, 2026, '集团店', day_map, keep_days=ROLE_HISTORY_KEEP_DAYS)
            self.assertEqual(stats['本次新增/更新天数'], 80)
            self.assertEqual(stats['缓存总天数'], ROLE_HISTORY_KEEP_DAYS)
            payload = load_gmv_daily(tmp, 2026, '集团店')
            self.assertEqual(len(payload), ROLE_HISTORY_KEEP_DAYS)
            self.assertEqual(max(payload), END)

    def test_update_merges_instead_of_replacing(self):
        with tempfile.TemporaryDirectory() as tmp:
            update_gmv_daily(tmp, 2026, '集团店', _day_map(days=3))
            stats = update_gmv_daily(tmp, 2026, '集团店', _day_map(days=3))
            self.assertEqual(stats['缓存总天数'], 3)
            self.assertEqual(sorted(load_gmv_daily(tmp, 2026, '集团店')), sorted(_day_map(days=3)))

    def test_load_tolerates_missing_and_corrupt_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(load_gmv_daily(tmp, 2026, '女装店'), {})
            path = gmv_daily_path(tmp, 2026, '女装店')
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'not a pickle')
            self.assertEqual(load_gmv_daily(tmp, 2026, '女装店'), {})
            path.write_bytes(pickle.dumps(['wrong shape']))
            self.assertEqual(load_gmv_daily(tmp, 2026, '女装店'), {})

    def test_coverage_reports_missing_days(self):
        full = gmv_daily_from_day_map(_day_map())
        coverage = gmv_daily_coverage(full, END)
        self.assertTrue(coverage['可用'])
        self.assertEqual(coverage['缺失天数'], [])
        self.assertEqual(coverage['窗口数'], 8)
        self.assertEqual(coverage['需要天数'], 56)

        thin = {key: value for key, value in full.items() if key != END}
        coverage_thin = gmv_daily_coverage(thin, END)
        self.assertFalse(coverage_thin['可用'])
        self.assertEqual(coverage_thin['缺失天数'], [END])


class KpiDailyCacheTests(unittest.TestCase):
    def _frame(self, days):
        return pd.DataFrame([
            {'日期': day, 'GMV': float(index), '付费': float(index) * 2}
            for index, day in enumerate(days)
        ])

    def test_update_and_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            stats = update_kpi_daily(tmp, 2026, '集团店', self._frame(['2026-09-08', '2026-09-09']))
            self.assertEqual(stats['写入天数'], 2)
            payload = load_kpi_daily(tmp, 2026, '集团店')
            self.assertEqual(sorted(payload), ['2026-09-08', '2026-09-09'])
            self.assertEqual(payload['2026-09-09']['GMV'], 1.0)
            self.assertTrue(kpi_daily_path(tmp, 2026, '集团店').exists())

    def test_merge_fills_missing_days_and_keeps_loaded_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            update_kpi_daily(tmp, 2026, '集团店', self._frame(['2026-07-20', '2026-08-01']))
            current = self._frame(['2026-09-08', '2026-09-09'])
            current.loc[current['日期'] == '2026-09-08', 'GMV'] = 77.0
            merged = merge_kpi_trend(current, tmp, 2026, '集团店')
            self.assertEqual(list(merged['日期']), ['2026-07-20', '2026-08-01', '2026-09-08', '2026-09-09'])
            self.assertEqual(float(merged.loc[merged['日期'] == '2026-09-08', 'GMV'].iloc[0]), 77.0)
            # 缓存里的天补进来时，本帧没有的指标留空而不是补 0
            self.assertTrue(pd.isna(merged.loc[merged['日期'] == '2026-07-20', 'GMV'].iloc[0]) is False)

    def test_merge_without_cache_returns_original_frame(self):
        with tempfile.TemporaryDirectory() as tmp:
            frame = self._frame(['2026-09-09'])
            merged = merge_kpi_trend(frame, tmp, 2026, '女装店')
            self.assertEqual(list(merged['日期']), ['2026-09-09'])


if __name__ == '__main__':  # pragma: no cover
    unittest.main()
