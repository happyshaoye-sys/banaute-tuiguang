"""推广计划管理「预算 / 出价方式」回归测试（2026-11-14 新增）。

需求口径
--------
* 来源：无界目录下最新的 `计划列表_*.xlsx`（`模块/计划名称/计划ID/预算/出价方式/计划组/状态/bizCode`）；
* **只读最新日期**那份快照，不回读分析日当天那一份（预算/出价是当前配置，不是历史值）；
* 按计划ID左连接到「推广计划管理」，插在 `计划经营结构` 之后；匹配不到留空、不推断；
* 页面与导出（全部 Sheet 的『推广计划管理』sheet）列顺序一致；
* 只做展示，不参与任何指标或预算决策计算。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from core.budget_decision import build_budget_decision_context, build_plan_management
from core.exporter import BUDGET_DECISION_PLAN_COLUMNS
from core.plan_config import (
    budget_caption,
    find_latest_plan_list,
    format_budget_cell,
    load_plan_config,
    parse_budget,
    plan_config_caption,
    read_plan_list,
    summarize_budget,
)
from core.plan_metrics import FULL_PLAN_DETAIL_COLUMN


def _write_plan_list(path: Path, rows):
    frame = pd.DataFrame(rows, columns=['模块', '计划名称', '计划ID', '预算', '出价方式', '计划组', '状态', 'bizCode'])
    frame.to_excel(path, index=False)


def _plan_rows():
    return [
        ['人群推广', '灰色套装_人群', '82843151879', '310元', '稳定投产比 投产比6', '', '投放中', 'onebpCrowd'],
        ['关键词推广', '针织短袖_关键词', '82938652650', '90元', '平均点击成本 0.3元', 'CS_KW', '投放中', 'onebpSearch'],
        ['货品全站推广', '不限预算计划', '81388538644', '不限', '最大化拿量', '', '已暂停', 'onebpSite'],
    ]


class ParseBudgetTests(unittest.TestCase):
    def test_common_forms(self):
        self.assertEqual(parse_budget('310元'), 310)
        self.assertEqual(parse_budget('90元'), 90)
        self.assertEqual(parse_budget('1,200元'), 1200)
        self.assertEqual(parse_budget('0.5元'), 0.5)
        self.assertEqual(parse_budget('不限'), '不限')
        self.assertEqual(parse_budget(''), '')
        self.assertEqual(parse_budget('nan'), '')
        self.assertEqual(parse_budget(None), '')


class FormatBudgetCellTests(unittest.TestCase):
    """预算列展示：整列必须同类型（否则 Streamlit 抛 ArrowInvalid）。"""

    def test_numbers_become_integer_strings(self):
        self.assertEqual(format_budget_cell(310), '310')
        self.assertEqual(format_budget_cell(310.0), '310')
        self.assertEqual(format_budget_cell(0.5), '0.5')

    def test_text_and_empty(self):
        self.assertEqual(format_budget_cell('不限'), '不限')
        self.assertEqual(format_budget_cell(''), '')
        self.assertEqual(format_budget_cell(None), '')
        self.assertEqual(format_budget_cell(float('nan')), '')

    def test_mixed_column_is_all_str(self):
        values = [310, '不限', '', None]
        formatted = [format_budget_cell(value) for value in values]
        self.assertTrue(all(isinstance(item, str) for item in formatted))
        self.assertEqual(formatted, ['310', '不限', '', ''])


class SummarizeBudgetTests(unittest.TestCase):
    """摘要「当日预算」= 计划列表里 状态=投放中 的计划日预算合计。"""

    def _config(self, rows):
        by_id = {}
        for plan_id, budget, status, module in rows:
            by_id[plan_id] = {'预算': budget, '出价方式': '', '状态': status, '模块': module}
        return {'by_id': by_id, 'source': {'file': '计划列表_x.xlsx', 'exported_at': '2026-11-14 13:54'}}

    def test_only_running_plans_counted(self):
        config = self._config([
            ('1', 500, '投放中', '关键词推广'),
            ('2', 800, '投放中', '人群推广'),
            ('3', 999, '已暂停', '人群推广'),
            ('4', 111, '已终止', '内容营销-短视频'),
        ])
        summary = summarize_budget(config)
        self.assertEqual(summary['金额'], 1300.0)
        self.assertEqual(summary['计划数'], 2)
        self.assertEqual(summary['按模块'], {'人群推广': 800.0, '关键词推广': 500.0})

    def test_unlimited_budget_counted_but_not_summed(self):
        config = self._config([
            ('1', 500, '投放中', '关键词推广'),
            ('2', '不限', '投放中', '货品全站推广'),
        ])
        summary = summarize_budget(config)
        self.assertEqual(summary['金额'], 500.0)
        self.assertEqual(summary['计划数'], 1)
        self.assertEqual(summary['含不限数'], 1)
        self.assertIn('无法求和', budget_caption(summary))

    def test_missing_status_is_not_counted(self):
        config = {'by_id': {'1': {'预算': 500, '出价方式': ''}}, 'source': {}}
        summary = summarize_budget(config)
        self.assertEqual(summary['金额'], 0.0)
        self.assertEqual(summary['计划数'], 0)

    def test_empty_config_returns_none_not_zero(self):
        summary = summarize_budget({})
        self.assertIsNone(summary['金额'])
        self.assertIn('不可用', budget_caption(summary))

    def test_caption_mentions_snapshot_and_excludes(self):
        config = self._config([('1', 500, '投放中', '关键词推广')])
        text = budget_caption(summarize_budget(config))
        self.assertIn('状态=投放中', text)
        self.assertIn('已暂停/已终止不计入', text)
        self.assertIn('计划列表_x.xlsx', text)

    def test_summary_carries_budget_fields(self):
        detail = json.dumps([
            {'计划ID': '82843151879', '计划名称': '计划A', '所属渠道': '人群',
             '当前花费': 300.0, '归因成交': 900.0},
        ], ensure_ascii=False)
        source = pd.DataFrame({FULL_PLAN_DETAIL_COLUMN: [detail], '宝贝ID': ['A1']})
        context = build_budget_decision_context(source, plan_config=self._config([
            ('82843151879', 500, '投放中', '人群推广'),
        ]))
        self.assertEqual(context['summary']['当日预算'], 500.0)
        self.assertEqual(context['summary']['当日预算计划数'], 1)

    def test_summary_budget_none_without_plan_config(self):
        detail = json.dumps([
            {'计划ID': '82843151879', '计划名称': '计划A', '所属渠道': '人群',
             '当前花费': 300.0, '归因成交': 900.0},
        ], ensure_ascii=False)
        source = pd.DataFrame({FULL_PLAN_DETAIL_COLUMN: [detail], '宝贝ID': ['A1']})
        context = build_budget_decision_context(source)
        self.assertIsNone(context['summary']['当日预算'])


class FindLatestPlanListTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='metox_planlist_'))

    def tearDown(self):
        for path in sorted(self.root.rglob('*'), reverse=True):
            path.unlink() if path.is_file() else path.rmdir()
        self.root.rmdir()

    def _folder(self, day):
        folder = self.root / day
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def test_picks_newest_folder_and_newest_file(self):
        self._folder('2026-11-10')
        newer = self._folder('2026-11-13')
        older_file = newer / '计划列表_2026-11-13T01-00-00.xlsx'
        latest_file = newer / '计划列表_2026-11-14T05-54-00.xlsx'
        _write_plan_list(older_file, _plan_rows())
        _write_plan_list(latest_file, _plan_rows())
        os.utime(older_file, (1_700_000_000, 1_700_000_000))
        os.utime(latest_file, (1_800_000_000, 1_800_000_000))
        path, folder = find_latest_plan_list(self.root, analysis_date='2026-11-13')
        self.assertEqual(folder, '2026-11-13')
        self.assertEqual(path.name, '计划列表_2026-11-14T05-54-00.xlsx')

    def test_future_folder_allowed_for_history_date(self):
        """查历史日期时也能拿到最新那份（预算/出价是当前配置）。"""
        self._folder('2026-10-20')
        newer = self._folder('2026-11-13')
        _write_plan_list(newer / '计划列表_2026-11-14T05-54-00.xlsx', _plan_rows())
        path, folder = find_latest_plan_list(self.root, analysis_date='2026-10-20')
        self.assertIsNotNone(path)
        self.assertEqual(folder, '2026-11-13')

    def test_lookback_window_excludes_too_old_folders(self):
        old = self._folder('2026-10-01')
        _write_plan_list(old / '计划列表_2026-10-01T09-00-00.xlsx', _plan_rows())
        path, _ = find_latest_plan_list(self.root, analysis_date='2026-11-13', lookback_days=7)
        self.assertIsNone(path)

    def test_ignores_other_files_and_week_folders(self):
        folder = self._folder('2026-11-13')
        _write_plan_list(folder / '计划列表_2026-11-14T05-54-00.xlsx', _plan_rows())
        (folder / '商品总表_20261114_090919.zip').write_bytes(b'x')
        (folder / '~$计划列表_临时.xlsx').write_bytes(b'x')
        week = self._folder('week-2026-11-09_to_2026-11-15')
        _write_plan_list(week / '计划列表_2026-11-14T05-54-00.xlsx', _plan_rows())
        path, folder_date = find_latest_plan_list(self.root, analysis_date='2026-11-13')
        self.assertEqual(folder_date, '2026-11-13')
        self.assertEqual(path.parent.name, '2026-11-13')

    def test_missing_root_is_safe(self):
        path, folder = find_latest_plan_list(self.root / '不存在', analysis_date='2026-11-13')
        self.assertIsNone(path)
        self.assertEqual(folder, '')


class ReadPlanListTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='metox_planread_'))
        self.path = self.root / '计划列表_2026-11-14T05-54-00.xlsx'
        _write_plan_list(self.path, _plan_rows())

    def tearDown(self):
        for path in self.root.rglob('*'):
            path.unlink()
        self.root.rmdir()

    def test_reads_by_plan_id(self):
        by_id, meta = read_plan_list(self.path)
        self.assertEqual(len(by_id), 3)
        self.assertEqual(
            {key: by_id['82843151879'][key] for key in ('预算', '出价方式')},
            {'预算': 310, '出价方式': '稳定投产比 投产比6'},
        )
        self.assertEqual(by_id['82843151879']['状态'], '投放中')
        self.assertEqual(by_id['82843151879']['模块'], '人群推广')
        self.assertEqual(
            {key: by_id['81388538644'][key] for key in ('预算', '出价方式', '状态', '模块')},
            {'预算': '不限', '出价方式': '最大化拿量', '状态': '已暂停', '模块': '货品全站推广'},
        )
        self.assertEqual(meta['rows'], 3)
        self.assertEqual(meta['file'], self.path.name)

    def test_missing_columns_reported(self):
        bad = self.root / '计划列表_坏.xlsx'
        pd.DataFrame({'模块': ['人群推广']}).to_excel(bad, index=False)
        by_id, meta = read_plan_list(bad)
        self.assertEqual(by_id, {})
        self.assertIn('缺少列', meta['reason'])


class LoadPlanConfigCacheTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='metox_plancfg_'))
        self.wujie = self.root / '无界'
        folder = self.wujie / '2026-11-13'
        folder.mkdir(parents=True)
        self.file = folder / '计划列表_2026-11-14T05-54-00.xlsx'
        _write_plan_list(self.file, _plan_rows())
        self.cache = self.root / 'cache'

    def tearDown(self):
        for path in sorted(self.root.rglob('*'), reverse=True):
            path.unlink() if path.is_file() else path.rmdir()
        self.root.rmdir()

    def test_cache_hit_then_invalidate_on_file_change(self):
        first = load_plan_config(self.wujie, analysis_date='2026-11-13', cache_root=self.cache)
        self.assertEqual(len(first['by_id']), 3)
        self.assertFalse(first['source']['from_cache'])
        second = load_plan_config(self.wujie, analysis_date='2026-11-13', cache_root=self.cache)
        self.assertTrue(second['source']['from_cache'])

        # 文件变了（内容+大小+mtime 都变）→ 重新读取
        rows = _plan_rows()
        rows.append(['人群推广', '新计划', '80000000001', '500元', '最大化拿量', '', '投放中', 'x'])
        _write_plan_list(self.file, rows)
        os.utime(self.file, (1_900_000_000, 1_900_000_000))
        third = load_plan_config(self.wujie, analysis_date='2026-11-13', cache_root=self.cache)
        self.assertFalse(third['source']['from_cache'])
        self.assertEqual(len(third['by_id']), 4)
        self.assertIn('80000000001', third['by_id'])

    def test_missing_file_returns_empty_config(self):
        empty = load_plan_config(self.root / '空目录', analysis_date='2026-11-13')
        self.assertEqual(empty['by_id'], {})
        self.assertIn('未找到', plan_config_caption(empty['source']))


class PlanManagementJoinTests(unittest.TestCase):
    def _source(self):
        details = [
            {'计划ID': '82843151879', '计划名称': '灰色套装_人群', '所属渠道': '人群', '当前花费': 100.0,
             '归因成交': 500.0},
            {'计划ID': '82938652650', '计划名称': '针织短袖_关键词', '所属渠道': '关键词', '当前花费': 50.0,
             '归因成交': 400.0},
            {'计划ID': '89999999999', '计划名称': '列表里没有的计划', '所属渠道': '全站', '当前花费': 20.0,
             '归因成交': 0.0},
        ]
        return pd.DataFrame({
            FULL_PLAN_DETAIL_COLUMN: [json.dumps(details, ensure_ascii=False)],
            '宝贝ID': ['A1'],
        })

    def _plan_config(self):
        return {
            'by_id': {
                '82843151879': {'预算': 310, '出价方式': '稳定投产比 投产比6'},
                '82938652650': {'预算': 90, '出价方式': '平均点击成本 0.3元'},
            },
            'fingerprint': 'test',
        }

    def test_columns_follow_plan_structure(self):
        rows = build_plan_management(self._source(), products=[
            {'宝贝ID': 'A1', '商品周期': '成长期', '商品定位': '主推',
             '_plan_detail_values': [self._source()[FULL_PLAN_DETAIL_COLUMN][0]]},
        ], plan_config=self._plan_config())
        self.assertTrue(rows)
        for row in rows:
            columns = list(row)
            self.assertIn('计划经营结构', columns)
            self.assertEqual(columns[columns.index('计划经营结构') + 1], '预算')
            self.assertEqual(columns[columns.index('计划经营结构') + 2], '出价方式')

    def test_join_values_and_missing_left_blank(self):
        detail = self._source()[FULL_PLAN_DETAIL_COLUMN][0]
        rows = build_plan_management(self._source(), products=[
            {'宝贝ID': 'A1', '商品周期': '成长期', '商品定位': '主推', '_plan_detail_values': [detail]},
        ], plan_config=self._plan_config())
        by_id = {row['计划ID']: row for row in rows}
        self.assertEqual(by_id['82843151879']['预算'], 310)
        self.assertEqual(by_id['82843151879']['出价方式'], '稳定投产比 投产比6')
        self.assertEqual(by_id['82938652650']['预算'], 90)
        # 计划列表里没有的计划：留空，不推断
        self.assertEqual(by_id['89999999999']['预算'], '')
        self.assertEqual(by_id['89999999999']['出价方式'], '')

    def test_context_passes_plan_config_through(self):
        detail = self._source()[FULL_PLAN_DETAIL_COLUMN][0]
        source = self._source()
        context = build_budget_decision_context(source, plan_config=self._plan_config())
        plans = context.get('plans') or []
        self.assertTrue(plans)
        budgets = {row['计划ID']: row['预算'] for row in plans}
        self.assertEqual(budgets.get('82843151879'), 310)
        self.assertIn('出价方式', plans[0])

    def test_no_plan_config_keeps_columns_empty(self):
        detail = self._source()[FULL_PLAN_DETAIL_COLUMN][0]
        rows = build_plan_management(self._source(), products=[
            {'宝贝ID': 'A1', '商品周期': '成长期', '商品定位': '主推', '_plan_detail_values': [detail]},
        ])
        self.assertTrue(all(row['预算'] == '' and row['出价方式'] == '' for row in rows))

    def test_export_column_order_matches_page(self):
        columns = list(BUDGET_DECISION_PLAN_COLUMNS)
        self.assertEqual(columns[columns.index('计划经营结构') + 1], '预算')
        self.assertEqual(columns[columns.index('计划经营结构') + 2], '出价方式')
        self.assertLess(columns.index('出价方式'), columns.index('周期花费'))
        # 2026-11-14：日均花费紧跟 ROI
        self.assertEqual(columns[columns.index('ROI') + 1], '日均花费')
        self.assertLess(columns.index('日均花费'), columns.index('方向证据'))


class PlanDailySpendTests(unittest.TestCase):
    """日均花费 = 所选周期花费 ÷ 所选周期天数。"""

    DETAIL = json.dumps([
        {'计划ID': '82843151879', '计划名称': '计划A', '所属渠道': '人群',
         '当前花费': 300.0, '归因成交': 900.0},
    ], ensure_ascii=False)

    def _source(self, days=None):
        frame = pd.DataFrame({FULL_PLAN_DETAIL_COLUMN: [self.DETAIL], '宝贝ID': ['A1']})
        if days is not None:
            frame['计划实际数据天数'] = [days]
        return frame

    def _rows(self, days=None, period_days=None):
        return build_plan_management(
            self._source(days),
            products=[{'宝贝ID': 'A1', '商品周期': '成长期', '商品定位': '主推',
                       '_plan_detail_values': [self.DETAIL]}],
            period_days=period_days,
        )

    def test_daily_spend_uses_period_days(self):
        rows = self._rows(days=30)
        self.assertEqual(rows[0]['周期花费'], 300.0)
        self.assertEqual(rows[0]['日均花费'], 10.0)

    def test_single_day_period_equals_period_spend(self):
        rows = self._rows(days=1)
        self.assertEqual(rows[0]['日均花费'], 300.0)

    def test_explicit_period_days_wins(self):
        rows = self._rows(days=30, period_days=15)
        self.assertEqual(rows[0]['日均花费'], 20.0)

    def test_missing_days_column_falls_back_to_one_day(self):
        rows = self._rows(days=None)
        self.assertEqual(rows[0]['日均花费'], 300.0)

    def test_zero_days_does_not_divide_by_zero(self):
        rows = self._rows(days=0)
        self.assertEqual(rows[0]['日均花费'], 300.0)

    def test_column_sits_right_after_roi(self):
        rows = self._rows(days=7)
        columns = list(rows[0])
        self.assertEqual(columns[columns.index('ROI') + 1], '日均花费')


if __name__ == '__main__':
    unittest.main()
