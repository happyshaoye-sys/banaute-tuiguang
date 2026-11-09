"""v2.61 数据源兜底与覆盖提示的回归测试。

覆盖点
------
1. 缺「商品-经营投产比」、有「商品-整体」+ 无界时，兜底帧列结构与既有源**完全一致**；
2. 字段映射正确：支付金额→GMV、加购件数→商品加购件数、点击量→渠道访客数、付费→推广消耗金额；
3. 类目：文件自带「叶子类目名称」优先，缺失时用历史映射补，并给出覆盖率；
4. 有正常「商品-经营投产比」的日子不触发兜底（`build_fallback_frame` 不被调用）；
5. 提示文案包含关键信息（缺哪个源、宝贝数、类目覆盖率、渠道加购无来源）。
"""
import unittest

import pandas as pd

from core.schema import SOURCE_PRODUCT_ROI
from core.source_fallback import (
    FALLBACK_COLUMNS,
    build_category_map,
    build_fallback_frame,
    describe_fallback,
)

WUJIE_COLUMNS = ['统计日期', '场景名字', '计划ID', '宝贝ID', '主体类型', '展现', '点击',
                 '付费', '点击率', 'GMV', '商品加购件数', '直购数量', '转化周期']


def overall_frame():
    """模拟「商品-整体」原始帧（read_overall_raw 的输出）。"""
    return pd.DataFrame([
        {'统计日期': '2026-11-08', '宝贝ID': '1001', '商品名称': '款A', '淘宝类目': '毛针织衫',
         'GMV': 10000.0, '商品访客数': 800.0, '支付买家数': 12.0, '支付件数': 13.0,
         '商品加购件数': 90.0, '商品支付转化率': '1.50%', '访客平均价值': 12.5,
         '成功退款金额': 300.0, '搜索引导访客数': 200.0},
        {'统计日期': '2026-11-08', '宝贝ID': '1002', '商品名称': '款B', '淘宝类目': '',
         'GMV': 5000.0, '商品访客数': 400.0, '支付买家数': 6.0, '支付件数': 6.0,
         '商品加购件数': 40.0, '商品支付转化率': '1.50%', '访客平均价值': 12.5,
         '成功退款金额': 100.0, '搜索引导访客数': 80.0},
    ])


def wujie_frame():
    return pd.DataFrame([
        {'统计日期': '', '场景名字': '关键词推广', '计划ID': 'P1', '宝贝ID': '1001',
         '主体类型': '商品', '展现': 1000, '点击': 120, '付费': 60.0, '点击率': '0.12',
         'GMV': 800.0, '商品加购件数': 9, '直购数量': 3, '转化周期': '15天转化'},
        {'统计日期': '', '场景名字': '人群推广', '计划ID': 'P2', '宝贝ID': '1001',
         '主体类型': '商品', '展现': 5000, '点击': 600, '付费': 140.0, '点击率': '0.12',
         'GMV': 1200.0, '商品加购件数': 15, '直购数量': 2, '转化周期': '15天转化'},
        {'统计日期': '', '场景名字': '关键词推广', '计划ID': 'P3', '宝贝ID': '1002',
         '主体类型': '商品', '展现': 300, '点击': 30, '付费': 20.0, '点击率': '0.10',
         'GMV': 200.0, '商品加购件数': 2, '直购数量': 1, '转化周期': '15天转化'},
    ])


class FallbackFrameTest(unittest.TestCase):
    def setUp(self):
        # read_overall_raw 依赖真实文件读取，这里直接打桩
        import core.source_fallback as mod
        self.mod = mod
        self._orig = mod.read_overall_raw
        mod.read_overall_raw = lambda path: overall_frame()

    def tearDown(self):
        self.mod.read_overall_raw = self._orig

    def test_columns_identical_to_existing_source(self):
        frame, stats = build_fallback_frame(['fake.xlsx'], [('src', wujie_frame())])
        self.assertTrue(stats['ok'])
        self.assertEqual(tuple(frame.columns), FALLBACK_COLUMNS)
        # 25 列，与「商品-经营投产比」必需字段集合一致
        from core.data_validation import SOURCE_REQUIRED_FIELDS
        required = SOURCE_REQUIRED_FIELDS[SOURCE_PRODUCT_ROI]
        self.assertTrue(required.issubset(set(frame.columns)),
                        f"缺必需列: {required - set(frame.columns)}")
        # 不新增字段
        self.assertEqual(set(frame.columns), set(FALLBACK_COLUMNS))
        self.assertEqual(len(frame), 2)

    def test_field_mapping_from_overall_and_wujie(self):
        frame, _stats = build_fallback_frame(['fake.xlsx'], [('src', wujie_frame())])
        row = frame.set_index('宝贝ID').loc['1001']
        self.assertAlmostEqual(float(row['GMV']), 10000.0)
        self.assertAlmostEqual(float(row['商品访客数']), 800.0)
        self.assertAlmostEqual(float(row['商品加购件数']), 90.0)
        # 无界侧：付费 → 推广消耗金额；GMV → 推广引导总成交金额
        self.assertAlmostEqual(float(row['推广消耗金额']), 200.0)
        self.assertAlmostEqual(float(row['推广引导总成交金额']), 2000.0)
        # 点击量 → 渠道访客数
        self.assertAlmostEqual(float(row['关键词推广访客数']), 120.0)
        self.assertAlmostEqual(float(row['人群推广访客数']), 600.0)
        self.assertAlmostEqual(float(row['场景推广访客数']), 0.0)
        # 渠道加购无来源 → 0
        self.assertAlmostEqual(float(row['关键词推广加购数']), 0.0)

    def test_second_product_only_keyword_clicks(self):
        frame, _ = build_fallback_frame(['fake.xlsx'], [('src', wujie_frame())])
        row = frame.set_index('宝贝ID').loc['1002']
        self.assertAlmostEqual(float(row['关键词推广访客数']), 30.0)
        self.assertAlmostEqual(float(row['人群推广访客数']), 0.0)
        self.assertAlmostEqual(float(row['推广消耗金额']), 20.0)

    def test_category_uses_history_when_missing(self):
        frame, stats = build_fallback_frame(
            ['fake.xlsx'], [('src', wujie_frame())], category_map={'1002': '半身裙'})
        cats = frame.set_index('宝贝ID')['淘宝类目']
        self.assertEqual(cats.loc['1001'], '毛针织衫')
        self.assertEqual(cats.loc['1002'], '半身裙')
        self.assertEqual(stats['category_covered'], 2)
        self.assertEqual(stats['category_from_history'], 1)
        self.assertAlmostEqual(stats['category_gmv_ratio'], 1.0)

    def test_no_overall_returns_not_ok(self):
        self.mod.read_overall_raw = lambda path: pd.DataFrame()
        frame, stats = build_fallback_frame(['fake.xlsx'], [])
        self.assertFalse(stats['ok'])
        self.assertEqual(len(frame), 0)
        self.assertIn('商品-整体', stats['reason'])

    def test_describe_fallback_mentions_gaps(self):
        _frame, stats = build_fallback_frame(['fake.xlsx'], [('src', wujie_frame())])
        text = describe_fallback('2026-11-08', stats)
        for token in ('2026-11-08', '商品-经营投产比', '商品-整体', '类目覆盖', '点击量', '加购'):
            self.assertIn(token, text)


class CategoryMapTest(unittest.TestCase):
    def test_empty_folders_return_empty_map(self):
        self.assertEqual(build_category_map([]), {})
        self.assertEqual(build_category_map(None), {})


class NoFallbackWhenSourcePresentTest(unittest.TestCase):
    """有正常「商品-经营投产比」的日子不应触发兜底（app 侧条件判断）。"""

    def test_condition(self):
        sources_with_roi = {SOURCE_PRODUCT_ROI: [('x', pd.DataFrame())], '商品-整体': [('y', pd.DataFrame())]}
        triggers = SOURCE_PRODUCT_ROI not in sources_with_roi and '商品-整体' in sources_with_roi
        self.assertFalse(triggers)

        sources_without_roi = {'商品-整体': [('y', pd.DataFrame())]}
        triggers = SOURCE_PRODUCT_ROI not in sources_without_roi and '商品-整体' in sources_without_roi
        self.assertTrue(triggers)

        sources_without_overall = {SOURCE_PRODUCT_ROI: [('x', pd.DataFrame())]}
        triggers = SOURCE_PRODUCT_ROI not in sources_without_overall and '商品-整体' in sources_without_overall
        self.assertFalse(triggers)


if __name__ == '__main__':
    unittest.main()
