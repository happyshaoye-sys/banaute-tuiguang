# -*- coding: utf-8 -*-
"""新品变化区（快照 + 差分）测试（v2.54）。

运营口径：
* 新增发现拆「真上新 / 历史补发现」；
* 消失＝上次在线 + 本次非在线/不再出现 + **连续 2 个自然日**；
* 恢复在线＝以前发现过、上次不在线、本次重新在线（不算全新款）；
* 判定结果（下架/继续观察）翻转不做变化列；
* 源缺失日不得算消失；回填历史不推进快照。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from core.new_product_change import (
    CHANGE_BACKFILL,
    CHANGE_BACK_ONLINE,
    CHANGE_GONE,
    CHANGE_NEW,
    CHANGE_WATCH,
    CHANGE_COLUMNS,
    GONE_CONFIRM_DAYS,
    STATE_ABSENT,
    STATE_OFFLINE,
    STATE_ONLINE,
    STATE_UNKNOWN,
    append_snapshot,
    build_online_status,
    build_snapshot,
    change_frame,
    diff_snapshot,
    load_snapshots,
    select_baseline,
    snapshot_path,
)


def _frame(rows):
    return pd.DataFrame(rows)


def _daily(*days):
    """days: (日期, [(宝贝ID, 货号, 商品状态), ...]) → product_daily。"""
    out = {}
    for day, items in days:
        out[day] = _frame([
            {'宝贝ID': pid, '货号': sku, '商品状态': status, '款式主图': f'http://img/{pid}.jpg',
             '淘宝类目': '连衣裙'}
            for pid, sku, status in items
        ])
    return out


def _lifecycle(rows):
    return _frame(rows)


def _first_seen(rows):
    return _frame(rows)


class OnlineStatusTests(unittest.TestCase):
    def test_states_cover_online_offline_absent(self):
        product_daily = _daily(
            ('2026-09-10', [('1', 'S1', '当前在线'), ('2', 'S2', '当前在线')]),
            ('2026-09-11', [('1', 'S1', '当前在线'), ('2', 'S2', '已下架')]),
        )
        status = build_online_status(product_daily, '2026-09-11')
        self.assertTrue(status['源可用'])
        self.assertEqual(status['款式']['S1']['本次状态'], STATE_ONLINE)
        self.assertEqual(status['款式']['S2']['本次状态'], STATE_OFFLINE)
        self.assertEqual(status['款式']['S2']['最后在线日'], '2026-09-10')
        # 本次没有出现过的款式（在更早的帧里）→ 不在源
        product_daily['2026-09-09'] = _frame([{'宝贝ID': '9', '货号': 'S9', '商品状态': '当前在线'}])
        status = build_online_status(product_daily, '2026-09-11')
        self.assertEqual(status['款式']['S9']['本次状态'], STATE_ABSENT)

    def test_missing_source_day_marks_unknown_and_blocks_judgement(self):
        product_daily = _daily(('2026-09-10', [('1', 'S1', '当前在线')]))
        status = build_online_status(product_daily, '2026-09-11')
        self.assertFalse(status['源可用'])
        self.assertEqual(status['款式']['S1']['本次状态'], STATE_UNKNOWN)

    def test_multi_link_style_is_online_when_any_link_online(self):
        product_daily = _daily(('2026-09-11', [
            ('1', 'S1', '已下架'),
            ('2', 'S1', '当前在线'),
        ]))
        status = build_online_status(product_daily, '2026-09-11')
        self.assertEqual(status['款式']['S1']['本次状态'], STATE_ONLINE)

    def test_sku_less_rows_fall_back_to_product_id(self):
        product_daily = _daily(('2026-09-11', [('777', '', '当前在线')]))
        status = build_online_status(product_daily, '2026-09-11')
        self.assertIn('777', status['款式'])


class DiffTests(unittest.TestCase):
    def _snapshot(self, analysis_date, styles, *, source_ok=True):
        return {
            '分析日': analysis_date,
            '源可用': source_ok,
            '源覆盖天数': 2,
            '款式': {
                key: {
                    '货号': value.get('货号', key),
                    '关联宝贝ID': value.get('关联宝贝ID', key),
                    '款式主图': value.get('款式主图', ''),
                    '淘宝类目': '连衣裙',
                    '首次在线发现日': value.get('首次在线发现日', '2026-08-01'),
                    '款式匹配可信度': '高',
                    '本次状态': value.get('本次状态', STATE_ONLINE),
                    '最后在线日': value.get('最后在线日', analysis_date),
                    '最后出现日': value.get('最后出现日', analysis_date),
                    '当前结果': value.get('当前结果', '继续观察'),
                    '当前商品状态': value.get('当前商品状态', '当前在线'),
                    '新品年龄': 40,
                }
                for key, value in styles.items()
            },
        }

    def test_first_run_reports_single_snapshot_without_zeros(self):
        current = self._snapshot('2026-09-11', {'S1': {}})
        result = diff_snapshot(None, current)
        self.assertEqual(result['状态'], '首个快照')
        self.assertEqual(result['rows'], [])
        self.assertIn('首个快照', result['说明'])

    def test_new_style_splits_true_new_from_backfill(self):
        previous = self._snapshot('2026-09-09', {'S1': {}})
        current = self._snapshot('2026-09-11', {
            'S1': {},
            'S2': {'首次在线发现日': '2026-09-10'},   # 晚于上次快照 → 真上新
            'S3': {'首次在线发现日': '2026-05-01'},   # 早于上次快照 → 补发现
        })
        result = diff_snapshot(previous, current)
        kinds = {row['款式键']: row['变化类型'] for row in result['rows']}
        self.assertEqual(kinds['S2'], CHANGE_NEW)
        self.assertEqual(kinds['S3'], CHANGE_BACKFILL)
        self.assertNotIn('S1', kinds)
        self.assertIn('补发现', [row['说明'] for row in result['rows'] if row['款式键'] == 'S3'][0])
        self.assertEqual(result['summary'][CHANGE_NEW], 1)
        self.assertEqual(result['summary'][CHANGE_BACKFILL], 1)

    def test_gone_requires_two_consecutive_days(self):
        previous = self._snapshot('2026-09-09', {'S1': {}})
        one_day = self._snapshot('2026-09-10', {
            'S1': {'本次状态': STATE_OFFLINE, '最后在线日': '2026-09-09'},
        })
        result = diff_snapshot(previous, one_day)
        self.assertEqual(result['rows'][0]['变化类型'], CHANGE_WATCH)
        self.assertEqual(result['rows'][0]['离线天数'], 1)

        two_days = self._snapshot('2026-09-11', {
            'S1': {'本次状态': STATE_OFFLINE, '最后在线日': '2026-09-09'},
        })
        result = diff_snapshot(previous, two_days)
        self.assertEqual(result['rows'][0]['变化类型'], CHANGE_GONE)
        self.assertEqual(result['rows'][0]['离线天数'], 2)
        self.assertGreaterEqual(result['rows'][0]['离线天数'], GONE_CONFIRM_DAYS)

    def test_absent_from_source_also_counts_as_gone(self):
        previous = self._snapshot('2026-09-09', {'S1': {}})
        current = self._snapshot('2026-09-11', {'S1': {'本次状态': STATE_ABSENT, '最后在线日': '2026-09-09'}})
        result = diff_snapshot(previous, current)
        self.assertEqual(result['rows'][0]['变化类型'], CHANGE_GONE)
        self.assertIn('不在源', result['rows'][0]['说明'])

    def test_back_online_is_not_a_new_style(self):
        previous = self._snapshot('2026-09-09', {
            'S1': {'本次状态': STATE_OFFLINE, '首次在线发现日': '2026-06-01', '最后在线日': '2026-06-20'},
        })
        current = self._snapshot('2026-09-11', {
            'S1': {'本次状态': STATE_ONLINE, '首次在线发现日': '2026-06-01', '最后在线日': '2026-09-11'},
        })
        result = diff_snapshot(previous, current)
        self.assertEqual(result['rows'][0]['变化类型'], CHANGE_BACK_ONLINE)
        self.assertEqual(result['summary'][CHANGE_NEW], 0)
        self.assertIn('以前发现过', result['rows'][0]['说明'])

    def test_missing_source_never_reports_gone(self):
        previous = self._snapshot('2026-09-09', {'S1': {}})
        current = self._snapshot('2026-09-11', {'S1': {'本次状态': STATE_UNKNOWN}}, source_ok=False)
        result = diff_snapshot(previous, current)
        self.assertEqual(result['状态'], '源缺失')
        self.assertEqual(result['rows'], [])
        self.assertEqual(result['summary'][CHANGE_GONE], 0)
        self.assertIn('缺失不能算消失', result['说明'])

    def test_result_flip_alone_is_not_a_change(self):
        previous = self._snapshot('2026-09-09', {'S1': {'当前结果': '继续观察'}})
        current = self._snapshot('2026-09-11', {'S1': {'当前结果': '下架'}})
        result = diff_snapshot(previous, current)
        self.assertEqual(result['rows'], [])
        self.assertEqual(result['summary']['无变化'], 1)

    def test_change_frame_keeps_fixed_columns_and_filters_watch(self):
        previous = self._snapshot('2026-09-09', {'S1': {}})
        current = self._snapshot('2026-09-10', {'S1': {'本次状态': STATE_OFFLINE, '最后在线日': '2026-09-09'}})
        rows = diff_snapshot(previous, current)['rows']
        frame = change_frame(rows)
        self.assertEqual(list(frame.columns), CHANGE_COLUMNS)
        self.assertEqual(len(frame), 1)
        self.assertTrue(change_frame(rows, include_watch=False).empty)


class SnapshotStoreTests(unittest.TestCase):
    def _snapshot(self, day, source_ok=True):
        return {
            '分析日': day,
            '源可用': source_ok,
            '源覆盖天数': 1,
            '款式': {'S1': {'货号': 'S1', '本次状态': STATE_ONLINE, '首次在线发现日': '2026-08-01'}},
        }

    def test_append_load_and_keep_window(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = snapshot_path(tmp, 2026, '集团店')
            for index in range(35):
                day = (pd.Timestamp('2026-07-01') + pd.Timedelta(days=index)).date().isoformat()
                result = append_snapshot(path, self._snapshot(day), keep=30)
                self.assertTrue(result['写入'])
            snapshots = load_snapshots(path)
            self.assertEqual(len(snapshots), 30)
            self.assertEqual(str(snapshots[-1]['分析日']), '2026-08-04')

    def test_same_day_rerun_overwrites_and_backfill_does_not_advance(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = snapshot_path(tmp, 2026, '女装店')
            append_snapshot(path, self._snapshot('2026-09-10'))
            again = append_snapshot(path, self._snapshot('2026-09-10'))
            self.assertTrue(again['写入'])
            self.assertIn('覆盖当天', again['原因'])
            backfill = append_snapshot(path, self._snapshot('2026-09-05'))
            self.assertFalse(backfill['写入'])
            self.assertTrue(backfill['历史回算'])
            self.assertEqual(len(load_snapshots(path)), 1)
            self.assertFalse(load_snapshots(path)[0]['源可用'] is None)

    def test_missing_source_snapshot_is_not_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = snapshot_path(tmp, 2026, '集团店')
            append_snapshot(path, self._snapshot('2026-09-10'))
            result = append_snapshot(path, self._snapshot('2026-09-11', source_ok=False))
            self.assertFalse(result['写入'])
            self.assertIn('源不可用', result['原因'])
            self.assertEqual(len(load_snapshots(path)), 1)

    def test_corrupt_snapshot_file_is_tolerated(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = snapshot_path(tmp, 2026, '集团店')
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'broken')
            self.assertEqual(load_snapshots(path), [])

    def test_select_baseline_default_and_days_back(self):
        snapshots = [self._snapshot(day) for day in ('2026-09-01', '2026-09-05', '2026-09-09')]
        self.assertEqual(select_baseline(snapshots, '2026-09-11')['分析日'], '2026-09-09')
        self.assertEqual(select_baseline(snapshots, '2026-09-11', days_back=7)['分析日'], '2026-09-01')
        self.assertEqual(select_baseline(snapshots, '2026-09-11', days_back=3)['分析日'], '2026-09-05')
        self.assertIsNone(select_baseline([], '2026-09-11'))


class BuildSnapshotTests(unittest.TestCase):
    def test_snapshot_merges_index_result_and_online_state(self):
        product_daily = _daily(
            ('2026-09-10', [('1', 'S1', '当前在线'), ('2', 'S2', '当前在线')]),
            ('2026-09-11', [('1', 'S1', '当前在线'), ('2', 'S2', '已下架')]),
        )
        online = build_online_status(product_daily, '2026-09-11')
        first_seen = _first_seen([
            {'style_key': 'S1', '首次在线发现日': '2026-08-01', '首次发现宝贝ID': '1',
             '款式匹配可信度': '高'},
        ])
        lifecycle = _lifecycle([
            {'货号': 'S1', '关联宝贝ID': '1', '款式主图': 'http://img/1.jpg', '淘宝类目': '连衣裙',
             '新品年龄': 42, '当前结果': '继续观察', '当前商品状态': '当前在线'},
            {'货号': 'S2', '关联宝贝ID': '2', '款式主图': 'http://img/2.jpg', '淘宝类目': '连衣裙',
             '新品年龄': 20, '当前结果': '下架', '当前商品状态': '已下架'},
        ])
        snapshot = build_snapshot(lifecycle, first_seen, online, '2026-09-11')
        self.assertEqual(snapshot['分析日'], '2026-09-11')
        self.assertTrue(snapshot['源可用'])
        self.assertEqual(snapshot['款式']['S1']['本次状态'], STATE_ONLINE)
        self.assertEqual(snapshot['款式']['S2']['本次状态'], STATE_OFFLINE)
        self.assertEqual(snapshot['款式']['S2']['当前结果'], '下架')
        self.assertEqual(snapshot['款式']['S2']['最后在线日'], '2026-09-10')


if __name__ == '__main__':  # pragma: no cover
    unittest.main()
